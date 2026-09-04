import pytest
from selectolax.parser import HTMLParser

from app.scrapers.utils import absolute_url, extract_heuristic, extract_jsonld, extract_microdata, parse_price, stock_from_text
from tests.conftest import fixture


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("£119.99", 119.99),
        ("£1,249.98", 1249.98),
        ("£ 99.95", 99.95),
        ("£ 519 .99", 519.99),
        ("£519 .", 519.0),
        ("£99", 99.0),
        ("From £1,249.98 inc VAT £1,041.65 ex VAT", 1249.98),
        ("519.99", 519.99),
        ("579", 579.0),
        ("1,249.98", 1249.98),
        ("GBP 45.50", 45.5),
        ("", None),
        (None, None),
        ("Call for price", None),
        ("£0.00", None),
        ("3 for 2 offer", None),
        (12.5, 12.5),
    ],
)
def test_parse_price(raw, expected):
    assert parse_price(raw) == expected


def test_stock_from_text():
    assert stock_from_text("Out of stock - notify me") is False
    assert stock_from_text("Pre-order now") is False
    assert stock_from_text("In stock, add to basket") is True
    assert stock_from_text("Delivery 3-5 days") is None


def test_jsonld_extraction_handles_itemlist_and_bad_json():
    tree = HTMLParser(fixture("jsonld_only"))
    items = extract_jsonld(tree, "https://shop.example.co.uk/", "x", "X")
    assert [i.title for i in items] == ["Seagate Barracuda 4TB 3.5in HDD", "WD Red Plus 4TB NAS HDD"]
    assert items[0].price == 79.99 and items[0].in_stock is True
    assert items[1].price == 94.5 and items[1].in_stock is False
    assert items[1].url == "https://shop.example.co.uk/p/wd-red-4tb"
    assert items[1].image == "https://shop.example.co.uk/i/wdred.jpg"


def test_microdata_extraction():
    tree = HTMLParser(fixture("microdata_only"))
    items = extract_microdata(tree, "https://shop.example.co.uk/", "x", "X")
    assert len(items) == 2
    assert items[0].price == 179.99 and items[0].in_stock is True
    assert items[1].price == 199.99 and items[1].in_stock is False
    assert items[0].image == "https://shop.example.co.uk/i/b650.jpg"


def test_heuristic_extraction_ignores_nav_and_footer():
    tree = HTMLParser(fixture("heuristic_only"))
    items = extract_heuristic(tree, "https://shop.example.co.uk/", "x", "X")
    titles = [i.title for i in items]
    assert titles == [
        "Corsair RM850e 850W 80+ Gold Fully Modular PSU",
        "be quiet! Pure Power 12 M 850W 80+ Gold PSU",
    ]
    assert items[0].price == 104.99 and items[0].in_stock is True
    assert items[1].price == 99.95 and items[1].in_stock is False
    assert items[0].image == "https://shop.example.co.uk/i/1.jpg"


@pytest.mark.parametrize(
    "href, expected",
    [
        ("/products/x", "https://shop.example.co.uk/products/x"),
        ("//cdn.example.com/i.jpg", "https://cdn.example.com/i.jpg"),
        ("javascript:alert(1)", None),
        ("data:text/html,hi", None),
        ("JAVASCRIPT:alert(1)", None),
        ("#", None),
        ("", None),
    ],
)
def test_absolute_url_only_allows_http(href, expected):
    assert absolute_url("https://shop.example.co.uk/search", href) == expected
