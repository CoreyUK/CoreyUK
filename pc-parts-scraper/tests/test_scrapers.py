import pytest

from app.scrapers import RETAILERS, get_retailer
from tests.conftest import fixture

EXPECTED = {
    "scan": ("Samsung 990 PRO 1TB M.2 NVMe PCIe 4.0 SSD", 119.99, True, "https://www.scan.co.uk/products/1tb-samsung-990-pro"),
    "overclockers": ("Kingston FURY Renegade 1TB PCIe 4.0 NVMe SSD", 89.99, True, "https://www.overclockers.co.uk/kingston-fury-renegade-1tb-nvme.html"),
    "ebuyer": ("Crucial P3 Plus 1TB PCIe 4.0 NVMe M.2 SSD", 59.99, True, "https://www.ebuyer.com/1234567-crucial-p3-plus-1tb"),
    "ccl": ("AMD Ryzen 7 7800X3D 8 Core AM5 Processor", 339.98, True, "https://www.cclonline.com/product/12345/AMD-Ryzen-7-7800X3D"),
    "novatech": ("Intel Core i5 14600K 14 Core LGA1700 Processor", 239.99, True, "https://www.novatech.co.uk/products/intel-core-i5-14600k/bx8071514600k.html"),
    "awd_it": ("MSI GeForce RTX 4070 VENTUS 2X 12GB Graphics Card", 519.99, True, "https://www.awd-it.co.uk/msi-geforce-rtx-4070-ventus-2x-12gb.html"),
    "box": ("Corsair Vengeance 32GB (2x16GB) DDR5 6000MHz CL30 Memory Kit", 104.99, True, "https://www.box.co.uk/corsair-vengeance-32gb-ddr5-6000_3456789.html"),
    "currys": ("SAMSUNG 980 Pro Internal SSD - 1 TB", 99.0, True, "https://www.currys.co.uk/products/samsung-980-pro-1tb-10250001.html"),
    "newegg": ("Samsung 990 PRO 2TB PCIe 4.0 NVMe M.2 SSD MZ-V9P2T0BW", 149.99, True, "https://www.newegg.com/global/uk-en/samsung-990-pro-2tb/p/N82E16820147876"),
    "amazon": ("Samsung 990 PRO 1TB PCIe 4.0 NVMe M.2 Internal SSD, Up to 7,450 MB/s", 84.99, True, "https://www.amazon.co.uk/Samsung-990-PRO-Internal-MZ-V9P1T0BW/dp/B0BHJJ9Y77/"),
}


@pytest.mark.parametrize("retailer_id", list(EXPECTED))
def test_each_retailer_parses_its_fixture_via_css(retailer_id):
    retailer = get_retailer(retailer_id)
    assert retailer is not None
    listings, tier = retailer.parse(fixture(retailer_id))
    assert tier.startswith("css:"), f"{retailer_id} fell through to {tier}"
    assert len(listings) == 2
    title, price, in_stock, url = EXPECTED[retailer_id]
    first = listings[0]
    assert first.title == title
    assert first.price == price
    assert first.in_stock is in_stock
    assert first.url == url
    assert listings[1].in_stock in (False, None)
    assert all(l.retailer == retailer_id for l in listings)


def test_scan_second_item_uses_lazy_image_and_out_of_stock():
    listings, _ = get_retailer("scan").parse(fixture("scan"))
    second = listings[1]
    assert second.price == 1249.98
    assert second.in_stock is False
    assert second.image == "https://www.scan.co.uk/images/products/3659999-b.jpg"


def test_awd_it_uses_special_price_when_present():
    listings, _ = get_retailer("awd_it").parse(fixture("awd_it"))
    assert listings[1].price == 579.0
    assert listings[1].in_stock is False


@pytest.mark.parametrize("retailer", RETAILERS, ids=lambda r: r.id)
def test_retailers_fall_back_to_jsonld_and_heuristics(retailer):
    listings, tier = retailer.parse(fixture("jsonld_only"))
    assert tier == "jsonld" and len(listings) == 2
    listings, tier = retailer.parse(fixture("heuristic_only"))
    assert tier == "heuristic" and len(listings) == 2
    listings, tier = retailer.parse("<html><body><p>No results found for your search.</p></body></html>")
    assert tier == "none" and listings == []


def test_search_urls_encode_queries():
    for retailer in RETAILERS:
        url = retailer.search_url("rtx 4070 ti & more")
        assert url.startswith(retailer.homepage)
        assert " " not in url and "&+" not in url
        assert "rtx+4070+ti" in url
