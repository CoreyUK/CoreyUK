from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.cache import ResultCache
from app.config import Settings
from app.fetcher import Fetcher
from app.scrapers import RETAILERS

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        cache_db_path=":memory:",
        cache_ttl_seconds=600,
        retailer_timeout_seconds=2.0,
        per_host_min_interval_seconds=0.0,
        api_rate_limit_per_minute=5,
        force_refresh_min_interval_seconds=60,
    )


class FakeSite:
    """Routes each retailer host to a canned response; lets tests inject failures."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.overrides: dict[str, httpx.Response | Exception] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        self.calls.append(str(request.url))
        if host == "api.nvidia.partners":
            override = self.overrides.get("nvidia")
            if isinstance(override, Exception):
                raise override
            return override or httpx.Response(200, json=json.loads((FIXTURES / "nvidia.json").read_text(encoding="utf-8")))
        for retailer in RETAILERS:
            if retailer.homepage.split("/")[2] == host:
                override = self.overrides.get(retailer.id)
                if isinstance(override, Exception):
                    raise override
                if override is not None:
                    return override
                return httpx.Response(200, text=fixture(retailer.id))
        return httpx.Response(404, text="not found")


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()


@pytest.fixture
async def fetcher(settings: Settings, site: FakeSite):
    f = Fetcher(settings, transport=httpx.MockTransport(site.handler))
    yield f
    await f.close()


@pytest.fixture
def cache(settings: Settings) -> ResultCache:
    c = ResultCache(settings.cache_db_path, settings.cache_ttl_seconds)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _reset_marketplace_catalogue():
    """The NVIDIA source caches its catalogue in-process; start every test cold."""
    from app.scrapers.retailers import NVIDIA

    NVIDIA._catalogue = None
    yield
    NVIDIA._catalogue = None
