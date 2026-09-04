# PartsPrice UK

Search UK PC component retailers in one place and see who has the part cheapest.
Type "rtx 5070", "2tb nvme" or "ryzen 7 9800x3d", get one sorted price list back,
with the retailer, stock status and a link straight to the product.

![screenshot](docs/screenshot.png)

## Retailers

| id | Retailer | Notes |
|----|----------|-------|
| `scan` | [Scan](https://www.scan.co.uk) | Imperva bot protection; may show as *blocked* from datacentre IPs |
| `overclockers` | [Overclockers UK](https://www.overclockers.co.uk) | |
| `ebuyer` | [Ebuyer](https://www.ebuyer.com) | |
| `ccl` | [CCL Computers](https://www.cclonline.com) | |
| `novatech` | [Novatech](https://www.novatech.co.uk) | |
| `awd_it` | [AWD-IT](https://www.awd-it.co.uk) | Magento storefront |
| `box` | [Box](https://www.box.co.uk) | |
| `currys` | [Currys](https://www.currys.co.uk) | Akamai bot manager; often blocks non-browser clients |
| `newegg` | [Newegg UK](https://www.newegg.com/global/uk-en/) | GBP storefront; includes marketplace sellers |
| `amazon` | [Amazon UK](https://www.amazon.co.uk) | Blocks most non-browser traffic, near-certain from cloud IPs. Works best from a home connection; the supported route is Amazon's Product Advertising API |

Argos is left out: results render client-side behind bot protection, so it would
only ever show as "blocked". To drop any retailer set `ENABLED_RETAILERS`, e.g.
`ENABLED_RETAILERS=scan,overclockers,ebuyer,ccl,novatech,awd_it,box,newegg`.

## Quick start

```bash
cd pc-parts-scraper
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m app
```

Open <http://localhost:8000>. API docs are at `/api/docs`.

Or with Docker:

```bash
docker compose up --build
```

## Check the scrapers against the live sites

Retail sites change their markup without warning, so before relying on it run the
probe. It queries every retailer once and tells you which extraction tier each one
resolved through:

```bash
python -m scripts.probe "rtx 4070"
python -m scripts.probe "1tb nvme" --retailers scan,ebuyer --dump data/dumps -v
```

```
retailer      status    items   secs  tier / message
------------------------------------------------------------------------------
scan          ok           24    1.3  css:li.product[data-price]
overclockers  ok           30    0.9  css:.product-box
ebuyer        ok           18    1.1  jsonld
currys        blocked       0    0.4  www.currys.co.uk returned 403 (bot protection?)
```

* `css:*` means the site-specific selectors matched (best quality).
* `jsonld` / `microdata` means the selectors did not match but the shop embeds
  schema.org data, which is used instead.
* `heuristic` means results came from the generic "find a price, walk up to the
  product card" fallback. Usually fine, but worth tightening the selectors.
* `empty` with `--dump` lets you open the saved HTML and update the selectors in
  `app/scrapers/retailers.py`.

## How it works

```
browser ──> FastAPI (/api/search) ──> SearchService
                                        ├─ per-retailer cache (SQLite, TTL)
                                        ├─ single-flight: identical concurrent searches share one fetch
                                        └─ asyncio fan-out ──> Fetcher (httpx, HTTP/2)
                                                                ├─ global concurrency cap
                                                                ├─ per-host concurrency + minimum interval
                                                                ├─ retries with backoff
                                                                └─ bot-challenge detection
                                              each retailer ──> tiered parser (css → json-ld → microdata → heuristic)
```

* **Fast**: all retailers are queried concurrently; a slow or blocked shop cannot hold
  up the others. Results parse with `selectolax` (C-backed) and the whole response is gzip'd.
* **Up to date**: results are cached per retailer for `CACHE_TTL_SECONDS` (15 minutes
  by default). The Refresh button forces a live fetch, rate-limited to once a minute
  per query. When a refresh fails the last good results are shown and marked *stale*.
* **Limits**: outbound requests are throttled per host so no retailer sees a burst,
  inbound searches are limited per client IP (`429` with `Retry-After`), query length
  and per-retailer result counts are capped.
* **Price history**: every fresh fetch records price observations, so a listing shows
  "▼ £20 was £539.99" when a price has moved since it was last seen. `/api/history`
  returns the observations for a product URL.

## Configuration

Copy `.env.example` to `.env`. Everything is optional.

| Variable | Default | Meaning |
|----------|---------|---------|
| `CACHE_TTL_SECONDS` | `900` | How long results count as fresh |
| `RETAILER_TIMEOUT_SECONDS` | `12` | Per-retailer request timeout |
| `GLOBAL_MAX_CONCURRENCY` | `16` | Max in-flight outbound requests |
| `PER_HOST_MAX_CONCURRENCY` | `2` | Max in-flight requests to one retailer |
| `PER_HOST_MIN_INTERVAL_SECONDS` | `1.0` | Minimum spacing between requests to one retailer |
| `MAX_RESULTS_PER_RETAILER` | `40` | Cap per retailer per query |
| `API_RATE_LIMIT_PER_MINUTE` | `30` | Searches allowed per client IP per minute |
| `FORCE_REFRESH_MIN_INTERVAL_SECONDS` | `60` | Minimum gap between forced refreshes of one query |
| `MAX_QUERY_LENGTH` | `80` | Longest accepted search |
| `ENABLED_RETAILERS` | `all` | Comma-separated retailer ids to enable |
| `DEBUG_DUMP_DIR` | | Save every fetched page here (debugging) |

## API

* `GET /api/search?q=rtx+4070&retailers=scan,ebuyer&refresh=1`
* `GET /api/retailers`
* `GET /api/categories`
* `GET /api/history?retailer=scan&url=...`
* `GET /api/health`

## Adding a retailer

Add a `Retailer(...)` to `app/scrapers/retailers.py` with the search URL template and
one or more `Strategy` blocks describing the product card (`item` selector, then
selectors for title, url, price, image and stock). Selectors accept `css@attr` to read
an attribute, and `self@attr` to read the card element itself. Append it to `RETAILERS`,
drop a saved search page into `tests/fixtures/<id>.html`, and add it to `EXPECTED` in
`tests/test_scrapers.py`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite runs entirely offline against saved HTML in `tests/fixtures/`.

## Being a good citizen

The scraper identifies as a normal browser, respects per-host limits, caches
aggressively and never hammers a shop. Keep the limits sensible if you host it
publicly. Prices are shown for comparison only. Always confirm on the retailer's site.
