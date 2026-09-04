"""Retailer registry. Add a new shop by appending a `Retailer` to RETAILERS."""
from __future__ import annotations

from ..config import Settings
from .base import Retailer
from .retailers import RETAILERS

__all__ = ["RETAILERS", "Retailer", "enabled_retailers", "get_retailer"]

_BY_ID = {r.id: r for r in RETAILERS}


def get_retailer(retailer_id: str) -> Retailer | None:
    return _BY_ID.get(retailer_id)


def enabled_retailers(settings: Settings) -> list[Retailer]:
    return [r for r in RETAILERS if settings.retailer_enabled(r.id)]
