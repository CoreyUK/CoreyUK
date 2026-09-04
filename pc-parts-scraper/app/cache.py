"""SQLite-backed per-retailer result cache plus a lightweight price history."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path

from .models import Listing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    key        TEXT PRIMARY KEY,
    retailer   TEXT NOT NULL,
    query      TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS results_fetched_at ON results(fetched_at);

CREATE TABLE IF NOT EXISTS prices (
    retailer  TEXT NOT NULL,
    url       TEXT NOT NULL,
    title     TEXT NOT NULL,
    price     REAL NOT NULL,
    seen_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_url ON prices(retailer, url, seen_at);
"""


class ResultCache:
    def __init__(self, db_path: str, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        path = Path(db_path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()
        # Small in-process hot cache to avoid touching SQLite for repeated queries.
        self._hot: dict[str, tuple[float, list[Listing]]] = {}

    # ----- results -----------------------------------------------------------------
    @staticmethod
    def key(retailer: str, query: str) -> str:
        return f"{retailer}\x00{query}"

    def get_sync(self, retailer: str, query: str) -> tuple[float, list[Listing]] | None:
        k = self.key(retailer, query)
        hit = self._hot.get(k)
        if hit is not None:
            return hit
        with self._lock:
            row = self._conn.execute("SELECT fetched_at, payload FROM results WHERE key = ?", (k,)).fetchone()
        if row is None:
            return None
        fetched_at, payload = row
        listings = [Listing.model_validate(item) for item in json.loads(payload)]
        self._hot[k] = (fetched_at, listings)
        return fetched_at, listings

    def put_sync(self, retailer: str, query: str, listings: list[Listing], fetched_at: float | None = None) -> None:
        fetched_at = time.time() if fetched_at is None else fetched_at
        k = self.key(retailer, query)
        payload = json.dumps([item.model_dump() for item in listings], separators=(",", ":"))
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO results(key, retailer, query, fetched_at, payload) VALUES (?,?,?,?,?)",
                (k, retailer, query, fetched_at, payload),
            )
        self._hot[k] = (fetched_at, listings)
        if len(self._hot) > 512:
            oldest = sorted(self._hot.items(), key=lambda kv: kv[1][0])[: len(self._hot) // 2]
            for old_key, _ in oldest:
                self._hot.pop(old_key, None)

    async def get(self, retailer: str, query: str) -> tuple[float, list[Listing]] | None:
        return await asyncio.to_thread(self.get_sync, retailer, query)

    async def put(self, retailer: str, query: str, listings: list[Listing]) -> None:
        await asyncio.to_thread(self.put_sync, retailer, query, listings)

    def is_fresh(self, fetched_at: float, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now - fetched_at < self.ttl

    # ----- price history -----------------------------------------------------------
    def record_prices_sync(self, listings: list[Listing]) -> dict[str, float]:
        """Store observations and return previous prices keyed by url where the price changed."""
        if not listings:
            return {}
        now = time.time()
        previous: dict[str, float] = {}
        with self._lock:
            for item in listings:
                row = self._conn.execute(
                    "SELECT price FROM prices WHERE retailer = ? AND url = ? ORDER BY seen_at DESC LIMIT 1",
                    (item.retailer, item.url),
                ).fetchone()
                if row is None or abs(row[0] - item.price) >= 0.005:
                    self._conn.execute(
                        "INSERT INTO prices(retailer, url, title, price, seen_at) VALUES (?,?,?,?,?)",
                        (item.retailer, item.url, item.title, item.price, now),
                    )
                    if row is not None:
                        previous[item.url] = row[0]
        return previous

    async def record_prices(self, listings: list[Listing]) -> dict[str, float]:
        return await asyncio.to_thread(self.record_prices_sync, listings)

    def history_sync(self, retailer: str, url: str, limit: int = 50) -> list[dict[str, float]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT price, seen_at FROM prices WHERE retailer = ? AND url = ? ORDER BY seen_at DESC LIMIT ?",
                (retailer, url, limit),
            ).fetchall()
        return [{"price": price, "seen_at": seen_at} for price, seen_at in rows]

    async def history(self, retailer: str, url: str, limit: int = 50) -> list[dict[str, float]]:
        return await asyncio.to_thread(self.history_sync, retailer, url, limit)

    # ----- maintenance -------------------------------------------------------------
    def purge_sync(self, max_age_seconds: float, price_max_age_seconds: float | None = None) -> int:
        """Drop old cached results (and, optionally, old price observations) so the DB stays bounded."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute("DELETE FROM results WHERE fetched_at < ?", (now - max_age_seconds,))
            removed = cur.rowcount
            if price_max_age_seconds is not None:
                removed += self._conn.execute("DELETE FROM prices WHERE seen_at < ?", (now - price_max_age_seconds,)).rowcount
        self._hot = {k: v for k, v in self._hot.items() if v[0] >= now - max_age_seconds}
        return removed

    def close(self) -> None:
        with self._lock:
            self._conn.close()
