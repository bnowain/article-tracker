#!/usr/bin/env python3
"""
Historical Article Backfill Module

Fetches historical articles from news sites using multiple discovery methods:
- RSS feeds (limited, usually 30-90 days)
- Google News search (can go back years)
- Archive.org Wayback Machine (historical snapshots)
- Sitemaps (if they include historical articles)

Usage:
    python backfill.py --source record-searchlight --years 2
    python backfill.py --source record-searchlight --start 2022-01-01 --end 2023-12-31
    python backfill.py --all --years 1  # Backfill all sources
"""

import argparse
import logging
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse
import json

import httpx
from dateutil import parser as dateparser

from archiver.config import Config
from archiver.database import Database
from archiver.feeds import download_image, fetch_og_metadata

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Google News Search ────────────────────────────────────────────────

def search_google_news(domain: str, start_date: datetime, end_date: datetime,
                       max_results: int = 100) -> list[dict]:
    """
    Search Google News for articles from a specific domain within a date range.
    Returns list of article dicts with url, headline, publish_date.
    """
    if not BS4_AVAILABLE:
        logger.warning("BeautifulSoup not available, skipping Google News search")
        return []
    
    articles = []
    
    # Format dates for Google News
    # Google News uses format: before:YYYY-MM-DD after:YYYY-MM-DD
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    # Build Google News search URL
    query = f"site:{domain} after:{start_str} before:{end_str}"
    search_url = f"https://news.google.com/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    
    logger.info(f"  Searching Google News: {domain} from {start_str} to {end_str}")
    
    try:
        resp = httpx.get(search_url, timeout=30, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        
        if resp.status_code != 200:
            logger.warning(f"  Google News returned status {resp.status_code}")
            return []
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Google News article links are in <a> tags with class containing "article"
        # Structure changes frequently, so we use multiple selectors
        article_links = []
        
        # Try different selectors
        for selector in ['article a[href*="/articles/"]', 'a[href*="/articles/"]', 'a.JtKRv']:
            links = soup.select(selector)
            if links:
                article_links.extend(links)
                break
        
        for link in article_links[:max_results]:
            href = link.get('href', '')
            if not href or 'articles' not in href:
                continue
            
            # Google News URLs are like: ./articles/ARTICLE_ID
            # We need to extract the real URL (often in a redirect)
            # For now, we'll try to get the article title
            title_elem = link.find('h3') or link.find('h4') or link
            title = title_elem.get_text(strip=True) if title_elem else ""
            
            if not title:
                continue
            
            # Try to extract the actual article URL from the Google News redirect
            # This is tricky - Google News often obfuscates URLs
            # We'll need to fetch the redirect target
            try:
                google_url = f"https://news.google.com{href}" if href.startswith('./') else href
                redirect_resp = httpx.get(google_url, timeout=10, follow_redirects=True)
                actual_url = str(redirect_resp.url)
                
                # Verify it's from the target domain
                if domain in actual_url:
                    articles.append({
                        'url': actual_url,
                        'headline': title,
                        'publish_date': None,  # Will be enriched later
                        'source': 'google_news',
                    })
                    logger.debug(f"    Found: {title}")
            except Exception as e:
                logger.debug(f"    Failed to resolve Google News URL: {e}")
                continue
        
        logger.info(f"  → Found {len(articles)} articles via Google News")
        
    except Exception as e:
        logger.error(f"  Google News search failed: {e}")
    
    return articles


# ── Archive.org Wayback Machine ──────────────────────────────────────

def search_wayback_machine(url_pattern: str, start_date: datetime, end_date: datetime,
                            max_results: int = 100) -> list[dict]:
    """
    Search Archive.org Wayback Machine for historical captures.
    Returns list of article dicts with url, snapshot_date.
    """
    articles = []
    
    # Wayback Machine CDX API
    # http://web.archive.org/cdx/search/cdx?url=PATTERN&from=YYYYMMDD&to=YYYYMMDD&output=json
    
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")
    
    # Build CDX API URL
    cdx_url = (
        f"http://web.archive.org/cdx/search/cdx"
        f"?url={quote(url_pattern)}"
        f"&from={start_str}"
        f"&to={end_str}"
        f"&output=json"
        f"&fl=timestamp,original,statuscode,mimetype"
        f"&filter=statuscode:200"
        f"&filter=mimetype:text/html"
        f"&collapse=original"  # Only unique URLs
        f"&limit={max_results}"
    )
    
    logger.info(f"  Searching Wayback Machine: {url_pattern}")
    
    try:
        resp = httpx.get(cdx_url, timeout=60, follow_redirects=True)
        
        if resp.status_code != 200:
            logger.warning(f"  Wayback CDX API returned status {resp.status_code}")
            return []
        
        data = resp.json()
        
        # First row is header: ['timestamp', 'original', 'statuscode', 'mimetype']
        if len(data) < 2:
            logger.info(f"  → No captures found in Wayback Machine")
            return []
        
        headers = data[0]
        rows = data[1:]
        
        for row in rows:
            if len(row) < 2:
                continue
            
            timestamp = row[0]  # YYYYMMDDHHMMSS
            original_url = row[1]
            
            # Parse timestamp
            try:
                snapshot_date = datetime.strptime(timestamp, "%Y%m%d%H%M%S")
                snapshot_date = snapshot_date.replace(tzinfo=timezone.utc)
            except Exception:
                snapshot_date = None
            
            # Build Wayback Machine URL
            wayback_url = f"https://web.archive.org/web/{timestamp}/{original_url}"
            
            articles.append({
                'url': original_url,
                'wayback_url': wayback_url,
                'snapshot_date': snapshot_date.isoformat() if snapshot_date else None,
                'publish_date': snapshot_date.isoformat() if snapshot_date else None,
                'source': 'wayback_machine',
            })
        
        logger.info(f"  → Found {len(articles)} captures in Wayback Machine")
        
    except Exception as e:
        logger.error(f"  Wayback Machine search failed: {e}")
    
    return articles


# ── Sitemap Parser ───────────────────────────────────────────────────

def fetch_sitemap_urls(sitemap_url: str, url_patterns: list[str] = None,
                       start_date: datetime = None, end_date: datetime = None,
                       max_results: int = 1000) -> list[dict]:
    """
    Fetch URLs from a sitemap XML file.
    Filters by URL patterns and date range if provided.
    """
    if not BS4_AVAILABLE:
        logger.warning("BeautifulSoup not available, skipping sitemap")
        return []
    
    articles = []
    
    logger.info(f"  Fetching sitemap: {sitemap_url}")
    
    try:
        resp = httpx.get(sitemap_url, timeout=30, follow_redirects=True)
        
        if resp.status_code != 200:
            logger.warning(f"  Sitemap returned status {resp.status_code}")
            return []
        
        soup = BeautifulSoup(resp.text, 'xml')
        
        # Handle sitemap index (links to other sitemaps)
        sitemap_refs = soup.find_all('sitemap')
        if sitemap_refs:
            logger.info(f"  → Sitemap index with {len(sitemap_refs)} sub-sitemaps")
            for sitemap_ref in sitemap_refs[:10]:  # Limit to 10 sub-sitemaps
                loc = sitemap_ref.find('loc')
                if loc and loc.text:
                    sub_articles = fetch_sitemap_urls(
                        loc.text, url_patterns, start_date, end_date,
                        max_results - len(articles)
                    )
                    articles.extend(sub_articles)
                    if len(articles) >= max_results:
                        break
            return articles
        
        # Regular sitemap with URLs
        urls = soup.find_all('url')
        
        for url_elem in urls:
            if len(articles) >= max_results:
                break
            
            loc = url_elem.find('loc')
            if not loc or not loc.text:
                continue
            
            url = loc.text.strip()
            
            # Filter by URL patterns
            if url_patterns:
                if not any(pattern in url for pattern in url_patterns):
                    continue
            
            # Get lastmod date if available
            lastmod = url_elem.find('lastmod')
            pub_date = None
            if lastmod and lastmod.text:
                try:
                    pub_date = dateparser.parse(lastmod.text).isoformat()
                    
                    # Filter by date range
                    if start_date or end_date:
                        dt = dateparser.parse(lastmod.text)
                        if start_date and dt < start_date:
                            continue
                        if end_date and dt > end_date:
                            continue
                except Exception:
                    pass
            
            articles.append({
                'url': url,
                'publish_date': pub_date,
                'source': 'sitemap',
            })
        
        logger.info(f"  → Found {len(articles)} URLs in sitemap")
        
    except Exception as e:
        logger.error(f"  Sitemap fetch failed: {e}")
    
    return articles


# ── Main Backfill Logic ──────────────────────────────────────────────

def backfill_source(source: dict, db: Database, config: Config,
                    start_date: datetime, end_date: datetime,
                    methods: list[str] = None) -> int:
    """
    Backfill historical articles for a source using multiple methods.
    Returns count of new articles added.
    """
    slug = source["slug"]
    name = source["name"]
    category = source.get("category", "")
    discovery = source.get("discovery", {})
    
    if methods is None:
        methods = ["google_news", "wayback", "sitemap"]
    
    logger.info(f"\n{'='*70}")
    logger.info(f"Backfilling: {name}")
    logger.info(f"Date range: {start_date.date()} to {end_date.date()}")
    logger.info(f"Methods: {', '.join(methods)}")
    logger.info(f"{'='*70}")
    
    all_articles = []
    
    # Method 1: Google News search
    if "google_news" in methods and discovery.get("google_news_domain"):
        domain = discovery["google_news_domain"]
        articles = search_google_news(domain, start_date, end_date)
        all_articles.extend(articles)
        time.sleep(2)  # Be polite to Google
    
    # Method 2: Wayback Machine
    if "wayback" in methods:
        # Use base_url or article_url_patterns
        base_url = source.get("base_url", "")
        url_patterns = discovery.get("article_url_patterns", [])
        
        # Search for each pattern
        for pattern in url_patterns[:3]:  # Limit to 3 patterns
            search_pattern = f"{base_url}{pattern}*" if base_url else pattern
            articles = search_wayback_machine(search_pattern, start_date, end_date, max_results=50)
            all_articles.extend(articles)
            time.sleep(1)
    
    # Method 3: Sitemaps
    if "sitemap" in methods and discovery.get("sitemap_urls"):
        url_patterns = discovery.get("article_url_patterns")
        for sitemap_url in discovery["sitemap_urls"][:2]:  # Limit to 2 sitemaps
            articles = fetch_sitemap_urls(
                sitemap_url, url_patterns, start_date, end_date
            )
            all_articles.extend(articles)
    
    # Deduplicate by URL
    seen = set()
    unique_articles = []
    for article in all_articles:
        url = article.get('url', '')
        if url and url not in seen:
            seen.add(url)
            unique_articles.append(article)
    
    logger.info(f"\nFound {len(unique_articles)} unique articles")
    
    # Filter out articles already in database
    new_articles = []
    for article in unique_articles:
        if not db.url_exists(article['url']):
            new_articles.append(article)
    
    logger.info(f"New articles (not in database): {len(new_articles)}")
    
    # Fetch metadata and store each article
    new_count = 0
    for i, article in enumerate(new_articles):
        logger.info(f"\n[{i+1}/{len(new_articles)}] {article['url']}")
        
        # Fetch OG metadata to enrich
        try:
            og = fetch_og_metadata(article['url'])
            if not article.get('headline') and og.get('og_title'):
                article['headline'] = og['og_title']
            if not article.get('publish_date') and og.get('og_published'):
                article['publish_date'] = og['og_published']
            if og.get('og_description'):
                article['description'] = og['og_description']
            if og.get('og_image'):
                article['preview_image_url'] = og['og_image']
            if og.get('og_author'):
                article['byline'] = og['og_author']
        except Exception as e:
            logger.debug(f"  OG fetch failed: {e}")
        
        time.sleep(0.5)  # Be polite
        
        # Download preview image
        local_img = None
        if article.get('preview_image_url'):
            local_img = download_image(
                article['preview_image_url'],
                config.images_dir,
                slug
            )
        
        # Store in database
        aid = db.add_article(
            url=article['url'],
            source_slug=slug,
            source_name=name,
            category=category,
            headline=article.get('headline', ''),
            byline=article.get('byline', ''),
            description=article.get('description', ''),
            publish_date=article.get('publish_date'),
            preview_image_url=article.get('preview_image_url', ''),
            preview_image_local=local_img or '',
        )
        
        if aid:
            new_count += 1
            logger.info(f"  ✓ Added: {article.get('headline', article['url'])[:80]}")
    
    logger.info(f"\n{'='*70}")
    logger.info(f"Backfill complete: {new_count} new articles added")
    logger.info(f"{'='*70}\n")
    
    return new_count


def main():
    parser = argparse.ArgumentParser(description="Historical article backfill")
    parser.add_argument("--config", default="config.json", help="Config file path")
    parser.add_argument("--source", help="Source slug to backfill")
    parser.add_argument("--all", action="store_true", help="Backfill all enabled sources")
    parser.add_argument("--years", type=float, help="How many years back to fetch (e.g., 2)")
    parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (YYYY-MM-DD)")
    parser.add_argument("--methods", nargs="+", 
                       choices=["google_news", "wayback", "sitemap"],
                       default=["google_news", "wayback", "sitemap"],
                       help="Which discovery methods to use")
    args = parser.parse_args()
    
    config = Config(args.config)
    db = Database(config.database_path)
    
    # Determine date range
    if args.start and args.end:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    elif args.years:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=int(args.years * 365))
    else:
        # Default: last 1 year
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=365)
    
    logger.info(f"\n📚 Historical Article Backfill")
    logger.info(f"Database: {config.database_path}")
    logger.info(f"Date range: {start_date.date()} to {end_date.date()}")
    logger.info(f"Methods: {', '.join(args.methods)}\n")
    
    # Get sources to backfill
    if args.all:
        sources = config.get_enabled_sites()
        sources = [s for s in sources if s.get("slug")]
    elif args.source:
        source = config.get_site(args.source)
        if not source:
            logger.error(f"Source '{args.source}' not found in config")
            return
        sources = [source]
    else:
        logger.error("Specify --source SLUG or --all")
        return
    
    # Backfill each source
    total_new = 0
    for source in sources:
        try:
            new = backfill_source(
                source, db, config,
                start_date, end_date,
                methods=args.methods
            )
            total_new += new
        except Exception as e:
            logger.error(f"Error backfilling {source['name']}: {e}")
    
    stats = db.get_stats()
    logger.info(f"\n{'='*70}")
    logger.info(f"TOTAL: {total_new} new articles added")
    logger.info(f"Database now has {stats['total_articles']} articles")
    logger.info(f"{'='*70}\n")
    
    db.close()


if __name__ == "__main__":
    main()
