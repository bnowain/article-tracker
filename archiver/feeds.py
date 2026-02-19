"""
Feed fetcher — discovers and processes articles from RSS/Atom feeds.

For each configured source, fetches all RSS feeds, extracts article metadata,
optionally enriches with og:image/og:description from the page, downloads
preview images locally, and stores everything in the database.
"""

from __future__ import annotations
import hashlib
import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, quote

import feedparser
import httpx
from dateutil import parser as dateparser

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

logger = logging.getLogger(__name__)


# ── Retry Logic ───────────────────────────────────────────────────────

def fetch_with_retry(url: str, headers: dict, timeout: int = 30, max_retries: int = 3) -> Optional[httpx.Response]:
    """
    Fetch URL with exponential backoff retry for rate limits (429).
    
    Retries with increasing delays: 2s, 4s, 8s for HTTP 429 errors.
    """
    for attempt in range(max_retries):
        try:
            resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers=headers)
            
            if resp.status_code == 429:  # Too Many Requests
                if attempt < max_retries - 1:
                    wait_time = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    logger.debug(f"  Rate limited (429), waiting {wait_time}s before retry {attempt + 2}/{max_retries}")
                    time.sleep(wait_time)
                    continue
                else:
                    logger.debug(f"  Rate limited (429), max retries reached")
                    return None
            
            return resp
            
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** (attempt + 1)
                logger.debug(f"  Request failed ({e}), retrying in {wait_time}s")
                time.sleep(wait_time)
            else:
                logger.debug(f"  Request failed after {max_retries} attempts: {e}")
                return None
    
    return None

# ── Feed Parsing ──────────────────────────────────────────────────────

def fetch_feed(url: str, timeout: int = 30) -> list[dict]:
    """Parse an RSS/Atom feed, return list of normalized article dicts."""
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; NewsAggregator/1.0)"
        })
        feed = feedparser.parse(resp.text)
    except Exception as e:
        logger.warning(f"Failed to fetch feed {url}: {e}")
        return []

    articles = []
    for entry in feed.entries:
        link = entry.get("link", "").strip()
        if not link:
            continue

        # Parse publish date
        pub_date = None
        for date_field in ("published_parsed", "updated_parsed"):
            parsed = entry.get(date_field)
            if parsed:
                try:
                    pub_date = datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()
                except Exception:
                    pass
                break
        if not pub_date:
            for date_str_field in ("published", "updated"):
                ds = entry.get(date_str_field)
                if ds:
                    try:
                        pub_date = dateparser.parse(ds).isoformat()
                    except Exception:
                        pass
                    break

        # Extract preview image from feed
        image_url = _extract_feed_image(entry)

        # Description / summary
        desc = ""
        if entry.get("summary"):
            desc = _strip_html(entry.summary)[:500]
        elif entry.get("description"):
            desc = _strip_html(entry.description)[:500]

        # Author
        author = entry.get("author", "")
        if not author and entry.get("authors"):
            author = ", ".join(a.get("name", "") for a in entry.authors if a.get("name"))

        # Tags
        tags = []
        if entry.get("tags"):
            tags = [t.get("term", "") for t in entry.tags if t.get("term")]

        articles.append({
            "url": link,
            "headline": entry.get("title", "").strip(),
            "description": desc,
            "byline": author,
            "publish_date": pub_date,
            "preview_image_url": image_url or "",
            "tags": tags,
        })

    return articles


def _extract_feed_image(entry) -> Optional[str]:
    """Try multiple methods to get a preview image from a feed entry."""
    # media:content
    if entry.get("media_content"):
        for mc in entry.media_content:
            if mc.get("medium") == "image" or (mc.get("type", "").startswith("image")):
                return mc.get("url")
            if mc.get("url") and not mc.get("type"):
                return mc["url"]

    # media:thumbnail
    if entry.get("media_thumbnail"):
        for mt in entry.media_thumbnail:
            if mt.get("url"):
                return mt["url"]

    # enclosure
    if entry.get("enclosures"):
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image"):
                return enc.get("href") or enc.get("url")

    # image in content
    if entry.get("content"):
        for c in entry.content:
            img = _find_img_in_html(c.get("value", ""))
            if img:
                return img

    # image in summary/description
    for field in ("summary", "description"):
        html = entry.get(field, "")
        if html:
            img = _find_img_in_html(html)
            if img:
                return img

    return None


def _find_img_in_html(html: str) -> Optional[str]:
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    return match.group(1) if match else None


def _strip_html(html: str) -> str:
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text).strip()
    # Decode common entities
    for ent, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                       ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(ent, char)
    return text


# ── OG Metadata Enrichment ────────────────────────────────────────────

def fetch_og_metadata(url: str, timeout: int = 15) -> dict:
    """Fetch a page and extract Open Graph / meta tags for preview data."""
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        html = resp.text[:50000]  # Only parse the head area
    except Exception as e:
        logger.debug(f"OG fetch failed for {url}: {e}")
        return {}

    result = {}

    # og:image
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
    if m:
        result["og_image"] = m.group(1)

    # og:description
    m = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']', html)
    if not m:
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html)
    if m:
        result["og_description"] = m.group(1).strip()

    # og:title (fallback for headline)
    m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html)
    if m:
        result["og_title"] = m.group(1).strip()

    # article:author
    m = re.search(r'<meta[^>]+(?:property|name)=["\'](?:article:author|author)["\'][^>]+content=["\']([^"\']+)["\']', html)
    if m:
        result["og_author"] = m.group(1).strip()

    # article:published_time
    m = re.search(r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']', html)
    if m:
        try:
            result["og_published"] = dateparser.parse(m.group(1)).isoformat()
        except Exception:
            pass

    return result


# ── Paywall Bypass & Article Text Extraction ──────────────────────────

def fetch_article_text_via_bypass(url: str, timeout: int = 30, prefer_playwright: bool = False) -> Optional[str]:
    """
    Fetch full article text using multiple bypass methods.
    
    If prefer_playwright=True:
      Tries Playwright FIRST (for known hard paywalls), falls back to others if it fails
    
    If prefer_playwright=False (default):
      Tries methods in order: Direct, Google referrer, Facebook referrer, 12ft.io, 
      removepaywalls.com, then Playwright as last resort
    
    Returns clean article text or None if all methods failed.
    """
    if not BS4_AVAILABLE:
        logger.debug("BeautifulSoup not available, skipping article text extraction")
        return None
    
    # If prefer_playwright is set, try Playwright FIRST
    if prefer_playwright:
        text = _try_playwright(url, timeout)
        if text:
            return text
        # Playwright failed, fall through to other methods
    
    # Standard methods (fast to slow)
    
    headers_standard = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    # Method 1: Direct fetch (works for free sites)
    try:
        resp = fetch_with_retry(url, headers_standard, timeout)
        
        if resp and resp.status_code == 200:
            text = extract_article_text(resp.text)
            if text and len(text) > 500:  # Got substantial content
                logger.debug(f"  ✓ Direct fetch: {len(text)} chars")
                return text
    except Exception as e:
        logger.debug(f"  Direct fetch failed: {e}")
    
    # Method 2: Referrer spoofing (pretend to come from Google - works for many paywalls)
    try:
        headers_google = {
            **headers_standard,
            "Referer": "https://www.google.com/",
        }
        resp = fetch_with_retry(url, headers_google, timeout)
        
        if resp and resp.status_code == 200:
            text = extract_article_text(resp.text)
            if text and len(text) > 500:
                logger.debug(f"  ✓ Google referrer: {len(text)} chars")
                return text
    except Exception as e:
        logger.debug(f"  Google referrer failed: {e}")
    
    # Method 3: Facebook referrer (some sites allow social media referrals)
    try:
        headers_facebook = {
            "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            "Referer": "https://www.facebook.com/",
        }
        resp = fetch_with_retry(url, headers_facebook, timeout)
        
        if resp and resp.status_code == 200:
            text = extract_article_text(resp.text)
            if text and len(text) > 500:
                logger.debug(f"  ✓ Facebook referrer: {len(text)} chars")
                return text
    except Exception as e:
        logger.debug(f"  Facebook referrer failed: {e}")
    
    # Method 4: 12ft.io (popular paywall bypass)
    try:
        proxy_url = f"https://12ft.io/{url}"
        resp = fetch_with_retry(proxy_url, headers_standard, timeout)
        
        if resp and resp.status_code == 200:
            text = extract_article_text(resp.text)
            if text and len(text) > 500:
                logger.debug(f"  ✓ 12ft.io: {len(text)} chars")
                return text
    except Exception as e:
        logger.debug(f"  12ft.io failed: {e}")
    
    # Method 5: removepaywalls.com (last resort)
    try:
        proxy_url = f"https://removepaywalls.com/{url}"
        resp = fetch_with_retry(proxy_url, headers_standard, timeout)
        
        if resp and resp.status_code == 200:
            text = extract_article_text(resp.text)
            if text and len(text) > 500:
                logger.debug(f"  ✓ removepaywalls.com: {len(text)} chars")
                return text
    except Exception as e:
        logger.debug(f"  removepaywalls.com failed: {e}")
    
    # Method 6: Playwright (nuclear option - auto-enabled if installed)
    # Only try if we haven't already (prefer_playwright would have tried it first)
    if not prefer_playwright:
        text = _try_playwright(url, timeout)
        if text:
            return text
    
    logger.debug(f"  ✗ All bypass methods failed for {url}")
    return None


def _try_playwright(url: str, timeout: int = 30) -> Optional[str]:
    """
    Try to fetch article text using Playwright browser automation.
    Returns text if successful, None if failed or not installed.
    """
    try:
        from playwright.sync_api import sync_playwright
        
        logger.debug(f"  Trying Playwright (browser automation)...")
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Set realistic browser context
            page.set_extra_http_headers({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            
            # Navigate and wait for content
            page.goto(url, timeout=timeout * 1000)
            page.wait_for_load_state('networkidle', timeout=timeout * 1000)
            
            # Get rendered HTML
            html_content = page.content()
            browser.close()
            
            text = extract_article_text(html_content)
            if text and len(text) > 500:
                logger.debug(f"  ✓ Playwright: {len(text)} chars")
                return text
                
    except ImportError:
        # Playwright not installed - skip silently
        logger.debug(f"  Playwright not installed (install with: pip install playwright && playwright install chromium)")
    except Exception as e:
        logger.debug(f"  Playwright failed: {e}")
    
    return None


def extract_article_text(html: str) -> Optional[str]:
    """
    Extract clean article HTML from a page, preserving rich content:
    images, links, videos/iframes, blockquotes, headings.
    
    Returns sanitized HTML string (not plain text) suitable for
    rendering directly in the Flask interface.
    
    Backward note: callers checking len(text) > 500 still work fine
    since HTML with content will easily exceed that threshold.
    """
    if not BS4_AVAILABLE:
        return None

    # Tags we want to KEEP (safe, useful for rendering)
    KEEP_TAGS = {
        'p', 'br', 'strong', 'b', 'em', 'i', 'u', 's',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li',
        'blockquote', 'figure', 'figcaption',
        'a', 'img',
        'iframe',        # YouTube, Vimeo, Twitter embeds
        'video', 'source',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'div', 'span',   # kept temporarily, stripped to text at end if empty
    }

    # Attributes to KEEP per tag
    KEEP_ATTRS = {
        'a':      ['href', 'title'],
        'img':    ['src', 'alt', 'title', 'width', 'height'],
        'iframe': ['src', 'width', 'height', 'allowfullscreen', 'frameborder', 'title'],
        'video':  ['src', 'controls', 'width', 'height', 'poster'],
        'source': ['src', 'type'],
        'td':     ['colspan', 'rowspan'],
        'th':     ['colspan', 'rowspan'],
    }

    # Class/id patterns that signal noise to remove entirely
    NOISE_PATTERNS = [
        'ad', 'advertisement', 'promo', 'sponsor',
        'social', 'share', 'sharing',
        'newsletter', 'signup', 'subscribe',
        'paywall', 'premium', 'meter',
        'related', 'recommended', 'trending',
        'nav', 'navigation', 'menu', 'sidebar',
        'comment', 'disqus',
        'cookie', 'gdpr', 'consent',
        'popup', 'modal', 'overlay',
    ]

    try:
        soup = BeautifulSoup(html, 'html.parser')

        # 1. Remove junk tags wholesale
        for tag in soup.find_all(['script', 'style', 'nav', 'footer',
                                   'aside', 'header', 'noscript', 'form',
                                   'button', 'input', 'select', 'textarea']):
            tag.decompose()

        # 2. Remove noise containers by class/id
        for el in soup.find_all(True):
            classes = ' '.join(el.get('class', [])).lower()
            el_id = (el.get('id') or '').lower()
            if any(p in classes or p in el_id for p in NOISE_PATTERNS):
                el.decompose()

        # 3. Find the main article container
        container = None
        candidates = [
            soup.find('article'),
            soup.find(class_=lambda c: c and any(x in ' '.join(c).lower()
                      for x in ['article-body', 'story-body', 'post-content',
                                 'entry-content', 'article-content', 'gnt_ar_b'])),
            soup.find('main'),
            soup.find('div', class_=lambda c: c and 'content' in ' '.join(c).lower()),
        ]
        for c in candidates:
            if c:
                container = c
                break
        if not container:
            container = soup.find('body') or soup

        # 4. Sanitize attributes — keep only safe ones, fix relative URLs
        for tag in container.find_all(True):
            tag_name = tag.name

            # Strip tags not in our keep list (but keep their inner content)
            if tag_name not in KEEP_TAGS:
                tag.unwrap()
                continue

            # Keep only allowed attributes
            allowed = KEEP_ATTRS.get(tag_name, [])
            attrs_to_remove = [a for a in list(tag.attrs) if a not in allowed]
            for a in attrs_to_remove:
                del tag[a]

            # Make links open in new tab safely
            if tag_name == 'a' and tag.get('href'):
                tag['target'] = '_blank'
                tag['rel'] = 'noopener noreferrer'

            # Add loading=lazy to images
            if tag_name == 'img':
                tag['loading'] = 'lazy'
                tag['referrerpolicy'] = 'no-referrer'

        # 5. Extract cleaned HTML — require meaningful text content
        result_html = str(container)

        # Quick plain-text check to ensure we got real content
        plain_check = container.get_text(strip=True)
        if len(plain_check) < 200:
            # Try fallback: grab all paragraphs
            paras = soup.find_all('p')
            plain_check = ' '.join(p.get_text(strip=True) for p in paras)
            if len(plain_check) < 200:
                return None
            result_html = ''.join(str(p) for p in paras)

        return result_html

    except Exception as e:
        logger.debug(f"Article HTML extraction failed: {e}")
        return None


# ── URL Resolution ────────────────────────────────────────────────────

def resolve_redirect_url(url: str, timeout: int = 10) -> str:
    """
    Resolve redirect URLs (like Google News) to the actual article URL.
    Returns the final URL after following redirects.
    """
    # Detect Google News redirect URLs
    if 'news.google.com' in url:
        try:
            resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            })
            final_url = str(resp.url)
            logger.debug(f"  Resolved Google News URL → {final_url}")
            return final_url
        except Exception as e:
            logger.debug(f"  Failed to resolve redirect: {e}")
            return url
    
    # Already a direct URL
    return url


# ── Image Download ────────────────────────────────────────────────────

def download_image(url: str, images_dir: str, source_slug: str) -> Optional[str]:
    """Download an image and return the relative local path, or None."""
    if not url or not url.startswith("http"):
        return None

    dest_dir = Path(images_dir) / source_slug
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename from URL hash + extension
    ext = Path(urlparse(url).path).suffix[:5] or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"):
        ext = ".jpg"
    filename = hashlib.md5(url.encode()).hexdigest()[:12] + ext
    filepath = dest_dir / filename

    if filepath.exists():
        return f"{source_slug}/{filename}"

    try:
        resp = httpx.get(url, timeout=15, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; NewsAggregator/1.0)"
        })
        if resp.status_code == 200 and len(resp.content) > 500:
            filepath.write_bytes(resp.content)
            return f"{source_slug}/{filename}"
    except Exception as e:
        logger.debug(f"Image download failed {url}: {e}")

    return None


# ── Source Processing ─────────────────────────────────────────────────

def process_source(source: dict, db, images_dir: str, enrich: bool = True) -> int:
    """
    Fetch all RSS feeds for a source, add new articles to the database.
    Returns count of new articles added.
    """
    slug = source["slug"]
    name = source["name"]
    category = source.get("category", "")
    rss_urls = source.get("discovery", {}).get("rss_urls", [])
    
    # Check if paywall bypass is enabled for this source
    bypass_enabled = source.get("discovery", {}).get("bypass_paywall", False)
    prefer_playwright = source.get("discovery", {}).get("prefer_playwright", False)

    if not rss_urls:
        logger.debug(f"No RSS feeds configured for {name}")
        return 0

    # Collect all feed items across all feeds for this source
    all_items = []
    for feed_url in rss_urls:
        items = fetch_feed(feed_url)
        all_items.extend(items)
        logger.debug(f"  {feed_url}: {len(items)} items")

    # Deduplicate by URL
    seen = set()
    unique_items = []
    for item in all_items:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique_items.append(item)

    new_count = 0
    for item in unique_items:
        # Resolve redirect URLs (like Google News) to actual article URLs
        original_url = item["url"]
        resolved_url = resolve_redirect_url(original_url)
        if resolved_url != original_url:
            item["url"] = resolved_url
            logger.debug(f"  Resolved: {original_url[:50]}... → {resolved_url}")
        
        # Skip if already in database
        if db.url_exists(item["url"]):
            continue

        # Try OG enrichment for image and description
        if enrich and (not item["preview_image_url"] or not item["description"]):
            try:
                og = fetch_og_metadata(item["url"])
                if not item["preview_image_url"] and og.get("og_image"):
                    item["preview_image_url"] = og["og_image"]
                if not item["description"] and og.get("og_description"):
                    item["description"] = og["og_description"][:500]
                if not item["byline"] and og.get("og_author"):
                    item["byline"] = og["og_author"]
                if not item["publish_date"] and og.get("og_published"):
                    item["publish_date"] = og["og_published"]
                if og.get("og_title") and not item["headline"]:
                    item["headline"] = og["og_title"]
            except Exception:
                pass
            # Random delay to avoid rate limiting
            time.sleep(random.uniform(0.5, 1.0))

        # Fetch full article text if paywall bypass is enabled
        article_text = ""
        if bypass_enabled:
            try:
                logger.debug(f"  Fetching full text for: {item['url']}")
                article_text = fetch_article_text_via_bypass(item["url"]) or ""
                if article_text:
                    logger.debug(f"  → Extracted {len(article_text)} chars")
                # Random delay between 1.5-2.5 seconds to avoid rate limiting
                time.sleep(random.uniform(1.5, 2.5))
            except Exception as e:
                logger.debug(f"  Failed to fetch article text: {e}")

        # Download preview image
        local_img = None
        if item["preview_image_url"]:
            local_img = download_image(item["preview_image_url"], images_dir, slug)

        # Store in database
        aid = db.add_article(
            url=item["url"],
            source_slug=slug,
            source_name=name,
            category=category,
            headline=item.get("headline", ""),
            byline=item.get("byline", ""),
            description=item.get("description", ""),
            article_text=article_text,
            publish_date=item.get("publish_date"),
            preview_image_url=item.get("preview_image_url", ""),
            preview_image_local=local_img or "",
            tags=item.get("tags", []),
        )

        if aid:
            new_count += 1
            text_indicator = " [+text]" if article_text else ""
            logger.info(f"  + {item.get('headline', item['url'])[:80]}{text_indicator}")

    return new_count
