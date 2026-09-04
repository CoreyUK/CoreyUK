"""HTML extraction helpers shared by all retailers.

Extraction is tiered so a retailer keeps working even when its markup changes:

1. Site-specific CSS `Strategy` definitions (fast path, best data).
2. schema.org JSON-LD blobs (many shops embed Product / ItemList data).
3. schema.org microdata (`itemtype="...Product"`).
4. A price-anchored heuristic that finds "card" containers around £ amounts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from ..models import Listing

_PRICE_RE = re.compile(r"£\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\s?\.\s?(\d{1,2}))?(?!\d)")
_BARE_NUMBER_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?(?![\d])")
_WS_RE = re.compile(r"\s+")

_OUT_OF_STOCK_RE = re.compile(
    r"(out of stock|sold out|currently unavailable|no longer available|notify me|pre[- ]?order|coming soon|discontinued)",
    re.IGNORECASE,
)
_IN_STOCK_RE = re.compile(r"(in stock|available now|add to (basket|cart)|buy now|get it (tomorrow|by|on) )", re.IGNORECASE)


def clean_text(value: str | None) -> str:
    return _WS_RE.sub(" ", value or "").strip()


def parse_price(value: str | float | int | None) -> float | None:
    """Extract a GBP amount from text such as '£1,234.56 inc VAT' or a raw number."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    text = clean_text(str(value)).replace("\xa0", " ")
    if not text:
        return None
    match = _PRICE_RE.search(text)
    if match is None:
        # Accept bare numbers only when the whole string is basically a number
        # (data-price="123.45", content="99.99").
        stripped = text.replace("GBP", "").replace("£", "").strip()
        if not _BARE_NUMBER_RE.fullmatch(stripped):
            return None
        match = _BARE_NUMBER_RE.search(stripped)
        if match is None:
            return None
    whole = match.group(1).replace(",", "")
    pence = match.group(2) or "0"
    try:
        amount = float(f"{whole}.{pence.ljust(2, '0')}")
    except ValueError:
        return None
    return amount if amount > 0 else None


def absolute_url(base: str, href: str | None) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "#", "mailto:")):
        return None
    if href.startswith("//"):
        href = "https:" + href
    return urljoin(base, href)


def _resolve(root: Node, spec: str) -> tuple[Node | None, str | None]:
    """Resolve 'css@attr', 'css' or 'self@attr' against root -> (node, attribute or None)."""
    selector, _, attr = spec.partition("@")
    selector = selector.strip()
    node = root if selector in ("", "self") else root.css_first(selector)
    return node, (attr.strip() or None)


def _value(root: Node, specs: Iterable[str], *, want_text: bool = True) -> str | None:
    for spec in specs:
        node, attr = _resolve(root, spec)
        if node is None:
            continue
        if attr:
            value = node.attributes.get(attr)
            if attr == "src":
                # Lazy-loaded images stash the real URL in a data-* attribute and leave a
                # placeholder (blank.gif, 1x1, data: URI) in src, so prefer those first.
                for alt in ("data-src", "data-original", "data-lazy", "data-lazy-src", "data-srcset", "srcset"):
                    lazy = node.attributes.get(alt)
                    if lazy and lazy.strip():
                        value = lazy
                        break
                if value and value.strip().startswith("data:"):
                    value = None
            if value and attr in ("src", "srcset", "data-srcset"):
                value = value.split(",")[0].split()[0]
            if value and clean_text(value):
                return clean_text(value)
            continue
        if want_text:
            text = clean_text(node.text(separator=" ", strip=True))
            if text:
                return text
    return None


def _href(root: Node, specs: Iterable[str]) -> str | None:
    for spec in specs:
        node, _ = _resolve(root, spec)
        if node is None:
            continue
        if node.tag != "a":
            node = node.css_first("a[href]")
            if node is None:
                continue
        href = node.attributes.get("href")
        if href and href.strip() and not href.strip().startswith(("#", "javascript:")):
            return href.strip()
    return None


def stock_from_text(text: str) -> bool | None:
    if _OUT_OF_STOCK_RE.search(text):
        return False
    if _IN_STOCK_RE.search(text):
        return True
    return None


@dataclass(slots=True)
class Strategy:
    """Declarative description of one product-card layout."""

    item: str
    title: tuple[str, ...]
    url: tuple[str, ...] = ("a[href]",)
    price: tuple[str, ...] = (".price",)
    image: tuple[str, ...] = ("img@src",)
    out_of_stock: tuple[str, ...] = ()
    in_stock: tuple[str, ...] = ()
    min_items: int = 1
    notes: str = field(default="")


def extract_by_strategy(tree: HTMLParser, strategy: Strategy, base_url: str, retailer_id: str, retailer_name: str) -> list[Listing]:
    listings: list[Listing] = []
    for item in tree.css(strategy.item):
        title = _value(item, strategy.title)
        href = _href(item, strategy.url) or _href(item, ("a[href]",))
        price = parse_price(_value(item, strategy.price))
        if not title or not href or price is None or len(title) < 3:
            continue
        url = absolute_url(base_url, href)
        if not url:
            continue
        image = absolute_url(base_url, _value(item, strategy.image, want_text=False))
        in_stock: bool | None = None
        if strategy.out_of_stock and any(item.css_first(sel) is not None for sel in strategy.out_of_stock):
            in_stock = False
        elif strategy.in_stock and any(item.css_first(sel) is not None for sel in strategy.in_stock):
            in_stock = True
        else:
            in_stock = stock_from_text(item.text(separator=" ", strip=True))
        listings.append(
            Listing(retailer=retailer_id, retailer_name=retailer_name, title=title, price=price, url=url, image=image, in_stock=in_stock)
        )
    return listings if len(listings) >= strategy.min_items else []


# ----- JSON-LD ---------------------------------------------------------------------
def _iter_products(obj: Any) -> Iterable[dict[str, Any]]:
    if isinstance(obj, dict):
        types = obj.get("@type")
        type_list = types if isinstance(types, list) else [types]
        if any(isinstance(t, str) and t.lower() == "product" for t in type_list):
            yield obj
        for key, value in obj.items():
            if key in ("@context",):
                continue
            yield from _iter_products(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_products(value)


def _offer_price(product: dict[str, Any]) -> tuple[float | None, bool | None]:
    offers = product.get("offers")
    candidates = offers if isinstance(offers, list) else [offers]
    for offer in candidates:
        if not isinstance(offer, dict):
            continue
        raw = offer.get("price") or offer.get("lowPrice")
        if raw is None and isinstance(offer.get("priceSpecification"), dict):
            raw = offer["priceSpecification"].get("price")
        price = parse_price(str(raw)) if raw is not None else None
        availability = str(offer.get("availability") or "")
        in_stock: bool | None = None
        if availability:
            in_stock = "instock" in availability.lower().replace(" ", "")
        if price is not None:
            return price, in_stock
    return None, None


def extract_jsonld(tree: HTMLParser, base_url: str, retailer_id: str, retailer_name: str) -> list[Listing]:
    listings: list[Listing] = []
    for script in tree.css('script[type="application/ld+json"]'):
        raw = script.text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for product in _iter_products(data):
            name = clean_text(str(product.get("name") or ""))
            url = absolute_url(base_url, product.get("url") or product.get("@id") or "")
            price, in_stock = _offer_price(product)
            if not name or not url or price is None:
                continue
            image = product.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            if isinstance(image, dict):
                image = image.get("url")
            listings.append(
                Listing(
                    retailer=retailer_id,
                    retailer_name=retailer_name,
                    title=name,
                    price=price,
                    url=url,
                    image=absolute_url(base_url, image if isinstance(image, str) else None),
                    in_stock=in_stock,
                )
            )
    return listings


# ----- Microdata -------------------------------------------------------------------
_MICRODATA_STRATEGY = Strategy(
    item='[itemtype*="schema.org/Product"]',
    title=('[itemprop="name"]@content', '[itemprop="name"]', "a@title"),
    url=('[itemprop="url"]', "a[href]"),
    price=('[itemprop="price"]@content', '[itemprop="price"]', '[itemprop="lowPrice"]@content'),
    image=('[itemprop="image"]@src', '[itemprop="image"]@content', "img@src"),
    out_of_stock=('[href*="OutOfStock"]', '[content*="OutOfStock"]'),
    in_stock=('[href*="InStock"]', '[content*="InStock"]'),
)


def extract_microdata(tree: HTMLParser, base_url: str, retailer_id: str, retailer_name: str) -> list[Listing]:
    return extract_by_strategy(tree, _MICRODATA_STRATEGY, base_url, retailer_id, retailer_name)


# ----- Heuristic -------------------------------------------------------------------
_SKIP_TAGS = {"script", "style", "noscript", "template", "header", "footer", "nav"}


def _price_count(node: Node) -> int:
    return len(_PRICE_RE.findall(node.text(separator=" ")))


def extract_heuristic(tree: HTMLParser, base_url: str, retailer_id: str, retailer_name: str, *, max_items: int = 60) -> list[Listing]:
    """Find product cards by walking up from '£' text nodes to the nearest container with a product link."""
    listings: list[Listing] = []
    seen: set[str] = set()
    for text_node in tree.root.traverse(include_text=True) if tree.root else []:
        if text_node.tag != "-text":
            continue
        text = text_node.text()
        if "£" not in text or _PRICE_RE.search(text) is None:
            continue
        node = text_node.parent
        depth = 0
        while node is not None and depth < 7 and node.tag not in ("body", "html"):
            if node.tag in _SKIP_TAGS:
                break
            anchor = None
            alt_anchor = None
            for a in node.css("a[href]"):
                label = clean_text(a.text(separator=" ")) or clean_text(a.attributes.get("title") or "")
                if len(label) >= 12 and "£" not in label:
                    anchor = (a, label)
                    break
                if alt_anchor is None:
                    img = a.css_first("img[alt]")
                    alt = clean_text(img.attributes.get("alt") or "") if img is not None else ""
                    if len(alt) >= 12 and "£" not in alt:
                        alt_anchor = (a, alt)
            anchor = anchor or alt_anchor
            if anchor is not None:
                if _price_count(node) > 3:
                    break  # container is too big to be one product card
                a, label = anchor
                url = absolute_url(base_url, a.attributes.get("href"))
                price = parse_price(text)
                if url and price is not None and url not in seen:
                    seen.add(url)
                    img = node.css_first("img")
                    image = absolute_url(base_url, _value(img, ("self@src",), want_text=False)) if img is not None else None
                    listings.append(
                        Listing(
                            retailer=retailer_id,
                            retailer_name=retailer_name,
                            title=label,
                            price=price,
                            url=url,
                            image=image,
                            in_stock=stock_from_text(node.text(separator=" ")),
                        )
                    )
                break
            node = node.parent
            depth += 1
        if len(listings) >= max_items:
            break
    return listings


def dedupe(listings: Iterable[Listing]) -> list[Listing]:
    seen: set[str] = set()
    result: list[Listing] = []
    for item in listings:
        key = item.url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
