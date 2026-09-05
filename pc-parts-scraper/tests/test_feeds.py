from __future__ import annotations

import httpx
import pytest

from app.feeds import FeedConfig, FeedImporter, FeedStore
from app.feeds.importer import parse_money, parse_stock, slug
from app.feeds.profiles import detect
from app.feeds.store import fts_query
from tests.conftest import FIXTURES

AWIN_MAP = {
    "Scan Computers International Ltd": "scan",
    "Ebuyer": "ebuyer",
    "Overclockers UK": "overclockers",
    "Currys": "currys",
    "Novatech": "novatech",
}


@pytest.fixture
def store() -> FeedStore:
    s = FeedStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def awin_config() -> FeedConfig:
    return FeedConfig(id="awin", path=str(FIXTURES / "awin_feed.csv"), retailer_map=dict(AWIN_MAP))


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("119.99", 119.99), ("£1,249.98", 1249.98), ("104.99 GBP", 104.99), ("59.99 inc vat", 59.99),
        ("", None), (None, None), ("0.00", None), ("N/A", None), ("1,299", 1299.0),
    ],
)
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize(
    "in_stock, qty, expected",
    [
        ("1", None, True), ("0", None, False), ("in stock", "", True), ("out of stock", "0", False),
        ("yes", "3", True), ("Available", None, True), ("", "5", True), ("", "0", False), ("", "", None),
        ("preorder", None, False),
    ],
)
def test_parse_stock(in_stock, qty, expected):
    assert parse_stock(in_stock, qty) is expected


def test_slug():
    assert slug("Scan Computers International Ltd") == "scan_computers_international_ltd"
    assert slug("Box.co.uk") == "box_co_uk"
    assert slug("  ") == "unknown"


def test_profile_detection():
    assert detect(["merchant_name", "aw_deep_link", "product_name", "search_price"]).name == "awin"
    assert detect(["id", "title", "link", "price", "image_link", "availability"]).name == "google"


@pytest.mark.parametrize(
    "query, expected",
    [
        ("rtx 4070", '"rtx" AND "4070"'),
        ("2TB nvme", '("2tb" OR "2 tb") AND "nvme"'),
        ('evil" OR "x', '"evil" AND "or" AND "x"'),
        ("!!!", ""),
    ],
)
def test_fts_query_is_escaped(query, expected):
    assert fts_query(query) == expected


def test_import_awin_feed(store, awin_config):
    result = FeedImporter(store).run(awin_config)
    assert result.ok, result.error
    # 12 data rows: one has no title, one has no price, one is EUR, one is a duplicate id.
    assert result.rows == 8
    assert result.skipped == 4
    assert result.retailers == {"scan": 2, "ebuyer": 2, "overclockers": 1, "currys": 2, "novatech": 1}
    assert {r[0] for r in store.retailers_sync()} == set(result.retailers)


def test_imported_products_keep_their_details(store, awin_config):
    FeedImporter(store).run(awin_config)
    found = store.search_sync("scan", "990 pro 1tb")
    assert len(found) == 1
    product = found[0]
    assert product.title == "Samsung 990 PRO 1TB M.2 NVMe PCIe 4.0 SSD"
    assert product.price == 119.99
    assert product.url.startswith("https://www.awin1.com/pclick.php")
    assert product.in_stock is True
    assert product.ean == "8806094215434"
    assert product.mpn == "MZ-V9P1T0BW"
    assert product.delivery_cost == 5.99
    assert store.search_sync("scan", "sn850x")[0].in_stock is False


def test_exclude_pattern_filters_rows(store):
    config = FeedConfig(
        id="awin",
        path=str(FIXTURES / "awin_feed.csv"),
        retailer_map=dict(AWIN_MAP),
        exclude_pattern="(washing machine|laundry)",
    )
    result = FeedImporter(store).run(config)
    assert result.retailers["currys"] == 1
    assert store.search_sync("currys", "washing machine") == []


def test_include_pattern_keeps_only_matches(store):
    config = FeedConfig(
        id="awin", path=str(FIXTURES / "awin_feed.csv"), retailer_map=dict(AWIN_MAP), include_pattern="(ssd|nvme)"
    )
    result = FeedImporter(store).run(config)
    assert set(result.retailers) == {"scan", "ebuyer", "currys"}


def test_unmapped_merchant_gets_a_derived_id(store):
    result = FeedImporter(store).run(FeedConfig(id="awin", path=str(FIXTURES / "awin_feed.csv")))
    assert "scan_computers_international_ltd" in result.retailers
    assert "overclockers_uk" in result.retailers


def test_gzipped_feed(store, awin_config):
    gz = FeedConfig(id="awin", path=str(FIXTURES / "awin_feed.csv.gz"), retailer_map=dict(AWIN_MAP))
    assert FeedImporter(store).run(gz).rows == 8


def test_google_shopping_feed(store):
    result = FeedImporter(store).run(
        FeedConfig(id="shop", path=str(FIXTURES / "google_feed.csv"), retailer_name="Example Shop")
    )
    assert result.ok and result.rows == 2
    found = store.search_sync("example_shop", "850w psu")
    assert [p.title for p in found] == [
        "Corsair RM850e 850W 80+ Gold Fully Modular PSU",
        "be quiet! Pure Power 12 M 850W 80+ Gold PSU",
    ]
    assert found[0].in_stock is True and found[1].in_stock is False


def test_reimport_replaces_previous_rows(store, awin_config):
    importer = FeedImporter(store)
    importer.run(awin_config)
    assert len(store.search_sync("ebuyer", "crucial p3")) == 1
    smaller = FeedConfig(
        id="awin", path=str(FIXTURES / "awin_feed.csv"), retailer_map=dict(AWIN_MAP), include_pattern="ryzen"
    )
    result = importer.run(smaller)
    assert result.rows == 1
    assert store.search_sync("ebuyer", "crucial p3") == [], "old rows are gone, not merged"
    assert len(store.search_sync("overclockers", "9800x3d")) == 1


def test_failed_import_reports_but_does_not_raise(store):
    result = FeedImporter(store).run(FeedConfig(id="broken", path="/nonexistent/feed.csv"))
    assert not result.ok and "FileNotFoundError" in result.error


def test_feed_without_required_columns_is_rejected(store, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("name,cost\nThing,10\n", encoding="utf-8")
    result = FeedImporter(store).run(FeedConfig(id="bad", path=str(bad)))
    assert not result.ok and "missing a column" in result.error


def test_download_over_http(store):
    body = (FIXTURES / "awin_feed.csv").read_bytes()
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)))
    importer = FeedImporter(store, client=client)
    result = importer.run(FeedConfig(id="awin", url="https://productdata.example/feed.csv", retailer_map=dict(AWIN_MAP)))
    assert result.ok and result.rows == 8


def test_config_validation(tmp_path):
    with pytest.raises(ValueError, match="unknown feed setting"):
        FeedConfig.from_dict({"id": "x", "url": "http://e/", "typo": 1})
    with pytest.raises(ValueError, match="needs an 'id'"):
        FeedConfig.from_dict({"url": "http://e/"})
    with pytest.raises(ValueError, match="either 'url' or 'path'"):
        FeedConfig.from_dict({"id": "x"})


def test_load_feed_configs(tmp_path):
    from app.feeds import load_feed_configs

    assert load_feed_configs(str(tmp_path / "missing.json")) == []
    path = tmp_path / "feeds.json"
    path.write_text('{"//": "comment keys are ignored", "feeds": [{"id": "a", "url": "https://e/f.csv"}]}', encoding="utf-8")
    configs = load_feed_configs(str(path))
    assert len(configs) == 1 and configs[0].id == "a"
