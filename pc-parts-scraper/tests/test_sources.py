"""Feeds replacing scrapers, end to end."""
from __future__ import annotations

import httpx
import pytest

from app.feeds import FeedConfig, FeedImporter, FeedStore
from app.main import create_app
from app.search import SearchService
from app.sources import SourceRegistry
from tests.conftest import FIXTURES

AWIN_MAP = {
    "Scan Computers International Ltd": "scan",
    "Ebuyer": "ebuyer",
    "Overclockers UK": "overclockers",
    "Currys": "currys",
    "Novatech": "novatech",
}


@pytest.fixture
def loaded_store() -> FeedStore:
    store = FeedStore(":memory:")
    FeedImporter(store).run(
        FeedConfig(id="awin", path=str(FIXTURES / "awin_feed.csv"), retailer_map=dict(AWIN_MAP))
    )
    yield store
    store.close()


def test_registry_prefers_feeds_and_keeps_scrapers_for_the_rest(settings, loaded_store):
    registry = SourceRegistry(settings, loaded_store)
    retailers = {r.id: r for r in registry.retailers()}
    assert getattr(retailers["scan"], "is_local", False) is True
    assert retailers["scan"].name == "Scan", "keeps the app's own name for a known shop"
    assert retailers["scan"].homepage == "https://www.scan.co.uk/"
    # Not in the feed, so still scraped.
    assert getattr(retailers["awd_it"], "is_local", False) is False
    assert getattr(retailers["nvidia"], "is_local", False) is False


def test_registry_respects_enabled_retailers(settings, loaded_store):
    settings.enabled_retailers = ["scan", "ebuyer"]
    ids = {r.id for r in SourceRegistry(settings, loaded_store).retailers()}
    assert ids == {"scan", "ebuyer"}


def test_registry_without_a_store_is_all_scrapers(settings):
    assert all(not getattr(r, "is_local", False) for r in SourceRegistry(settings, None).retailers())


def test_registry_picks_up_a_new_import_without_a_restart(settings):
    store = FeedStore(":memory:")
    registry = SourceRegistry(settings, store, refresh_seconds=0)
    assert all(not getattr(r, "is_local", False) for r in registry.retailers())
    FeedImporter(store).run(FeedConfig(id="awin", path=str(FIXTURES / "awin_feed.csv"), retailer_map=dict(AWIN_MAP)))
    registry.invalidate()
    assert getattr({r.id: r for r in registry.retailers()}["scan"], "is_local", False) is True
    store.close()


async def test_feed_search_makes_no_outbound_requests(settings, fetcher, cache, site, loaded_store):
    service = SearchService(settings, fetcher, cache, SourceRegistry(settings, loaded_store))
    result = await service.search("1tb nvme ssd", ["scan", "ebuyer"])
    assert site.calls == [], "feed-backed retailers must not hit the network"
    titles = [l.title for l in result.listings]
    assert "Crucial P3 Plus 1TB PCIe 4.0 NVMe M.2 SSD" in titles
    assert "Samsung 990 PRO 1TB M.2 NVMe PCIe 4.0 SSD" in titles
    assert result.listings[0].url.startswith("https://www.awin1.com/"), "affiliate link is preserved"
    assert all("feed" in (s.message or "") for s in result.retailers)


async def test_mixed_feed_and_scrape_search(settings, fetcher, cache, site, loaded_store):
    service = SearchService(settings, fetcher, cache, SourceRegistry(settings, loaded_store))
    result = await service.search("ssd", ["scan", "awd_it"])
    assert [c for c in site.calls if "awd-it" in c], "awd_it is still scraped"
    assert not [c for c in site.calls if "scan.co.uk" in c], "scan comes from the feed"
    assert {s.id for s in result.retailers} == {"scan", "awd_it"}


async def test_feed_results_are_not_cached(settings, fetcher, cache, loaded_store):
    service = SearchService(settings, fetcher, cache, SourceRegistry(settings, loaded_store))
    await service.search("ryzen", ["overclockers"])
    second = await service.search("ryzen", ["overclockers"])
    assert second.retailers[0].state == "ok", "always read live from the local store, never 'cached'"
    assert await cache.get("overclockers", "ryzen") is None


async def test_api_reports_feed_backed_retailers(settings, fetcher, cache, loaded_store):
    app = create_app(settings, fetcher=fetcher, cache=cache, feed_store=loaded_store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            retailers = {r["id"]: r for r in (await client.get("/api/retailers")).json()}
            assert retailers["scan"]["source"] == "feed"
            assert retailers["awd_it"]["source"] == "scrape"
            assert len(retailers) >= 11, "every configured shop is still listed"

            body = (await client.get("/api/search", params={"q": "990 pro", "retailers": "scan"})).json()
            assert body["total"] == 1
            assert body["listings"][0]["url"].startswith("https://www.awin1.com/")

            health = (await client.get("/api/health")).json()
            assert health["feeds"][0]["source"] == "awin" and health["feeds"][0]["rows"] == 8
