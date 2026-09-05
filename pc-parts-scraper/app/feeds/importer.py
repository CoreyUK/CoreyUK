"""Download an affiliate product feed and load it into the local store.

A feed is a CSV (often gzipped) with one row per product. Awin's "Create-a-Feed"
builds a single combined file across every merchant you are approved for, so one
configured feed usually covers every retailer at once — the merchant name column
splits the rows back out per shop.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import os
import re
import tempfile
import time
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import httpx

from .profiles import PROFILES, Profile, detect
from .store import FeedProduct, FeedStore

log = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_TRUE = {"1", "y", "yes", "true", "in stock", "instock", "available", "in_stock"}
_FALSE = {"0", "n", "no", "false", "out of stock", "outofstock", "oos", "unavailable", "preorder", "backorder"}
# Feeds are streamed to disk, but stop runaway downloads from filling a small VPS.
_MAX_BYTES = 4 * 1024 * 1024 * 1024


def slug(value: str) -> str:
    return _SLUG_RE.sub("_", value.strip().lower()).strip("_") or "unknown"


def parse_money(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("\xa0", " ")
    if not text:
        return None
    text = re.sub(r"(?i)\b(gbp|inc\.? ?vat|incl\.? ?vat|each)\b", "", text)
    text = text.replace("£", "").replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if match is None:
        return None
    try:
        amount = float(match.group(0))
    except ValueError:
        return None
    return amount if amount > 0 else None


def parse_stock(in_stock: str | None, quantity: str | None) -> bool | None:
    text = (in_stock or "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    if text:
        # Google feeds use "in stock" / "out of stock" with extra words around them.
        if "out of stock" in text or "unavailable" in text or "preorder" in text:
            return False
        if "in stock" in text or "available" in text:
            return True
    qty = (quantity or "").strip()
    if qty.isdigit():
        return int(qty) > 0
    return None


@dataclass(slots=True)
class FeedConfig:
    id: str
    url: str = ""
    path: str = ""  # local file instead of a URL (handy for testing)
    profile: str = "auto"
    delimiter: str = ""  # "" = sniff, otherwise e.g. "," or "\t"
    encoding: str = "utf-8"
    retailer_name: str = ""  # fixed shop name when the feed has no merchant column
    retailer_map: dict[str, str] = field(default_factory=dict)  # merchant name -> retailer id
    include_pattern: str = ""  # keep rows whose title/category match this regex
    exclude_pattern: str = ""  # drop rows whose title/category match this regex
    currency: str = "GBP"  # rows in another currency are skipped
    max_rows: int = 0  # 0 = no limit

    @classmethod
    def from_dict(cls, data: dict) -> "FeedConfig":
        known = {f for f in cls.__slots__}  # type: ignore[attr-defined]
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown feed setting(s): {', '.join(sorted(unknown))}")
        if not data.get("id"):
            raise ValueError("each feed needs an 'id'")
        if not data.get("url") and not data.get("path"):
            raise ValueError(f"feed {data['id']}: set either 'url' or 'path'")
        return cls(**data)


def load_feed_configs(path: str) -> list[FeedConfig]:
    file = Path(path)
    if not file.is_file():
        return []
    data = json.loads(file.read_text(encoding="utf-8"))
    feeds = data.get("feeds", data if isinstance(data, list) else [])
    return [FeedConfig.from_dict(item) for item in feeds]


@dataclass(slots=True)
class Counter:
    """Running totals while a feed streams past."""

    kept: int = 0
    skipped: int = 0
    retailers: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class ImportResult:
    source: str
    rows: int = 0
    skipped: int = 0
    retailers: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@contextmanager
def _open_text(path: Path, encoding: str) -> Iterator[Iterator[str]]:
    """Open a feed for reading, transparently handling gzip and zip, without
    loading it into memory. Real combined feeds run to several GB."""
    with open(path, "rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        with gzip.open(path, "rt", encoding=encoding, errors="replace", newline="") as handle:
            yield handle
    elif magic == b"PK":
        with zipfile.ZipFile(path) as archive:
            inner = next((n for n in archive.namelist() if n.lower().endswith((".csv", ".txt", ".tsv"))), None)
            if inner is None:
                raise ValueError(f"{path.name}: zip contains no csv file")
            with archive.open(inner) as raw:
                yield io.TextIOWrapper(raw, encoding=encoding, errors="replace", newline="")
    else:
        with open(path, "rt", encoding=encoding, errors="replace", newline="") as handle:
            yield handle


class FeedImporter:
    def __init__(self, store: FeedStore, *, timeout: float = 300.0, client: httpx.Client | None = None) -> None:
        self.store = store
        self._timeout = timeout
        self._client = client

    @contextmanager
    def download(self, config: FeedConfig) -> Iterator[Path]:
        """Yield a local path for the feed, downloading to a temp file when needed."""
        if config.path:
            yield Path(config.path)
            return
        client = self._client or httpx.Client(timeout=self._timeout, follow_redirects=True)
        handle, temp_name = tempfile.mkstemp(prefix=f"feed_{config.id}_", suffix=".dat")
        temp = Path(temp_name)
        try:
            total = 0
            with os.fdopen(handle, "wb") as sink, client.stream("GET", config.url) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=1 << 20):
                    total += len(chunk)
                    if total > _MAX_BYTES:
                        raise ValueError(
                            f"{config.id}: feed is larger than {_MAX_BYTES // (1024 * 1024)}MB. "
                            "Narrow the advertiser or category selection in Create-a-Feed."
                        )
                    sink.write(chunk)
            log.info("%s: downloaded %.1f MB", config.id, total / (1024 * 1024))
            yield temp
        finally:
            temp.unlink(missing_ok=True)
            if self._client is None:
                client.close()

    def _columns(self, config: FeedConfig, headers: list[str]) -> tuple[Profile, dict[str, str | None]]:
        profile = PROFILES[config.profile] if config.profile in PROFILES else detect(headers)
        lookup = {h.strip().lower(): h for h in headers}
        columns = {
            name: next((lookup[c.lower()] for c in candidates if c.lower() in lookup), None)
            for name, candidates in profile.fields().items()
        }
        missing = [name for name in ("title", "price", "url") if columns[name] is None]
        if missing:
            raise ValueError(
                f"{config.id}: feed is missing a column for {', '.join(missing)}. "
                f"Columns present: {', '.join(sorted(lookup))}"
            )
        absent = [name for name in ("in_stock", "brand", "mpn", "ean") if columns[name] is None]
        if absent:
            log.warning(
                "%s: feed has no %s column(s); add them in Create-a-Feed for stock badges and product matching",
                config.id, ", ".join(absent),
            )
        return profile, columns

    def _delimiter(self, config: FeedConfig, path: Path) -> str:
        if config.delimiter:
            return config.delimiter
        with _open_text(path, config.encoding) as stream:
            sample = stream.read(8192)
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            return ","

    def parse(self, config: FeedConfig, path: Path, counter: "Counter | None" = None) -> Iterator[FeedProduct]:
        """Stream products out of a feed file, counting rows dropped along the way."""
        counter = Counter() if counter is None else counter
        delimiter = self._delimiter(config, path)
        include = re.compile(config.include_pattern, re.IGNORECASE) if config.include_pattern else None
        exclude = re.compile(config.exclude_pattern, re.IGNORECASE) if config.exclude_pattern else None
        want_currency = (config.currency or "").strip().upper()

        seen: set[str] = set()
        with _open_text(path, config.encoding) as stream:
            reader = csv.DictReader(stream, delimiter=delimiter)
            headers = reader.fieldnames or []
            if not headers:
                raise ValueError(f"{config.id}: feed has no header row")
            profile, columns = self._columns(config, headers)
            log.info("%s: %d columns, using the %s column layout", config.id, len(headers), profile.name)

            for row in reader:
                yield from self._row(config, row, columns, include, exclude, want_currency, seen, counter)
                if config.max_rows and counter.kept >= config.max_rows:
                    break

    def _row(self, config, row, columns, include, exclude, want_currency, seen, counter) -> Iterator[FeedProduct]:
            def cell(name: str) -> str | None:
                column = columns[name]
                if column is None:
                    return None
                value = row.get(column)
                return value.strip() if isinstance(value, str) and value.strip() else None

            title, url, price = cell("title"), cell("url"), parse_money(cell("price"))
            if not title or not url or price is None:
                counter.skipped += 1
                return
            currency = (cell("currency") or want_currency).upper()
            if want_currency and currency != want_currency:
                counter.skipped += 1
                return
            category = cell("category")
            if include and not include.search(f"{title} {category or ''}"):
                counter.skipped += 1
                return
            if exclude and exclude.search(f"{title} {category or ''}"):
                counter.skipped += 1
                return

            shop = cell("retailer_name") or config.retailer_name or config.id
            retailer_id = config.retailer_map.get(shop) or config.retailer_map.get(shop.lower()) or slug(shop)
            external_id = cell("external_id") or url
            key = f"{retailer_id}\x00{external_id}"
            if key in seen:
                counter.skipped += 1
                return
            seen.add(key)

            counter.kept += 1
            counter.retailers[retailer_id] = counter.retailers.get(retailer_id, 0) + 1
            yield FeedProduct(
                source=config.id,
                retailer=retailer_id,
                retailer_name=shop,
                external_id=external_id,
                title=title,
                price=price,
                url=url,
                image=cell("image"),
                in_stock=parse_stock(cell("in_stock"), cell("stock_quantity")),
                brand=cell("brand"),
                mpn=cell("mpn"),
                ean=cell("ean"),
                category=category,
                delivery_cost=parse_money(cell("delivery_cost")),
            )

    def run(self, config: FeedConfig) -> ImportResult:
        started = time.monotonic()
        result = ImportResult(source=config.id)
        counter = Counter()
        try:
            with self.download(config) as path:
                self.store.replace_source_sync(config.id, self.parse(config, path, counter), lambda: counter.skipped)
            result.rows = counter.kept
            result.skipped = counter.skipped
            result.retailers = dict(counter.retailers)
        except Exception as exc:
            result.error = f"{exc.__class__.__name__}: {exc}"
            log.error("%s: import failed: %s", config.id, result.error)
        result.seconds = time.monotonic() - started
        return result
