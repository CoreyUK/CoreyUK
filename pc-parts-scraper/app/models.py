"""API and internal data models."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RetailerState = Literal["ok", "empty", "cached", "stale", "timeout", "blocked", "error", "disabled"]


class Listing(BaseModel):
    retailer: str
    retailer_name: str
    title: str
    price: float = Field(ge=0)
    url: str
    image: str | None = None
    in_stock: bool | None = None
    seller: str | None = None  # partner shop when a listing comes via a marketplace (e.g. NVIDIA Store)
    previous_price: float | None = None
    lowest_price: float | None = None  # lowest price ever observed for this listing
    relevance: float = 0.0


class RetailerStatus(BaseModel):
    id: str
    name: str
    state: RetailerState
    count: int = 0
    ms: int = 0
    fetched_at: float | None = None
    message: str | None = None


class SearchResponse(BaseModel):
    query: str
    listings: list[Listing]
    retailers: list[RetailerStatus]
    fetched_at: float
    ttl_seconds: int
    total: int


class RetailerInfo(BaseModel):
    id: str
    name: str
    homepage: str
    enabled: bool
