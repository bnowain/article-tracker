#!/usr/bin/env python3
"""
News Aggregator — forward-looking feed aggregator.

Fetches RSS feeds from all configured sources, stores new articles in the
database with preview images. Tracks last check time per source so restarts
pick up where they left off.

Usage:
    python run.py                   # Single pass — check all sources once
    python run.py --continuous      # Keep running, re-check on schedule
    python run.py --interval 10     # Custom interval in minutes (default: 15)
    python run.py --source npr      # Check a single source
    python run.py --no-enrich       # Skip OG metadata fetching (faster)
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from archiver.config import Config
from archiver.database import Database
from archiver.feeds import process_source

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

RUNNING = True

def signal_handler(sig, frame):
    global RUNNING
    logger.info("\nStopping after current source completes...")
    RUNNING = False

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def check_all_sources(config: Config, db: Database, enrich: bool = True,
                       only_source: str = None) -> int:
    """Run one pass over all enabled sources. Returns total new articles."""
    sites = config.get_enabled_sites()
    # Filter out non-site entries (section dividers etc.)
    sites = [s for s in sites if s.get("slug")]

    if only_source:
        sites = [s for s in sites if s["slug"] == only_source]
        if not sites:
            logger.error(f"Source '{only_source}' not found or not enabled")
            return 0

    total_new = 0
    images_dir = config.images_dir

    for i, site in enumerate(sites):
        if not RUNNING:
            break

        slug = site["slug"]
        name = site["name"]
        has_rss = bool(site.get("discovery", {}).get("rss_urls"))
        has_google = bool(site.get("discovery", {}).get("google_news_domain"))
        has_scrape = bool(site.get("discovery", {}).get("article_url_patterns"))

        if not has_rss and not has_google and not has_scrape:
            continue

        logger.info(f"[{i+1}/{len(sites)}] {name}")

        try:
            new = process_source(site, db, images_dir, enrich=enrich)
            db.set_last_check(slug)
            total_new += new

            if new > 0:
                logger.info(f"  → {new} new articles")
        except Exception as e:
            logger.error(f"  Error processing {name}: {e}")

        # Small delay between sources to be polite
        if RUNNING and i < len(sites) - 1:
            time.sleep(2)

    return total_new


def main():
    parser = argparse.ArgumentParser(description="News feed aggregator")
    parser.add_argument("--config", default="config.json", help="Config file path")
    parser.add_argument("--continuous", action="store_true", help="Keep running on schedule")
    parser.add_argument("--interval", type=int, default=15, help="Minutes between checks (default: 15)")
    parser.add_argument("--source", default=None, help="Check only this source slug")
    parser.add_argument("--no-enrich", action="store_true", help="Skip OG metadata enrichment")
    args = parser.parse_args()

    config = Config(args.config)
    db = Database(config.database_path)

    enabled = [s for s in config.get_enabled_sites() if s.get("slug") and (
        s.get("discovery", {}).get("rss_urls") or
        s.get("discovery", {}).get("google_news_domain") or
        s.get("discovery", {}).get("article_url_patterns")
    )]
    logger.info(f"News Aggregator — {len(enabled)} sources configured")
    logger.info(f"Database: {config.database_path}")
    logger.info(f"Images: {config.images_dir}")

    if args.continuous:
        logger.info(f"Running continuously — checking every {args.interval} minutes")
        logger.info("Press Ctrl+C to stop\n")

    pass_num = 0
    while RUNNING:
        pass_num += 1
        start = time.time()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")

        logger.info(f"{'═' * 50}")
        logger.info(f"Pass #{pass_num} — {ts}")
        logger.info(f"{'═' * 50}")

        new = check_all_sources(config, db, enrich=not args.no_enrich,
                                 only_source=args.source)

        elapsed = time.time() - start
        stats = db.get_stats()
        logger.info(f"{'─' * 50}")
        logger.info(f"Pass complete: {new} new articles in {elapsed:.0f}s")
        logger.info(f"Database total: {stats['total_articles']} articles from {stats['total_sources']} sources")

        if not args.continuous:
            break

        if RUNNING:
            logger.info(f"Next check in {args.interval} minutes...\n")
            for _ in range(args.interval * 60):
                if not RUNNING:
                    break
                time.sleep(1)

    db.close()
    logger.info("Stopped.")


if __name__ == "__main__":
    main()
