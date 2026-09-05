"""Builds the live retailer list: feed-backed shops first, scrapers for the rest."""
from __future__ import annotations

import time

from .config import Settings
from .feeds import FeedRetailer, FeedStore
from .scrapers import RETAILERS
from .scrapers.base import Retailer

# Homepages for shops we also scrape, so a feed-backed retailer keeps a sensible link.
_KNOWN = {r.id: r for r in RETAILERS}


class SourceRegistry:
    """Feeds win over scraping for the same retailer, and the list refreshes itself
    shortly after an import so a new feed appears without restarting the app."""

    def __init__(self, settings: Settings, store: FeedStore | None, refresh_seconds: float = 60.0) -> None:
        self._settings = settings
        self._store = store
        self._refresh = refresh_seconds
        self._checked_at = 0.0
        self._cached: list[Retailer] = []

    def retailers(self) -> list[Retailer]:
        now = time.monotonic()
        if not self._cached or now - self._checked_at >= self._refresh:
            self._cached = self._build()
            self._checked_at = now
        return self._cached

    def invalidate(self) -> None:
        self._checked_at = 0.0

    def _build(self) -> list[Retailer]:
        feed_retailers: list[Retailer] = []
        if self._store is not None:
            for retailer_id, shop_name in self._store.retailers_sync():
                if not self._settings.retailer_enabled(retailer_id):
                    continue
                known = _KNOWN.get(retailer_id)
                feed_retailers.append(
                    FeedRetailer(
                        id=retailer_id,
                        name=known.name if known else shop_name,
                        homepage=known.homepage if known else "",
                        search_template=known.search_template if known else "",
                        store=self._store,
                        notes="Affiliate product feed",
                    )
                )
        feed_ids = {r.id for r in feed_retailers}
        scrapers = [r for r in RETAILERS if r.id not in feed_ids and self._settings.retailer_enabled(r.id)]
        return feed_retailers + scrapers
