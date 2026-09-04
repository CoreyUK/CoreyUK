"""Background refresh of popular searches so results are already fresh when someone asks."""
from __future__ import annotations

import asyncio
import logging
import time

from .cache import ResultCache
from .config import Settings
from .search import SearchService, normalise_query

log = logging.getLogger(__name__)


class Warmer:
    def __init__(self, settings: Settings, service: SearchService, cache: ResultCache, seed_queries: list[str]) -> None:
        self.settings = settings
        self.service = service
        self.cache = cache
        self.seed_queries = [normalise_query(q) for q in seed_queries]
        self.last_run: float | None = None
        self.last_refreshed: list[str] = []
        self._last_warm: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self.settings.warm_top_queries > 0 or (self.settings.warm_categories and bool(self.seed_queries))

    def candidates(self) -> list[str]:
        queries: list[str] = []
        if self.settings.warm_categories:
            queries.extend(self.seed_queries)
        queries.extend(self.cache.top_queries_sync(self.settings.warm_top_queries, self.settings.warm_min_hits))
        seen: set[str] = set()
        return [q for q in queries if q and not (q in seen or seen.add(q))]

    def _due(self, query: str, now: float) -> bool:
        ttl = self.cache.ttl
        margin = min(120.0, ttl / 4)  # refresh slightly before the cache would go stale
        last = self._last_warm.get(query)
        if last is not None:
            return now - last >= ttl - margin
        age = self.cache.query_age_sync(query, len(self.service.retailers))
        return age is None or age >= ttl - margin

    async def run_once(self) -> list[str]:
        now = time.time()
        refreshed: list[str] = []
        for query in self.candidates():
            if not self._due(query, now):
                continue
            try:
                await self.service.search(query, force=True)
                refreshed.append(query)
            except Exception:  # never let one bad query stop the loop
                log.exception("warm refresh failed for %r", query)
            self._last_warm[query] = time.time()
        self.last_run = time.time()
        self.last_refreshed = refreshed
        if refreshed:
            log.info("warmed %d queries: %s", len(refreshed), ", ".join(refreshed))
        return refreshed

    async def loop(self) -> None:
        await asyncio.sleep(5)  # let the server finish starting
        while True:
            try:
                await self.run_once()
            except Exception:  # pragma: no cover
                log.exception("warm loop iteration failed")
            await asyncio.sleep(max(10, self.settings.warm_interval_seconds))

    def status(self) -> dict[str, object]:
        return {"enabled": self.enabled, "last_run": self.last_run, "last_refreshed": self.last_refreshed, "tracked": len(self._last_warm)}
