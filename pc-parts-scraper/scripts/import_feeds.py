"""Download affiliate product feeds and load them into the local database.

    python -m scripts.import_feeds                   # every feed in feeds.json
    python -m scripts.import_feeds --source awin     # just one
    python -m scripts.import_feeds --file sample.csv --id awin --dry-run
    python -m scripts.import_feeds --list            # what is currently loaded

Run it once a day (see deploy/README.md for a cron line). The app notices new data
within a minute; no restart needed.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from app.config import Settings
from app.feeds import FeedConfig, FeedImporter, FeedStore, load_feed_configs


def _ago(ts: float) -> str:
    seconds = max(0, time.time() - ts)
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to feeds.json (default: FEEDS_CONFIG or ./feeds.json)")
    parser.add_argument("--source", help="only import the feed with this id")
    parser.add_argument("--file", help="import a local CSV instead of downloading (needs --id)")
    parser.add_argument("--id", help="feed id to use with --file")
    parser.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    parser.add_argument("--list", action="store_true", help="show what is loaded and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env()
    store = FeedStore(settings.cache_db_path)

    if args.list:
        stats = store.stats_sync()
        if not stats:
            print("No feeds imported yet.")
            return 0
        print(f"{'feed':<20}{'products':>10}{'skipped':>10}  imported")
        for row in stats:
            when = datetime.fromtimestamp(row["imported_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            print(f"{row['source']:<20}{row['rows']:>10}{row['skipped']:>10}  {when} ({_ago(row['imported_at'])})")
        print("\nRetailers available from feeds:")
        for retailer_id, name in store.retailers_sync():
            print(f"  {retailer_id:<20}{name}")
        return 0

    if args.file:
        if not args.id:
            parser.error("--file needs --id, e.g. --file sample.csv --id awin")
        configs = [FeedConfig(id=args.id, path=args.file)]
    else:
        path = args.config or settings.feeds_config
        configs = load_feed_configs(path)
        if not configs:
            print(f"No feeds configured. Copy feeds.example.json to {path} and add your feed URL.", file=sys.stderr)
            return 1
        if args.source:
            configs = [c for c in configs if c.id == args.source]
            if not configs:
                print(f"No feed with id {args.source!r}.", file=sys.stderr)
                return 1

    importer = FeedImporter(store)
    failures = 0
    for config in configs:
        if args.dry_run:
            started = time.monotonic()
            try:
                products, skipped = importer.parse(config, importer.fetch(config))
            except Exception as exc:
                print(f"{config.id}: FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
                failures += 1
                continue
            by_retailer: dict[str, int] = {}
            for product in products:
                by_retailer[product.retailer] = by_retailer.get(product.retailer, 0) + 1
            print(f"{config.id}: would import {len(products)} products, skipping {skipped} ({time.monotonic() - started:.1f}s)")
            for retailer_id, count in sorted(by_retailer.items(), key=lambda kv: -kv[1]):
                print(f"    {retailer_id:<20}{count:>8}")
            for product in products[:3]:
                print(f"    e.g. £{product.price:>8.2f}  {product.title[:64]}")
            continue

        result = importer.run(config)
        if not result.ok:
            print(f"{config.id}: FAILED {result.error}", file=sys.stderr)
            failures += 1
            continue
        print(f"{config.id}: imported {result.rows} products, skipped {result.skipped} ({result.seconds:.1f}s)")
        for retailer_id, count in sorted(result.retailers.items(), key=lambda kv: -kv[1]):
            print(f"    {retailer_id:<20}{count:>8}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
