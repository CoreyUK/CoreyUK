"""NVIDIA's UK marketplace (marketplace.nvidia.com).

NVIDIA does not sell directly in the UK; its store page lists partner retailers
(Scan, Overclockers UK, AWD-IT, Ebuyer, ...) with each one's price and stock for
every GeForce card. The page is driven by a JSON feed, which we read directly.
The full catalogue is small, so it is fetched once, cached briefly, and filtered
locally for each query.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..fetcher import Fetcher
from ..models import Listing
from .base import Retailer
from .utils import absolute_url, clean_text, parse_price

log = logging.getLogger(__name__)

API_URL = "https://api.nvidia.partners/edge/product/search?page={page}&limit={limit}&locale=en-gb&category=GPU"
_HEADERS = {"Accept": "application/json, text/plain, */*", "Referer": "https://marketplace.nvidia.com/en-gb/consumer/graphics-cards/"}
_PAGE_LIMIT = 100
_MAX_PAGES = 30
_CATALOGUE_TTL = 300  # seconds; the search service caches per query on top of this


def _stock(entry: dict[str, Any], product: dict[str, Any]) -> bool | None:
    stock = entry.get("stock")
    if isinstance(stock, (int, float)):
        return stock > 0
    available = entry.get("isAvailable")
    if isinstance(available, bool):
        return available
    status = str(product.get("prdStatus") or "").lower()
    if status in ("buy_now", "in_stock"):
        return True
    if status in ("out_of_stock", "sold_out"):
        return False
    return None


@dataclass(slots=True)
class NvidiaStore(Retailer):
    id: str = "nvidia"
    name: str = "NVIDIA Store"
    homepage: str = "https://marketplace.nvidia.com/en-gb/consumer/graphics-cards/"
    search_template: str = "https://marketplace.nvidia.com/en-gb/consumer/graphics-cards/?search={q}"
    allow_heuristic: bool = False
    notes: str = "Lists partner retailers' prices for every GeForce card; graphics cards only."
    _catalogue: tuple[float, list[Listing]] | None = field(default=None, repr=False)
    _lock: asyncio.Lock | None = field(default=None, repr=False)

    # ----- parsing -------------------------------------------------------------------
    def parse(self, text: str) -> tuple[list[Listing], str]:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return [], "none"
        listings = self.parse_products(data)
        return listings, ("api" if listings else "none")

    def parse_products(self, data: dict[str, Any]) -> list[Listing]:
        searched = data.get("searchedProducts") or {}
        products = searched.get("productDetails") or []
        featured = searched.get("featuredProduct")
        if isinstance(featured, dict) and featured not in products:
            products = [featured, *products]
        listings: list[Listing] = []
        seen: set[str] = set()
        for product in products:
            if not isinstance(product, dict):
                continue
            title = clean_text(str(product.get("productTitle") or product.get("displayName") or ""))
            image = absolute_url(self.homepage, product.get("imageURL"))
            fallback_price = parse_price(str(product.get("productPrice") or ""))
            for entry in product.get("retailers") or []:
                if not isinstance(entry, dict):
                    continue
                url = absolute_url(self.homepage, entry.get("purchaseLink") or entry.get("directPurchaseLink") or product.get("internalLink"))
                price = parse_price(str(entry.get("salePrice") or "")) or fallback_price
                seller = clean_text(str(entry.get("retailerName") or ""))
                if not title or not url or price is None:
                    continue
                key = f"{seller}|{url}"
                if key in seen:
                    continue
                seen.add(key)
                listings.append(
                    Listing(
                        retailer=self.id,
                        retailer_name=self.name,
                        title=title,
                        price=price,
                        url=url,
                        image=image,
                        in_stock=_stock(entry, product),
                        seller=seller or None,
                    )
                )
        return listings

    # ----- fetching ------------------------------------------------------------------
    async def _load_catalogue(self, fetcher: Fetcher) -> list[Listing]:
        if self._catalogue is not None and time.monotonic() - self._catalogue[0] < _CATALOGUE_TTL:
            return self._catalogue[1]
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._catalogue is not None and time.monotonic() - self._catalogue[0] < _CATALOGUE_TTL:
                return self._catalogue[1]
            listings: list[Listing] = []
            total: int | None = None
            for page in range(1, _MAX_PAGES + 1):
                text = await fetcher.get(API_URL.format(page=page, limit=_PAGE_LIMIT), headers=_HEADERS, dump_name=f"nvidia_p{page}")
                data = json.loads(text)
                batch = self.parse_products(data)
                searched = data.get("searchedProducts") or {}
                products = searched.get("productDetails") or []
                if total is None:
                    raw_total = searched.get("totalProducts")
                    total = int(raw_total) if isinstance(raw_total, (int, float, str)) and str(raw_total).isdigit() else None
                listings.extend(batch)
                if not products or (total is not None and page * _PAGE_LIMIT >= total) or len(products) < _PAGE_LIMIT:
                    break
            self._catalogue = (time.monotonic(), listings)
            return listings

    async def search(self, fetcher: Fetcher, query: str) -> tuple[list[Listing], str]:
        from ..search import relevance  # local import to avoid a circular dependency

        catalogue = await self._load_catalogue(fetcher)
        # The catalogue is not a search engine, so require every query word to match.
        matches = [item.model_copy() for item in catalogue if relevance(query, f"{item.title} {item.seller or ''}") >= 1.0]
        log.info("%s: %d of %d marketplace listings match %r", self.id, len(matches), len(catalogue), query)
        return matches, "api"
