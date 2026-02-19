#!/usr/bin/env python3
"""
Discover working RSS feeds for a news site.
Tests common RSS URL patterns and checks for RSS auto-discovery.
"""

import httpx
from bs4 import BeautifulSoup

def test_rss_url(url):
    """Test if a URL returns a valid RSS feed."""
    try:
        resp = httpx.get(url, timeout=10, follow_redirects=True)
        if resp.status_code == 200:
            # Check if it looks like RSS/XML
            content = resp.text[:500].lower()
            if any(tag in content for tag in ['<rss', '<feed', '<?xml']):
                return True, resp.url
        return False, None
    except Exception:
        return False, None

def discover_rss_feeds(base_url):
    """Discover RSS feeds from a website."""
    print(f"🔍 Discovering RSS feeds for: {base_url}\n")
    
    found_feeds = []
    
    # Common RSS URL patterns
    patterns = [
        "/feed/",
        "/feeds/",
        "/rss/",
        "/rss.xml",
        "/feed.xml",
        "/atom.xml",
        "/index.rss",
        "/index.xml",
        "/?feed=rss",
        "/?feed=rss2",
        "/?feed=atom",
        "/news/feed/",
        "/news/rss/",
    ]
    
    print("Testing common RSS URL patterns:")
    for pattern in patterns:
        test_url = f"{base_url}{pattern}"
        is_rss, final_url = test_rss_url(test_url)
        if is_rss:
            print(f"  ✓ Found: {test_url}")
            if final_url and final_url != test_url:
                print(f"    → Redirects to: {final_url}")
            found_feeds.append(final_url or test_url)
        else:
            print(f"  ✗ Not found: {test_url}")
    
    # Check homepage for RSS auto-discovery links
    print(f"\nChecking homepage for RSS auto-discovery:")
    try:
        resp = httpx.get(base_url, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Look for RSS link tags
            rss_links = soup.find_all('link', type=lambda t: t and 'rss' in t.lower())
            rss_links += soup.find_all('link', type=lambda t: t and 'atom' in t.lower())
            rss_links += soup.find_all('link', rel='alternate')
            
            for link in rss_links:
                href = link.get('href')
                if href:
                    # Make absolute URL
                    if href.startswith('//'):
                        href = 'https:' + href
                    elif href.startswith('/'):
                        href = base_url + href
                    elif not href.startswith('http'):
                        href = base_url + '/' + href
                    
                    if href not in found_feeds:
                        is_rss, final_url = test_rss_url(href)
                        if is_rss:
                            print(f"  ✓ Found via auto-discovery: {href}")
                            found_feeds.append(final_url or href)
    except Exception as e:
        print(f"  ✗ Error checking homepage: {e}")
    
    # Google News RSS (always works if domain is in Google News)
    google_news_url = f"https://news.google.com/rss/search?q=site:{base_url.replace('https://', '').replace('http://', '')}&hl=en-US&gl=US&ceid=US:en"
    print(f"\nGoogle News RSS (always available):")
    print(f"  ✓ {google_news_url}")
    found_feeds.append(google_news_url)
    
    return found_feeds

if __name__ == "__main__":
    base_url = "https://www.redding.com"
    feeds = discover_rss_feeds(base_url)
    
    print(f"\n{'='*70}")
    print(f"SUMMARY: Found {len(feeds)} RSS feeds")
    print(f"{'='*70}")
    for i, feed in enumerate(feeds, 1):
        print(f"{i}. {feed}")
    
    print(f"\n{'='*70}")
    print("Next steps:")
    print("1. Copy the working RSS URLs above")
    print("2. Update config.json Record Searchlight section:")
    print('   "rss_urls": [')
    for feed in feeds:
        print(f'     "{feed}",')
    print('   ]')
    print(f"{'='*70}")
