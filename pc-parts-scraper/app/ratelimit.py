"""Outbound (per host) and inbound (per client) rate limiting."""
from __future__ import annotations

import asyncio
import time
from collections import deque


class HostLimiter:
    """Caps concurrency and enforces a minimum interval between requests to one host."""

    def __init__(self, max_concurrency: int, min_interval: float) -> None:
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self._min_interval = max(0.0, min_interval)
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> None:
        await self._sem.acquire()
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval

    async def __aexit__(self, *exc: object) -> None:
        self._sem.release()


class HostLimiterPool:
    def __init__(self, max_concurrency: int, min_interval: float) -> None:
        self._max_concurrency = max_concurrency
        self._min_interval = min_interval
        self._limiters: dict[str, HostLimiter] = {}

    def get(self, host: str) -> HostLimiter:
        limiter = self._limiters.get(host)
        if limiter is None:
            limiter = HostLimiter(self._max_concurrency, self._min_interval)
            self._limiters[host] = limiter
        return limiter


class SlidingWindowLimiter:
    """In-memory sliding-window limiter keyed by client id (e.g. IP address)."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = max(1, limit)
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = 0.0

    def check(self, key: str, now: float | None = None) -> tuple[bool, int, float]:
        """Returns (allowed, remaining, retry_after_seconds)."""
        now = time.monotonic() if now is None else now
        self._sweep(now)
        hits = self._hits.setdefault(key, deque())
        cutoff = now - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self.limit:
            return False, 0, max(0.0, hits[0] + self.window - now)
        hits.append(now)
        return True, self.limit - len(hits), 0.0

    def _sweep(self, now: float) -> None:
        # Drop idle keys occasionally so the map cannot grow without bound.
        if now - self._last_sweep < self.window:
            return
        self._last_sweep = now
        cutoff = now - self.window
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]


class MinIntervalGate:
    """Allows an action for a key at most once per interval (used for forced refreshes)."""

    def __init__(self, interval_seconds: float) -> None:
        self.interval = interval_seconds
        self._last: dict[str, float] = {}

    def allow(self, key: str, now: float | None = None) -> tuple[bool, float]:
        now = time.monotonic() if now is None else now
        last = self._last.get(key)
        if last is not None and now - last < self.interval:
            return False, self.interval - (now - last)
        self._last[key] = now
        if len(self._last) > 5000:
            cutoff = now - self.interval
            self._last = {k: v for k, v in self._last.items() if v > cutoff}
        return True, 0.0
