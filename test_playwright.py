#!/usr/bin/env python3
"""
Test Playwright bypass on Record Searchlight (Gannett paywall).
Focused on stealth/anti-detection strategies.

Usage:
    python test_playwright_rs.py
    python test_playwright_rs.py "https://www.redding.com/story/..."
"""

import asyncio
import sys
from playwright.async_api import async_playwright

TEST_URL = "https://www.redding.com/story/news/local/2026/02/16/money-to-demolish-this-problem-property-on-redding-council-agenda/88669018007/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

WAIT = "domcontentloaded"

try:
    from playwright_stealth import stealth_async
    STEALTH_AVAILABLE = True
    print("✅ playwright-stealth loaded")
except ImportError:
    STEALTH_AVAILABLE = False
    print("❌ playwright-stealth not found")


# ── Helpers ────────────────────────────────────────────────────────────────

def score_content(text: str) -> str:
    if len(text) > 3000:
        return "✅ FULL TEXT (likely bypassed)"
    elif len(text) > 800:
        return "⚠️  PARTIAL (teaser only?)"
    else:
        return "❌ LITTLE/NO content"


def print_result(strategy: str, text: str):
    print(f"\n{'─'*60}")
    print(f"Strategy: {strategy}")
    print(f"Length: {len(text):,} chars  {score_content(text)}")
    print(f"{'─'*60}")
    print(text[:1500].strip())
    if len(text) > 1500:
        print(f"\n... [{len(text)-1500:,} more chars] ...")


# ── Strategies ─────────────────────────────────────────────────────────────

async def strategy_stealth_headless(url: str) -> str:
    """Stealth mode — patches all headless browser fingerprint tells."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=USER_AGENT)
        if STEALTH_AVAILABLE:
            await stealth_async(page)
        await page.goto(url, wait_until=WAIT, timeout=30000)
        await page.wait_for_timeout(3000)
        text = await page.evaluate("document.body.innerText")
        await browser.close()
        return text


async def strategy_stealth_google_referrer(url: str) -> str:
    """Stealth + Google referrer."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={"Referer": "https://www.google.com/"}
        )
        page = await context.new_page()
        if STEALTH_AVAILABLE:
            await stealth_async(page)
        await page.goto(url, wait_until=WAIT, timeout=30000)
        await page.wait_for_timeout(3000)
        text = await page.evaluate("document.body.innerText")
        await browser.close()
        return text


async def strategy_stealth_navigate_from_google(url: str) -> str:
    """Stealth + actually navigate from Google so referrer is set natively."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=USER_AGENT)
        if STEALTH_AVAILABLE:
            await stealth_async(page)
        await page.goto("https://www.google.com", wait_until=WAIT, timeout=15000)
        await page.wait_for_timeout(800)
        await page.goto(url, wait_until=WAIT, timeout=30000)
        await page.wait_for_timeout(3000)
        text = await page.evaluate("document.body.innerText")
        await browser.close()
        return text


async def strategy_non_headless(url: str) -> str:
    """
    Non-headless (visible) browser — hardest for sites to detect.
    Opens a real browser window briefly.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-minimized"]  # minimized so it's less disruptive
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={"Referer": "https://www.google.com/"}
        )
        page = await context.new_page()
        await page.goto(url, wait_until=WAIT, timeout=30000)
        await page.wait_for_timeout(4000)
        text = await page.evaluate("document.body.innerText")
        await browser.close()
        return text


async def strategy_non_headless_stealth(url: str) -> str:
    """Non-headless + stealth — the most human-like combination."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--start-minimized"]
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={"Referer": "https://www.google.com/"}
        )
        page = await context.new_page()
        if STEALTH_AVAILABLE:
            await stealth_async(page)
        await page.goto(url, wait_until=WAIT, timeout=30000)
        await page.wait_for_timeout(4000)
        text = await page.evaluate("document.body.innerText")
        await browser.close()
        return text


async def strategy_stealth_paragraph_dump(url: str) -> str:
    """
    Stealth + dump all <p> tags from DOM.
    Definitive check: is full article text in DOM with stealth enabled?
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={"Referer": "https://www.google.com/"}
        )
        page = await context.new_page()
        if STEALTH_AVAILABLE:
            await stealth_async(page)
        await page.goto(url, wait_until=WAIT, timeout=30000)
        await page.wait_for_timeout(3000)
        text = await page.evaluate("""
            () => Array.from(document.querySelectorAll('p'))
                .map(p => p.textContent.trim())
                .filter(t => t.length > 40)
                .join('\\n\\n')
        """)
        await browser.close()
        return text


# ── Main ───────────────────────────────────────────────────────────────────

async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else TEST_URL
    print(f"\n{'═'*60}")
    print(f"Testing stealth Playwright bypass strategies")
    print(f"URL: {url}")
    print(f"{'═'*60}")

    if not STEALTH_AVAILABLE:
        print("\n⚠️  WARNING: playwright-stealth not installed.")
        print("   Run: pip install playwright-stealth")
        print("   Stealth strategies will run without patching.\n")

    strategies = [
        ("Stealth headless", strategy_stealth_headless),
        ("Stealth + Google referrer", strategy_stealth_google_referrer),
        ("Stealth + navigate from Google", strategy_stealth_navigate_from_google),
        ("Stealth + paragraph DOM dump", strategy_stealth_paragraph_dump),
        ("Non-headless (visible browser)", strategy_non_headless),
        ("Non-headless + stealth", strategy_non_headless_stealth),
    ]

    results = []
    best_strategy = None
    best_length = 0

    for name, fn in strategies:
        print(f"\n⏳ Trying: {name}...")
        try:
            text = await fn(url)
            print_result(name, text)
            results.append((name, len(text)))
            if len(text) > best_length:
                best_length = len(text)
                best_strategy = name
        except Exception as e:
            print(f"\n❌ {name} failed: {e}")
            results.append((name, 0))

    print(f"\n{'═'*60}")
    print(f"RANKINGS:")
    for name, length in sorted(results, key=lambda x: x[1], reverse=True):
        status = score_content(' ' * length)
        print(f"  {length:>6,} chars  {status}  {name}")
    print(f"\nBEST: {best_strategy} ({best_length:,} chars)")
    if best_length >= 3000:
        print(f"\n🎉 SUCCESS! Wire the winning strategy into feeds.py bypass chain.")
    else:
        print(f"\n⚠️  Still truncated. Gannett may be detecting automation or")
        print(f"   truncating server-side regardless of browser fingerprint.")
        print(f"   Next step: residential proxy or saved login session.")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
