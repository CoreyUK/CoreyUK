"""Fan-out search across retailers with caching, timeouts and single-flight de-duplication."""
from __future__ import annotations

import asyncio
import logging
import re
import time

from .cache import ResultCache
from .config import Settings
from .fetcher import BlockedError, FetchError, Fetcher
from .models import Listing, RetailerStatus, SearchResponse
from .scrapers import Retailer

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_UNIT_GLUE_RE = re.compile(r"(\d)\s+(tb|gb|mb|ghz|mhz|w|mm|hz)\b")


def normalise_query(raw: str) -> str:
    return re.sub(r"\s+", " ", raw or "").strip().lower()


def _tokens(text: str) -> list[str]:
    text = _UNIT_GLUE_RE.sub(r"\1\2", text.lower())
    return _TOKEN_RE.findall(text)


def relevance(query: str, title: str) -> float:
    """Fraction of query tokens present in the title (0..1)."""
    q = [t for t in _tokens(query) if len(t) > 1]
    if not q:
        return 1.0
    t = _tokens(title)
    joined = "".join(t)
    hits = 0
    for token in q:
        if token in t or (len(token) >= 3 and token in joined):
            hits += 1
    return hits / len(q)


class SearchService:
    def __init__(self, settings: Settings, fetcher: Fetcher, cache: ResultCache, retailers: list[Retailer]) -> None:
        self.settings = settings
        self.fetcher = fetcher
        self.cache = cache
        self.retailers = retailers
        self._inflight: dict[str, asyncio.Task[tuple[list[Listing], RetailerStatus]]] = {}

    # ----- public --------------------------------------------------------------------
    async def search(self, raw_query: str, retailer_ids: list[str] | None = None, *, force: bool = False) -> SearchResponse:
        query = normalise_query(raw_query)
        chosen = [r for r in self.retailers if not retailer_ids or r.id in retailer_ids]
        results = await asyncio.gather(*(self._search_one(r, query, force) for r in chosen))
        listings: list[Listing] = []
        statuses: list[RetailerStatus] = []
        for items, status in results:
            listings.extend(items)
            statuses.append(status)
        listings = [self._score(query, item) for item in listings]
        listings = [item for item in listings if item.relevance > 0]
        listings.sort(key=lambda item: (item.price, -item.relevance))
        return SearchResponse(
            query=query,
            listings=listings,
            retailers=statuses,
            fetched_at=time.time(),
            ttl_seconds=self.cache.ttl,
            total=len(listings),
        )

    # ----- internals -----------------------------------------------------------------
    @staticmethod
    def _score(query: str, item: Listing) -> Listing:
        item.relevance = round(relevance(query, item.title), 3)
        return item

    async def _search_one(self, retailer: Retailer, query: str, force: bool) -> tuple[list[Listing], RetailerStatus]:
        cached = await self.cache.get(retailer.id, query)
        if cached is not None and not force and self.cache.is_fresh(cached[0]):
            fetched_at, items = cached
            return [i.model_copy() for i in items], RetailerStatus(
                id=retailer.id, name=retailer.name, state="cached", count=len(items), fetched_at=fetched_at
            )

        key = self.cache.key(retailer.id, query)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(retailer, query, cached))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        items, status = await task
        return [i.model_copy() for i in items], status.model_copy()

    async def _fetch(self, retailer: Retailer, query: str, stale: tuple[float, list[Listing]] | None) -> tuple[list[Listing], RetailerStatus]:
        started = time.monotonic()
        try:
            items, tier = await asyncio.wait_for(retailer.search(self.fetcher, query), timeout=self.settings.retailer_timeout_seconds + 1)
            items = items[: self.settings.max_results_per_retailer]
            previous = await self.cache.record_prices(items)
            for item in items:
                item.previous_price = previous.get(item.url)
            now = time.time()
            await self.cache.put(retailer.id, query, items)
            ms = int((time.monotonic() - started) * 1000)
            state = "ok" if items else "empty"
            return items, RetailerStatus(id=retailer.id, name=retailer.name, state=state, count=len(items), ms=ms, fetched_at=now, message=f"via {tier}")
        except asyncio.TimeoutError:
            return self._fallback(retailer, stale, "timeout", f"no response within {self.settings.retailer_timeout_seconds:.0f}s", started)
        except BlockedError as exc:
            return self._fallback(retailer, stale, "blocked", str(exc), started)
        except FetchError as exc:
            return self._fallback(retailer, stale, "error", str(exc), started)
        except Exception as exc:  # parser bug or unexpected markup must not take the whole search down
            log.exception("%s: unexpected failure", retailer.id)
            return self._fallback(retailer, stale, "error", f"{exc.__class__.__name__}: {exc}", started)

    @staticmethod
    def _fallback(retailer: Retailer, stale: tuple[float, list[Listing]] | None, state: str, message: str, started: float) -> tuple[list[Listing], RetailerStatus]:
        ms = int((time.monotonic() - started) * 1000)
        log.warning("%s: %s (%s)", retailer.id, state, message)
        if stale is not None:
            fetched_at, items = stale
            return items, RetailerStatus(id=retailer.id, name=retailer.name, state="stale", count=len(items), ms=ms, fetched_at=fetched_at, message=f"{state}: {message}; showing older results")
        return [], RetailerStatus(id=retailer.id, name=retailer.name, state=state, count=0, ms=ms, message=message)  # type: ignore[arg-type]
