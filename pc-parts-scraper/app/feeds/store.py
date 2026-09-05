"""SQLite storage and full-text search for imported feed products."""
from __future__ import annotations

import asyncio
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Callable, Iterable

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feed_products (
    id            INTEGER PRIMARY KEY,
    source        TEXT NOT NULL,
    retailer      TEXT NOT NULL,
    retailer_name TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    price         REAL NOT NULL,
    url           TEXT NOT NULL,
    image         TEXT,
    in_stock      INTEGER,
    brand         TEXT,
    mpn           TEXT,
    ean           TEXT,
    category      TEXT,
    delivery_cost REAL,
    updated_at    REAL NOT NULL,
    UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS feed_products_retailer ON feed_products(retailer);

CREATE VIRTUAL TABLE IF NOT EXISTS feed_search USING fts5(
    title, brand, mpn, ean,
    content='feed_products', content_rowid='id', tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS feed_imports (
    source      TEXT PRIMARY KEY,
    imported_at REAL NOT NULL,
    rows        INTEGER NOT NULL,
    skipped     INTEGER NOT NULL DEFAULT 0
);
"""

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_UNIT_RE = re.compile(r"^(\d+)(tb|gb|mb|kb|hz|ghz|mhz|w|mm|cm|k)$")


@dataclass(slots=True)
class FeedProduct:
    source: str
    retailer: str
    retailer_name: str
    external_id: str
    title: str
    price: float
    url: str
    image: str | None = None
    in_stock: bool | None = None
    brand: str | None = None
    mpn: str | None = None
    ean: str | None = None
    category: str | None = None
    delivery_cost: float | None = None


def fts_query(text: str) -> str:
    """Turn a user's words into a safe FTS5 MATCH expression.

    Every token is quoted, so punctuation and FTS operators in user input cannot change
    the query's meaning. Size tokens also match their spaced form, so "2tb" finds
    "2 TB" as well.
    """
    clauses: list[str] = []
    for token in _TOKEN_RE.findall(text.lower()):
        unit = _UNIT_RE.match(token)
        if unit:
            clauses.append(f'("{token}" OR "{unit.group(1)} {unit.group(2)}")')
        else:
            clauses.append(f'"{token}"')
    return " AND ".join(clauses)


class FeedStore:
    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    # ----- import ------------------------------------------------------------------
    def replace_source_sync(
        self,
        source: str,
        products: Iterable[FeedProduct],
        skipped: int | Callable[[], int] = 0,
        batch_size: int = 5000,
    ) -> int:
        """Swap in a source's products atomically, so searches never see a half-written
        feed. Products are consumed in batches, so a multi-GB feed never has to fit in
        memory."""
        now = time.time()
        insert = (
            "INSERT OR REPLACE INTO feed_products(source, retailer, retailer_name, external_id, title, price,"
            " url, image, in_stock, brand, mpn, ean, category, delivery_cost, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        stream = iter(products)
        written = 0
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute("DELETE FROM feed_products WHERE source = ?", (source,))
                while True:
                    batch = list(islice(stream, batch_size))
                    if not batch:
                        break
                    self._conn.executemany(
                        insert,
                        [
                            (
                                p.source, p.retailer, p.retailer_name, p.external_id, p.title, p.price, p.url,
                                p.image, None if p.in_stock is None else int(p.in_stock), p.brand, p.mpn, p.ean,
                                p.category, p.delivery_cost, now,
                            )
                            for p in batch
                        ],
                    )
                    written += len(batch)
                dropped = skipped() if callable(skipped) else skipped
                self._conn.execute(
                    "INSERT OR REPLACE INTO feed_imports(source, imported_at, rows, skipped) VALUES (?,?,?,?)",
                    (source, now, written, dropped),
                )
                self._conn.execute("INSERT INTO feed_search(feed_search) VALUES('rebuild')")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return written

    # ----- read --------------------------------------------------------------------
    def retailers_sync(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT retailer, retailer_name, COUNT(*) c FROM feed_products GROUP BY retailer ORDER BY c DESC"
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def imported_at_sync(self, retailer: str) -> float | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(updated_at) FROM feed_products WHERE retailer = ?", (retailer,)
            ).fetchone()
        return row[0] if row and row[0] is not None else None

    def search_sync(self, retailer: str, query: str, limit: int = 60) -> list[FeedProduct]:
        match = fts_query(query)
        if not match:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.source, p.retailer, p.retailer_name, p.external_id, p.title, p.price, p.url, p.image,"
                " p.in_stock, p.brand, p.mpn, p.ean, p.category, p.delivery_cost"
                " FROM feed_search s JOIN feed_products p ON p.id = s.rowid"
                " WHERE feed_search MATCH ? AND p.retailer = ?"
                " ORDER BY bm25(feed_search), p.price LIMIT ?",
                (match, retailer, limit),
            ).fetchall()
        return [
            FeedProduct(
                source=r[0], retailer=r[1], retailer_name=r[2], external_id=r[3], title=r[4], price=r[5], url=r[6],
                image=r[7], in_stock=None if r[8] is None else bool(r[8]), brand=r[9], mpn=r[10], ean=r[11],
                category=r[12], delivery_cost=r[13],
            )
            for r in rows
        ]

    async def search(self, retailer: str, query: str, limit: int = 60) -> list[FeedProduct]:
        return await asyncio.to_thread(self.search_sync, retailer, query, limit)

    def stats_sync(self) -> list[dict[str, object]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, imported_at, rows, skipped FROM feed_imports ORDER BY imported_at DESC"
            ).fetchall()
        return [{"source": r[0], "imported_at": r[1], "rows": r[2], "skipped": r[3]} for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
