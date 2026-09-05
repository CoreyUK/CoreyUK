"""Column mappings for the feed formats worth supporting out of the box.

A profile says which CSV column holds each field we care about. Several candidate
names are listed per field because networks and merchants vary; the first column
present in the file wins. `detect` picks a profile from the header row, so most
feeds need no configuration at all.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(slots=True, frozen=True)
class Profile:
    name: str
    title: tuple[str, ...]
    price: tuple[str, ...]
    url: tuple[str, ...]
    external_id: tuple[str, ...]
    image: tuple[str, ...] = ()
    retailer_name: tuple[str, ...] = ()
    in_stock: tuple[str, ...] = ()
    stock_quantity: tuple[str, ...] = ()
    brand: tuple[str, ...] = ()
    mpn: tuple[str, ...] = ()
    ean: tuple[str, ...] = ()
    category: tuple[str, ...] = ()
    delivery_cost: tuple[str, ...] = ()
    currency: tuple[str, ...] = ()
    signature: tuple[str, ...] = field(default=())  # columns that identify this profile

    def fields(self) -> dict[str, tuple[str, ...]]:
        return {
            "title": self.title,
            "price": self.price,
            "url": self.url,
            "external_id": self.external_id,
            "image": self.image,
            "retailer_name": self.retailer_name,
            "in_stock": self.in_stock,
            "stock_quantity": self.stock_quantity,
            "brand": self.brand,
            "mpn": self.mpn,
            "ean": self.ean,
            "category": self.category,
            "delivery_cost": self.delivery_cost,
            "currency": self.currency,
        }


# Awin's Create-a-Feed export. Column names are Awin's documented ones; a feed built
# with a subset of columns still works as long as title, price and a link are present.
AWIN = Profile(
    name="awin",
    title=("product_name", "product_short_description"),
    price=("search_price", "store_price", "display_price", "product_price"),
    url=("aw_deep_link", "merchant_deep_link", "deep_link"),
    external_id=("merchant_product_id", "aw_product_id", "product_id"),
    image=("merchant_image_url", "aw_image_url", "large_image", "image_url"),
    retailer_name=("merchant_name",),
    in_stock=("in_stock", "is_for_sale"),
    stock_quantity=("stock_quantity",),
    brand=("brand_name", "manufacturer", "brand"),
    mpn=("mpn", "model_number", "manufacturer_part_number"),
    ean=("ean", "gtin", "product_GTIN"),
    category=("merchant_category", "category_name", "product_type"),
    delivery_cost=("delivery_cost", "delivery_charges"),
    currency=("currency",),
    signature=("aw_deep_link", "aw_product_id", "merchant_product_id"),
)

# Google Shopping / Merchant Center style, which several networks and merchants emit.
GOOGLE = Profile(
    name="google",
    title=("title",),
    price=("price", "sale_price"),
    url=("link", "product_link"),
    external_id=("id", "item_group_id"),
    image=("image_link", "additional_image_link"),
    retailer_name=("merchant",),
    in_stock=("availability",),
    brand=("brand",),
    mpn=("mpn",),
    ean=("gtin",),
    category=("product_type", "google_product_category"),
    delivery_cost=("shipping",),
    currency=("currency",),
    signature=("image_link", "availability"),
)

PROFILES: dict[str, Profile] = {p.name: p for p in (AWIN, GOOGLE)}


def detect(headers: Iterable[str]) -> Profile:
    """Choose the profile whose signature columns are present; falls back to Awin."""
    present = {h.strip().lower() for h in headers}
    best, best_score = AWIN, 0
    for profile in PROFILES.values():
        score = sum(1 for column in profile.signature if column.lower() in present)
        # A profile only counts if it can actually find a title, price and link.
        usable = all(any(c.lower() in present for c in group) for group in (profile.title, profile.price, profile.url))
        if usable and score > best_score:
            best, best_score = profile, score
    return best
