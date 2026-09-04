"""Polite, rate-limited async HTTP fetching with block detection."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .ratelimit import HostLimiterPool

log = logging.getLogger(__name__)


class FetchError(Exception):
    """Transport-level or HTTP-level failure."""


class BlockedError(FetchError):
    """The retailer served a bot-challenge / access-denied page."""


_BLOCK_MARKERS = re.compile(
    r"(cf-challenge|cf_chl_|challenge-platform|/cdn-cgi/challenge|"
    r"incapsula|_Incapsula_Resource|Request unsuccessful|"
    r"Access Denied|Reference #\d+\.\w+|"
    r"px-captcha|perimeterx|"
    r"Pardon Our Interruption|Are you a robot|verify you are human|"
    r"datadome|geo\.captcha-delivery)",
    re.IGNORECASE,
)

_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "no-cache",
}


class Fetcher:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._global = asyncio.Semaphore(max(1, settings.global_max_concurrency))
        self._hosts = HostLimiterPool(settings.per_host_max_concurrency, settings.per_host_min_interval_seconds)
        headers = dict(_BROWSER_HEADERS)
        headers["User-Agent"] = settings.user_agent
        self._client = httpx.AsyncClient(
            headers=headers,
            http2=transport is None,
            follow_redirects=True,
            timeout=httpx.Timeout(settings.retailer_timeout_seconds, connect=6.0),
            limits=httpx.Limits(max_connections=settings.global_max_concurrency + 4, max_keepalive_connections=8),
            transport=transport,
        )
        self._dump_dir = Path(settings.debug_dump_dir) if settings.debug_dump_dir else None

    async def close(self) -> None:
        await self._client.aclose()

    async def get(self, url: str, *, retries: int = 1, dump_name: str | None = None, headers: dict[str, str] | None = None) -> str:
        host = urlsplit(url).netloc.lower()
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._global, self._hosts.get(host):
                    started = time.monotonic()
                    response = await self._client.get(url, headers=headers)
                    elapsed = (time.monotonic() - started) * 1000
                log.debug("GET %s -> %s in %.0fms", url, response.status_code, elapsed)
                text = response.text
                self._maybe_dump(dump_name, host, response.status_code, text)
                if response.status_code in (403, 429, 503) or (
                    response.status_code == 200 and len(text) < 20000 and _BLOCK_MARKERS.search(text)
                ):
                    raise BlockedError(f"{host} returned {response.status_code} (bot protection?)")
                if response.status_code >= 400:
                    raise FetchError(f"{host} returned HTTP {response.status_code}")
                return text
            except BlockedError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt > retries:
                    raise FetchError(f"{host}: {exc.__class__.__name__}") from exc
                await asyncio.sleep(0.5 * attempt)
            except FetchError:
                if attempt > retries:
                    raise
                await asyncio.sleep(0.5 * attempt)

    def _maybe_dump(self, name: str | None, host: str, status: int, text: str) -> None:
        if not self._dump_dir:
            return
        try:
            self._dump_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^a-z0-9_-]+", "_", (name or host).lower())[:80]
            (self._dump_dir / f"{safe}_{status}.html").write_text(text, encoding="utf-8")
        except OSError as exc:  # pragma: no cover - best effort
            log.warning("could not dump html: %s", exc)
