#!/usr/bin/env python3
"""
Analyze config.json and suggest which sources should enable paywall bypass.

Usage:
    python analyze_bypass_candidates.py config.json
    python analyze_bypass_candidates.py config.json --auto-add  # Add bypass_paywall flags
"""

import json
import argparse
from pathlib import Path


# Known paywalled domains
PAYWALL_DOMAINS = {
    "nytimes.com": "Hard paywall",
    "wsj.com": "Hard paywall", 
    "washingtonpost.com": "Metered paywall",
    "latimes.com": "Metered paywall",
    "sfchronicle.com": "Metered paywall",
    "sacbee.com": "Metered paywall",
    "redding.com": "Paywall",
    "mercurynews.com": "Metered paywall",
    "eastbaytimes.com": "Metered paywall",
    "ocregister.com": "Metered paywall",
    "sandiegouniontribune.com": "Metered paywall",
    "theguardian.com": "Soft paywall (optional)",
    "ft.com": "Hard paywall",
    "economist.com": "Hard paywall",
    "bloomberg.com": "Hard paywall",
    "theatlantic.com": "Metered paywall",
    "newyorker.com": "Metered paywall",
    "wired.com": "Metered paywall",
}

# Free news sites (don't need bypass)
FREE_DOMAINS = {
    "shastascout.org",
    "apnews.com",
    "reuters.com", 
    "bbc.com",
    "bbc.co.uk",
    "npr.org",
    "pbs.org",
    "cnn.com",
    "foxnews.com",
    "nbcnews.com",
    "abcnews.go.com",
    "cbsnews.com",
    "politico.com",
    "thehill.com",
    "axios.com",
}


def extract_domain(url):
    """Extract domain from URL."""
    if not url:
        return None
    url = url.replace("https://", "").replace("http://", "")
    parts = url.split("/")[0].split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return None


def analyze_source(source):
    """Analyze a source and determine if it should use bypass."""
    slug = source.get("slug", "")
    name = source.get("name", "")
    base_url = source.get("base_url", "")
    discovery = source.get("discovery", {})
    
    # Extract domains from various URL fields
    domains = set()
    
    if base_url:
        domains.add(extract_domain(base_url))
    
    for rss_url in discovery.get("rss_urls", []):
        domains.add(extract_domain(rss_url))
    
    if discovery.get("google_news_domain"):
        domains.add(extract_domain(discovery["google_news_domain"]))
    
    domains = {d for d in domains if d}
    
    # Check for paywall indicators
    paywall_type = None
    for domain in domains:
        if domain in PAYWALL_DOMAINS:
            paywall_type = PAYWALL_DOMAINS[domain]
            break
    
    # Check for free site indicators
    is_free = any(domain in FREE_DOMAINS for domain in domains)
    
    # Current bypass setting
    current_bypass = discovery.get("bypass_paywall", False)
    
    return {
        "slug": slug,
        "name": name,
        "domains": list(domains),
        "paywall_type": paywall_type,
        "is_free": is_free,
        "current_bypass": current_bypass,
        "has_rss": bool(discovery.get("rss_urls")),
    }


def main():
    parser = argparse.ArgumentParser(description="Analyze config for paywall bypass candidates")
    parser.add_argument("config", help="Path to config.json")
    parser.add_argument("--auto-add", action="store_true", 
                       help="Automatically add bypass_paywall flags")
    args = parser.parse_args()
    
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ Config file not found: {config_path}")
        return
    
    with open(config_path) as f:
        config = json.load(f)
    
    sites = config.get("sites", [])
    
    print(f"\n{'='*80}")
    print(f"Analyzing {len(sites)} sources from {config_path}")
    print(f"{'='*80}\n")
    
    # Analyze all sources
    results = []
    for site in sites:
        if not site.get("slug"):
            continue
        if not site.get("enabled", True):
            continue
        result = analyze_source(site)
        if result["has_rss"]:  # Only consider sources with RSS
            results.append(result)
    
    # Group by recommendation
    should_enable = []
    already_enabled = []
    should_disable = []
    unknown = []
    
    for r in results:
        if r["paywall_type"] and not r["current_bypass"]:
            should_enable.append(r)
        elif r["current_bypass"] and r["paywall_type"]:
            already_enabled.append(r)
        elif r["current_bypass"] and (r["is_free"] or not r["paywall_type"]):
            should_disable.append(r)
        elif not r["paywall_type"] and not r["is_free"] and not r["current_bypass"]:
            unknown.append(r)
    
    # Print recommendations
    if should_enable:
        print(f"✅ SHOULD ENABLE bypass_paywall ({len(should_enable)}):")
        print("-" * 80)
        for r in should_enable:
            print(f"  {r['name']} ({r['slug']})")
            print(f"    Domain: {', '.join(r['domains'])}")
            print(f"    Paywall: {r['paywall_type']}")
            print()
    
    if already_enabled:
        print(f"✓ ALREADY ENABLED ({len(already_enabled)}):")
        print("-" * 80)
        for r in already_enabled:
            print(f"  {r['name']} ({r['slug']}) - {r['paywall_type']}")
        print()
    
    if should_disable:
        print(f"⚠️  SHOULD DISABLE bypass_paywall ({len(should_disable)}):")
        print("-" * 80)
        for r in should_disable:
            print(f"  {r['name']} ({r['slug']})")
            if r['is_free']:
                print(f"    Reason: Free site, no paywall")
            else:
                print(f"    Reason: No known paywall")
            print()
    
    if unknown:
        print(f"❓ UNKNOWN / CHECK MANUALLY ({len(unknown)}):")
        print("-" * 80)
        for r in unknown:
            print(f"  {r['name']} ({r['slug']})")
            print(f"    Domain: {', '.join(r['domains'])}")
            print(f"    Action: Test manually to determine if paywall exists")
            print()
    
    # Auto-add option
    if args.auto_add and should_enable:
        print(f"\n{'='*80}")
        print(f"Auto-adding bypass_paywall flags...")
        print(f"{'='*80}\n")
        
        modified = False
        for site in sites:
            slug = site.get("slug")
            if any(r["slug"] == slug for r in should_enable):
                if "discovery" not in site:
                    site["discovery"] = {}
                site["discovery"]["bypass_paywall"] = True
                print(f"  ✓ Added bypass_paywall to {site.get('name')} ({slug})")
                modified = True
        
        if modified:
            backup_path = config_path.with_suffix(".json.backup")
            with open(backup_path, "w") as f:
                json.dump(config, f, indent=2)
            print(f"\n📄 Backup saved to: {backup_path}")
            
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            print(f"✅ Updated config saved to: {config_path}")
        else:
            print("  No changes needed.")
    
    elif should_enable and not args.auto_add:
        print(f"\n💡 TIP: Run with --auto-add to automatically update config.json")
    
    # Summary
    print(f"\n{'='*80}")
    print("SUMMARY:")
    print("-" * 80)
    print(f"  Sources with RSS: {len(results)}")
    print(f"  Should enable: {len(should_enable)}")
    print(f"  Already enabled: {len(already_enabled)}")
    print(f"  Should disable: {len(should_disable)}")
    print(f"  Unknown: {len(unknown)}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
