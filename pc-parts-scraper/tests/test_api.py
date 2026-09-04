import httpx
import pytest

from app.main import create_app


@pytest.fixture
async def client(settings, fetcher, cache):
    app = create_app(settings, fetcher=fetcher, cache=cache)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_index_and_static(client):
    r = await client.get("/")
    assert r.status_code == 200 and "PartsPrice" in r.text
    assert (await client.get("/static/app.js")).status_code == 200
    assert (await client.get("/static/styles.css")).status_code == 200


async def test_retailers_and_categories(client):
    retailers = (await client.get("/api/retailers")).json()
    assert {r["id"] for r in retailers} >= {"scan", "overclockers", "ebuyer", "ccl", "novatech", "awd_it", "box", "currys", "newegg", "amazon"}
    assert all(r["enabled"] for r in retailers)
    categories = (await client.get("/api/categories")).json()
    assert categories and {"label", "query"} <= set(categories[0])


async def test_search_endpoint(client):
    r = await client.get("/api/search", params={"q": "ssd"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "ssd" and body["total"] > 0
    assert len(body["retailers"]) == 10
    assert r.headers["X-RateLimit-Remaining"] == "4"
    first = body["listings"][0]
    assert {"retailer", "retailer_name", "title", "price", "url", "in_stock", "relevance"} <= set(first)


async def test_search_validation(client):
    assert (await client.get("/api/search", params={"q": "   "})).status_code == 400
    assert (await client.get("/api/search", params={"q": "x" * 200})).status_code == 400
    r = await client.get("/api/search", params={"q": "ssd", "retailers": "scan,nope"})
    assert r.status_code == 400 and "nope" in r.json()["detail"]


async def test_search_is_rate_limited_per_client(client):
    for _ in range(5):
        assert (await client.get("/api/search", params={"q": "ram"})).status_code == 200
    r = await client.get("/api/search", params={"q": "ram"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    other = await client.get("/api/search", params={"q": "ram"}, headers={"X-Forwarded-For": "203.0.113.9"})
    assert other.status_code == 200


async def test_history_endpoint(client):
    body = (await client.get("/api/search", params={"q": "rtx", "retailers": "awd_it"})).json()
    url = body["listings"][0]["url"]
    r = await client.get("/api/history", params={"retailer": "awd_it", "url": url})
    assert r.status_code == 200
    assert len(r.json()["points"]) == 1
