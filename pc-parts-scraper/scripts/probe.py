"""Live diagnostic: run one query against every retailer and report what came back.

    python -m scripts.probe "rtx 4070"
    python -m scripts.probe "1tb nvme" --retailers scan,ebuyer --dump data/dumps

`--dump DIR` saves each raw HTML response so you can inspect the markup and fix
selectors in app/scrapers/retailers.py when a shop changes its layout.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from app.config import Settings
from app.fetcher import BlockedError, FetchError, Fetcher
from app.scrapers import RETAILERS


async def probe(query: str, ids: set[str] | None, dump: str, verbose: bool) -> None:
    settings = Settings.from_env()
    if dump:
        settings.debug_dump_dir = dump
    fetcher = Fetcher(settings)
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING)
    try:
        chosen = [r for r in RETAILERS if not ids or r.id in ids]

        async def one(retailer):  # type: ignore[no-untyped-def]
            started = time.monotonic()
            try:
                items, tier = await asyncio.wait_for(retailer.search(fetcher, query), timeout=settings.retailer_timeout_seconds + 1)
                return retailer, "ok" if items else "empty", tier, items, None, time.monotonic() - started
            except asyncio.TimeoutError:
                return retailer, "timeout", "-", [], "timed out", time.monotonic() - started
            except BlockedError as exc:
                return retailer, "blocked", "-", [], str(exc), time.monotonic() - started
            except FetchError as exc:
                return retailer, "error", "-", [], str(exc), time.monotonic() - started
            except Exception as exc:  # noqa: BLE001
                return retailer, "crash", "-", [], f"{exc.__class__.__name__}: {exc}", time.monotonic() - started

        results = await asyncio.gather(*(one(r) for r in chosen))
        print(f"\nQuery: {query!r}\n")
        print(f"{'retailer':<14}{'status':<9}{'items':>6}  {'secs':>5}  tier / message")
        print("-" * 78)
        for retailer, status, tier, items, message, secs in results:
            print(f"{retailer.id:<14}{status:<9}{len(items):>6}  {secs:>5.1f}  {message or tier}")
        for retailer, status, tier, items, message, secs in results:
            if items:
                print(f"\n{retailer.name} ({tier}):")
                for item in items[:5]:
                    stock = {True: "in stock", False: "out of stock", None: "stock ?"}[item.in_stock]
                    print(f"  £{item.price:>9.2f}  {stock:<13} {item.title[:70]}")
                    if verbose:
                        print(f"             {item.url}")
        if dump:
            print(f"\nRaw HTML saved under {dump}/")
    finally:
        await fetcher.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query")
    parser.add_argument("--retailers", help="comma-separated retailer ids (default: all)")
    parser.add_argument("--dump", default="", help="directory to save raw HTML responses")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    ids = {r.strip().lower() for r in args.retailers.split(",")} if args.retailers else None
    asyncio.run(probe(args.query, ids, args.dump, args.verbose))


if __name__ == "__main__":
    main()
