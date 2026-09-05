"""A retailer whose results come from an imported feed rather than a live fetch."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..fetcher import Fetcher
from ..models import Listing
from ..scrapers.base import Retailer
from .store import FeedStore


@dataclass(slots=True)
class FeedRetailer(Retailer):
    """Searches the local feed store. Makes no outbound requests."""

    store: FeedStore | None = field(default=None, repr=False)
    is_local: bool = True

    async def search(self, fetcher: Fetcher, query: str) -> tuple[list[Listing], str]:
        assert self.store is not None
        products = await self.store.search(self.id, query)
        listings = [
            Listing(
                retailer=self.id,
                retailer_name=self.name,
                title=p.title,
                price=p.price,
                url=p.url,
                image=p.image,
                in_stock=p.in_stock,
            )
            for p in products
        ]
        imported_at = self.store.imported_at_sync(self.id)
        age = "" if imported_at is None else f", updated {_ago(time.time() - imported_at)}"
        return listings, f"feed{age}"


def _ago(seconds: float) -> str:
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"
