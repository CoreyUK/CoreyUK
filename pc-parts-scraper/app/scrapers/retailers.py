"""UK PC component retailers.

Selectors are written against each shop's known product-card markup with several
fallbacks per shop, and every retailer also falls through to JSON-LD, microdata and
the price heuristic (see utils.py). Use `python -m scripts.probe "rtx 4070"` to see
which tier each retailer is currently resolving through and fix selectors if needed.
"""
from __future__ import annotations

from .base import Retailer
from .utils import Strategy

# Generic card selectors used as trailing fallbacks for most shops.
_GENERIC_TITLE = (
    "h2 a@title", "h3 a@title", "h2 a", "h3 a", "h4 a", ".product-title a", ".product-name a",
    ".product-title", ".product-name", "a.product-link@title", "a@title", "img@alt",
)
_GENERIC_PRICE = (
    "[data-price-amount]@data-price-amount", "[itemprop=price]@content", ".price-inc", ".price--inc-vat",
    ".product-price", ".price-current", ".price-now", ".now-price", ".price", "[class*=price]",
)
_GENERIC_IMAGE = ("img@src",)
_GENERIC_OOS = (".out-of-stock", ".outofstock", ".stock-out", ".unavailable", ".sold-out")
_GENERIC_INSTOCK = (".in-stock", ".instock", ".stock-in", ".available")


def _generic(item: str) -> Strategy:
    return Strategy(
        item=item,
        title=_GENERIC_TITLE,
        url=("h2 a", "h3 a", ".product-title a", ".product-name a", "a[href]"),
        price=_GENERIC_PRICE,
        image=_GENERIC_IMAGE,
        out_of_stock=_GENERIC_OOS,
        in_stock=_GENERIC_INSTOCK,
        min_items=2,
    )


SCAN = Retailer(
    id="scan",
    name="Scan",
    homepage="https://www.scan.co.uk/",
    search_template="https://www.scan.co.uk/search?q={q}",
    strategies=[
        Strategy(
            item="li.product[data-price]",
            title=("self@data-description", ".description h2 a", ".description a", "a@title"),
            url=(".description a[href]", "a[href*='/products/']", "a[href]"),
            price=("self@data-price", ".price span.price", ".price"),
            image=(".image img@src", "img@src"),
            out_of_stock=(".buyButton .notify", ".buyButton .preorder", ".stock .outofstock"),
            in_stock=(".buyButton a.buy", ".buyButton .buy"),
        ),
        Strategy(
            item="li.product",
            title=(".description h2 a", ".description a", "self@data-description", "a@title"),
            url=(".description a[href]", "a[href]"),
            price=(".price span.price", ".price", "self@data-price"),
            image=(".image img@src", "img@src"),
        ),
        _generic(".productColumns li"),
    ],
    notes="Scan uses Imperva/Incapsula bot protection; a blocked status means the challenge page was served.",
)

OVERCLOCKERS = Retailer(
    id="overclockers",
    name="Overclockers UK",
    homepage="https://www.overclockers.co.uk/",
    search_template="https://www.overclockers.co.uk/search?search={q}&query={q}",
    strategies=[
        # Shopware 6 style
        Strategy(
            item=".product-box",
            title=("a.product-name@title", ".product-name", "a.product-image-link@title"),
            url=("a.product-name", "a.product-image-link", "a[href]"),
            price=(".product-price", ".product-price-wrapper", ".price"),
            image=("img.product-image@src", "img@src"),
            out_of_stock=(".delivery-not-available", ".delivery-unavailable"),
            in_stock=(".delivery-available",),
        ),
        # Shopware 5 style
        Strategy(
            item=".product--box",
            title=(".product--title@title", ".product--title"),
            url=(".product--title", "a.product--image", "a[href]"),
            price=(".price--default", ".product--price", ".price"),
            image=(".product--image img@src", "img@src"),
            out_of_stock=(".delivery--text-not-available",),
            in_stock=(".delivery--text-available",),
        ),
        _generic(".product-item"),
        _generic("[data-product-id]"),
    ],
)

EBUYER = Retailer(
    id="ebuyer",
    name="Ebuyer",
    homepage="https://www.ebuyer.com/",
    search_template="https://www.ebuyer.com/search?q={q}",
    strategies=[
        Strategy(
            item=".grid-item",
            title=(".grid-item__title a", ".grid-item__title", "h3 a", "a@title"),
            url=(".grid-item__title a", "h3 a", "a[href]"),
            price=(".grid-item__price", "p.price", ".price"),
            image=("img@src",),
            out_of_stock=(".grid-item__stock--out", ".stock-out", ".out-of-stock"),
            in_stock=(".grid-item__stock--in", ".in-stock"),
        ),
        Strategy(
            item=".listing-product",
            title=(".listing-product__title a", ".listing-product__title", "h3 a"),
            url=(".listing-product__title a", "h3 a", "a[href]"),
            price=(".listing-product__price", ".price"),
            image=("img@src",),
        ),
        _generic(".product-listing__item"),
        _generic("[data-product-id]"),
    ],
)

CCL = Retailer(
    id="ccl",
    name="CCL Computers",
    homepage="https://www.cclonline.com/",
    search_template="https://www.cclonline.com/search/?q={q}",
    strategies=[
        Strategy(
            item=".product-listing-item, .product-list__item, .product-tile",
            title=(".product-title a", ".product-name a", "h2 a", "h3 a", "a@title"),
            url=(".product-title a", ".product-name a", "h2 a", "h3 a", "a[href]"),
            price=(".price-inc", ".product-price .price", ".product-price", ".price"),
            image=("img@src",),
            out_of_stock=_GENERIC_OOS,
            in_stock=_GENERIC_INSTOCK,
        ),
        _generic(".product-item"),
        _generic("[data-product-id]"),
    ],
)

NOVATECH = Retailer(
    id="novatech",
    name="Novatech",
    homepage="https://www.novatech.co.uk/",
    search_template="https://www.novatech.co.uk/search/?q={q}",
    strategies=[
        Strategy(
            item=".product-box, .product-listing-item, article.product",
            title=(".product-title a", ".product-name", "h3 a", "h2 a", "a@title"),
            url=(".product-title a", "h3 a", "h2 a", "a[href]"),
            price=(".product-price .price", ".product-price", ".price-inc", ".price"),
            image=("img@src",),
            out_of_stock=_GENERIC_OOS,
            in_stock=_GENERIC_INSTOCK,
        ),
        _generic(".product-item"),
        _generic("[data-product-id]"),
    ],
)

AWD_IT = Retailer(
    id="awd_it",
    name="AWD-IT",
    homepage="https://www.awd-it.co.uk/",
    search_template="https://www.awd-it.co.uk/catalogsearch/result/?q={q}",
    strategies=[
        # Magento 2 product grid
        Strategy(
            item="li.product-item",
            title=("a.product-item-link", ".product-item-name a", ".product-item-name"),
            url=("a.product-item-link", "a.product-item-photo", "a[href]"),
            price=(
                ".price-final_price [data-price-amount]@data-price-amount",
                ".special-price .price",
                ".price-box .price",
                ".price",
            ),
            image=("img.product-image-photo@src", "img@src"),
            out_of_stock=(".stock.unavailable",),
            in_stock=(".stock.available",),
        ),
        _generic(".product-item"),
    ],
)

BOX = Retailer(
    id="box",
    name="Box",
    homepage="https://www.box.co.uk/",
    search_template="https://www.box.co.uk/search?q={q}",
    strategies=[
        Strategy(
            item=".product-list-item, .product-list__item, .product-tile",
            title=(".product-list-item-title a", ".product-list-item-title", "h3 a", "h2 a", "a@title"),
            url=(".product-list-item-title a", "h3 a", "h2 a", "a[href]"),
            price=(".price-value", ".pq-price", ".product-price", ".price"),
            image=("img@src",),
            out_of_stock=_GENERIC_OOS,
            in_stock=_GENERIC_INSTOCK,
        ),
        _generic(".product-item"),
        _generic("[data-product-id]"),
    ],
)

CURRYS = Retailer(
    id="currys",
    name="Currys",
    homepage="https://www.currys.co.uk/",
    search_template="https://www.currys.co.uk/search?q={q}",
    strategies=[
        # Salesforce Commerce Cloud tiles
        Strategy(
            item=".product-tile, .product-item-element",
            title=(".pdp-grid-product-name", ".product-name a", "a.link", "h2 a", "a@title"),
            url=(".pdp-grid-product-link", ".product-name a", "a.link", "a[href]"),
            price=(".product-tile-price .value@content", ".price .value@content", ".sales .value@content", ".price"),
            image=("img.tile-image@src", "img@src"),
            out_of_stock=(".out-of-stock", ".unavailable"),
            in_stock=(".in-stock",),
        ),
        _generic("[data-pid]"),
    ],
    notes="Currys sits behind Akamai Bot Manager and frequently blocks non-browser clients.",
)

NEWEGG = Retailer(
    id="newegg",
    name="Newegg UK",
    homepage="https://www.newegg.com/global/uk-en/",
    search_template="https://www.newegg.com/global/uk-en/p/pl?d={q}",
    strategies=[
        Strategy(
            item=".item-cell",
            title=("a.item-title", ".item-title"),
            url=("a.item-title", "a.item-img", "a[href]"),
            price=(".price-current", ".item-price", ".price"),
            image=("a.item-img img@src", "img@src"),
            out_of_stock=(".item-promo",),  # Newegg only renders this banner for OUT OF STOCK
            in_stock=(".item-button-area .btn-primary", ".item-button-area .btn"),
        ),
        _generic(".item-container"),
    ],
    notes="Newegg's UK storefront lists prices in GBP; marketplace sellers are included.",
)

AMAZON = Retailer(
    id="amazon",
    name="Amazon UK",
    homepage="https://www.amazon.co.uk/",
    search_template="https://www.amazon.co.uk/s?k={q}&i=computers",
    strategies=[
        Strategy(
            item='div[data-component-type="s-search-result"]',
            title=("h2 a span", "h2 span", "h2 a@aria-label", "h2"),
            url=("h2 a", "a.a-link-normal.s-no-outline", "a.a-link-normal[href*='/dp/']", "a[href]"),
            price=(".a-price .a-offscreen", ".a-price", ".a-color-price"),
            image=("img.s-image@src", "img@src"),
            out_of_stock=(".s-item-unavailable",),
            in_stock=(".a-price .a-offscreen",),  # priced and not flagged unavailable = buyable
        ),
    ],
    notes=(
        "Amazon aggressively blocks non-browser clients, especially from datacentre IPs. "
        "Expect 'blocked' unless requests come from a residential connection; the supported "
        "route is Amazon's Product Advertising API."
    ),
)

RETAILERS: list[Retailer] = [SCAN, OVERCLOCKERS, EBUYER, CCL, NOVATECH, AWD_IT, BOX, CURRYS, NEWEGG, AMAZON]
