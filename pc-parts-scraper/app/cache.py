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

CREATE TABLE IF NOT EXISTS query_stats (
    query         TEXT PRIMARY KEY,
    hits          INTEGER NOT NULL DEFAULT 0,
    last_searched REAL NOT NULL
);
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
    def record_prices_sync(self, listings: list[Listing]) -> tuple[dict[str, float], dict[str, float]]:
        """Store observations. Returns (previous price where it changed, lowest price ever seen), keyed by url."""
        if not listings:
            return {}, {}
        now = time.time()
        previous: dict[str, float] = {}
        lowest: dict[str, float] = {}
        with self._lock:
            for item in listings:
                row = self._conn.execute(
                    "SELECT price, (SELECT MIN(price) FROM prices WHERE retailer = ? AND url = ?) "
                    "FROM prices WHERE retailer = ? AND url = ? ORDER BY seen_at DESC LIMIT 1",
                    (item.retailer, item.url, item.retailer, item.url),
                ).fetchone()
                last_price = row[0] if row is not None else None
                if row is not None:
                    lowest[item.url] = min(row[1], item.price)
                if last_price is None or abs(last_price - item.price) >= 0.005:
                    self._conn.execute(
                        "INSERT INTO prices(retailer, url, title, price, seen_at) VALUES (?,?,?,?,?)",
                        (item.retailer, item.url, item.title, item.price, now),
                    )
                    if last_price is not None:
                        previous[item.url] = last_price
        return previous, lowest

    async def record_prices(self, listings: list[Listing]) -> tuple[dict[str, float], dict[str, float]]:
        return await asyncio.to_thread(self.record_prices_sync, listings)

    def history_sync(self, retailer: str, url: str, limit: int = 200) -> list[dict[str, float]]:
        """Price observations, oldest first (a new row is only written when the price changes)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT price, seen_at FROM (SELECT price, seen_at FROM prices WHERE retailer = ? AND url = ? "
                "ORDER BY seen_at DESC LIMIT ?) ORDER BY seen_at ASC",
                (retailer, url, limit),
            ).fetchall()
        return [{"price": price, "seen_at": seen_at} for price, seen_at in rows]

    async def history(self, retailer: str, url: str, limit: int = 200) -> list[dict[str, float]]:
        return await asyncio.to_thread(self.history_sync, retailer, url, limit)

    # ----- query popularity (drives background warming) ----------------------------
    def bump_query_sync(self, query: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO query_stats(query, hits, last_searched) VALUES (?, 1, ?) "
                "ON CONFLICT(query) DO UPDATE SET hits = hits + 1, last_searched = excluded.last_searched",
                (query, time.time()),
            )

    async def bump_query(self, query: str) -> None:
        await asyncio.to_thread(self.bump_query_sync, query)

    def top_queries_sync(self, limit: int, min_hits: int = 1, max_age_seconds: float = 7 * 24 * 3600) -> list[str]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT query FROM query_stats WHERE hits >= ? AND last_searched >= ? "
                "ORDER BY hits DESC, last_searched DESC LIMIT ?",
                (min_hits, time.time() - max_age_seconds, limit),
            ).fetchall()
        return [row[0] for row in rows]

    def query_age_sync(self, query: str, expected_retailers: int) -> float | None:
        """Seconds since the oldest cached retailer result for a query; None if any retailer is missing."""
        with self._lock:
            row = self._conn.execute("SELECT MIN(fetched_at), COUNT(*) FROM results WHERE query = ?", (query,)).fetchone()
        if row is None or row[0] is None or row[1] < expected_retailers:
            return None
        return time.time() - row[0]

    # ----- maintenance -------------------------------------------------------------
    def purge_sync(self, max_age_seconds: float, price_max_age_seconds: float | None = None) -> int:
        """Drop old cached results (and, optionally, old price observations) so the DB stays bounded."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute("DELETE FROM results WHERE fetched_at < ?", (now - max_age_seconds,))
            removed = cur.rowcount
            if price_max_age_seconds is not None:
                removed += self._conn.execute("DELETE FROM prices WHERE seen_at < ?", (now - price_max_age_seconds,)).rowcount
                self._conn.execute("DELETE FROM query_stats WHERE last_searched < ?", (now - 30 * 24 * 3600,))
        self._hot = {k: v for k, v in self._hot.items() if v[0] >= now - max_age_seconds}
        return removed

    def close(self) -> None:
        with self._lock:
            self._conn.close()
