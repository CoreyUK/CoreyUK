"""Retailer definition and the tiered parse pipeline."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import quote_plus

from selectolax.parser import HTMLParser

from ..fetcher import Fetcher
from ..models import Listing
from .utils import Strategy, dedupe, extract_by_strategy, extract_heuristic, extract_jsonld, extract_microdata

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Retailer:
    id: str
    name: str
    homepage: str
    search_template: str  # must contain {q}
    strategies: list[Strategy] = field(default_factory=list)
    allow_heuristic: bool = True
    notes: str = ""

    def search_url(self, query: str) -> str:
        return self.search_template.format(q=quote_plus(query))

    def parse(self, html: str) -> tuple[list[Listing], str]:
        """Parse a search results page. Returns (listings, extraction tier name)."""
        tree = HTMLParser(html)
        for strategy in self.strategies:
            items = extract_by_strategy(tree, strategy, self.homepage, self.id, self.name)
            if items:
                return dedupe(items), f"css:{strategy.item}"
        items = extract_jsonld(tree, self.homepage, self.id, self.name)
        if items:
            return dedupe(items), "jsonld"
        items = extract_microdata(tree, self.homepage, self.id, self.name)
        if items:
            return dedupe(items), "microdata"
        if self.allow_heuristic:
            items = extract_heuristic(tree, self.homepage, self.id, self.name)
            if items:
                return dedupe(items), "heuristic"
        return [], "none"

    async def search(self, fetcher: Fetcher, query: str) -> tuple[list[Listing], str]:
        html = await fetcher.get(self.search_url(query), dump_name=f"{self.id}_{query}")
        listings, tier = self.parse(html)
        log.info("%s: %d listings via %s for %r", self.id, len(listings), tier, query)
        return listings, tier
