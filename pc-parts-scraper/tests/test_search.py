import asyncio
import time

import httpx
import pytest

from app.scrapers import RETAILERS
from app.search import SearchService, normalise_query, relevance
from tests.conftest import fixture


@pytest.mark.parametrize(
    "query, title, expected",
    [
        ("1tb nvme ssd", "Samsung 990 PRO 1TB M.2 NVMe PCIe 4.0 SSD", 1.0),
        ("1 tb ssd", "Samsung 990 PRO 1TB M.2 NVMe PCIe 4.0 SSD", 1.0),
        ("rtx 4070", "MSI GeForce RTX 4070 VENTUS 2X 12GB Graphics Card", 1.0),
        ("rtx 4070", "Corsair RM850e 850W PSU", 0.0),
        ("ryzen 7800x3d", "AMD Ryzen 7 7800X3D 8 Core AM5 Processor", 1.0),
        ("ddr5 32gb", "Corsair Vengeance 32GB (2x16GB) DDR5 6000MHz", 1.0),
        ("", "anything", 1.0),
    ],
)
def test_relevance(query, title, expected):
    assert relevance(query, title) == expected


def test_normalise_query():
    assert normalise_query("  RTX   4070\tTi ") == "rtx 4070 ti"


async def test_search_merges_sorts_and_caches(settings, fetcher, cache, site):
    service = SearchService(settings, fetcher, cache, RETAILERS)
    result = await service.search("ssd")
    assert result.total > 0
    prices = [l.price for l in result.listings]
    assert prices == sorted(prices)
    assert all(l.relevance > 0 for l in result.listings)
    assert {s.state for s in result.retailers} == {"ok"}
    assert len(site.calls) == len(RETAILERS)

    again = await service.search("SSD ")
    assert len(site.calls) == len(RETAILERS), "second search must be served from cache"
    assert all(s.state == "cached" for s in again.retailers)
    assert again.total == result.total


async def test_retailer_filter_and_force_refresh(settings, fetcher, cache, site):
    service = SearchService(settings, fetcher, cache, RETAILERS)
    result = await service.search("ssd", ["scan", "ebuyer"])
    assert {s.id for s in result.retailers} == {"scan", "ebuyer"}
    assert len(site.calls) == 2
    await service.search("ssd", ["scan", "ebuyer"], force=True)
    assert len(site.calls) == 4


async def test_failures_are_isolated_and_stale_results_are_kept(settings, fetcher, cache, site):
    service = SearchService(settings, fetcher, cache, RETAILERS)
    first = await service.search("ssd", ["scan", "currys", "ebuyer"])
    assert {s.id: s.state for s in first.retailers} == {"scan": "ok", "currys": "ok", "ebuyer": "ok"}

    site.overrides["scan"] = httpx.Response(200, text=fixture("blocked"))
    site.overrides["ebuyer"] = httpx.ConnectError("boom")
    site.overrides["currys"] = httpx.Response(500, text="oops")
    site.overrides["box"] = httpx.Response(500, text="oops")
    second = await service.search("ssd", ["scan", "currys", "ebuyer", "box"], force=True)
    states = {s.id: s for s in second.retailers}
    assert states["scan"].state == "stale" and states["scan"].count == 2
    assert "blocked" in (states["scan"].message or "")
    assert states["ebuyer"].state == "stale" and states["ebuyer"].count == 2
    assert states["currys"].state == "stale" and "HTTP 500" in (states["currys"].message or "")
    assert states["box"].state == "error" and states["box"].count == 0, "no earlier results to fall back on"
    assert second.total == first.total


async def test_timeout_is_reported(settings, fetcher, cache, site):
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, text="")

    fetcher._client._transport = httpx.MockTransport(slow)  # type: ignore[attr-defined]
    settings.retailer_timeout_seconds = 0.2
    service = SearchService(settings, fetcher, cache, RETAILERS)
    result = await service.search("ssd", ["scan"])
    assert result.retailers[0].state == "timeout"
    assert result.total == 0


async def test_single_flight_dedupes_concurrent_requests(settings, fetcher, cache, site):
    service = SearchService(settings, fetcher, cache, RETAILERS)
    results = await asyncio.gather(*(service.search("nvme", ["awd_it"]) for _ in range(5)))
    assert len(site.calls) == 1
    assert all(r.total == results[0].total for r in results)


async def test_price_history_records_changes(settings, fetcher, cache, site):
    service = SearchService(settings, fetcher, cache, RETAILERS)
    first = await service.search("rtx", ["awd_it"])
    assert all(l.previous_price is None for l in first.listings)
    cheaper = fixture("awd_it").replace('data-price-amount="519.99"', 'data-price-amount="499.99"')
    site.overrides["awd_it"] = httpx.Response(200, text=cheaper)
    second = await service.search("rtx", ["awd_it"], force=True)
    changed = [l for l in second.listings if l.previous_price is not None]
    assert len(changed) == 1
    assert changed[0].price == 499.99 and changed[0].previous_price == 519.99
    history = await cache.history("awd_it", changed[0].url)
    assert [p["price"] for p in history] == [499.99, 519.99]
    assert history[0]["seen_at"] <= time.time()
