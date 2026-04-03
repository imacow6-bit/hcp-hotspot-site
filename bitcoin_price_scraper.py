"""
Bitcoin Real-Time Index (BRTI) Price Scraper
Scrapes from CF Benchmarks: https://www.cfbenchmarks.com/data/indices/BRTI

Uses Playwright (headless browser) to render the JS-heavy page, then polls
the DOM at high frequency to read the live price.

For theoretical/educational purposes only.

Setup:
    pip install playwright
    playwright install chromium
"""

import time
import csv
import asyncio
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# --- Configuration ---
URL = "https://www.cfbenchmarks.com/data/indices/BRTI"
POLLS_PER_SECOND = 5           # How many times per second to read the price
TOTAL_DURATION_SECONDS = 60    # How long to run (seconds)
OUTPUT_FILE = "brti_prices.csv"
# ---------------------

# CSS selectors to try — inspect the page in your browser (right-click the
# price -> Inspect Element) and update these if needed.
PRICE_SELECTORS = [
    "[data-testid='index-value']",
    ".index-value",
    ".price-value",
    ".current-value",
    "h1 + div span",            # common Next.js layout pattern
    "text=/\\$[\\d,]+\\.\\d+/", # regex: match anything like $83,123.45
]


async def find_price_element(page):
    """Try multiple selectors to find the price element on the page."""
    for selector in PRICE_SELECTORS:
        try:
            el = page.locator(selector).first
            if await el.count() > 0:
                text = await el.text_content()
                if text and any(c.isdigit() for c in text):
                    return el, selector
        except Exception:
            continue
    return None, None


def parse_price(text: str) -> float | None:
    """Extract a numeric price from text like '$83,123.45' or '83123.45'."""
    if not text:
        return None
    cleaned = text.replace("$", "").replace(",", "").replace(" ", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


async def run_scraper():
    results = []
    total_polls = POLLS_PER_SECOND * TOTAL_DURATION_SECONDS
    interval = 1.0 / POLLS_PER_SECOND

    print(f"Starting BRTI scraper: {POLLS_PER_SECOND} polls/s for {TOTAL_DURATION_SECONDS}s")
    print(f"Total polls planned: {total_polls}")
    print(f"Target URL: {URL}")
    print("-" * 70)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        print("Loading page (this may take a moment)...")
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        print("DOM loaded. Waiting for page to fully render...")

        # Give the JS framework time to fetch data and render the price
        await page.wait_for_timeout(10000)

        # Discover which selector works
        price_el, matched_selector = await find_price_element(page)
        if price_el is None:
            # Fallback: dump visible text AND save HTML for debugging
            body_text = await page.inner_text("body")
            html = await page.content()
            print("\nCould not auto-detect price element.")
            print("Page visible text (first 3000 chars):\n")
            print(body_text[:3000])
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(html)
            print("\n\nFull HTML saved to debug_page.html")
            print("Open that file and search for the price number, then update PRICE_SELECTORS.")
            await browser.close()
            return

        print(f"Found price element with selector: {matched_selector}")
        sample = await price_el.text_content()
        print(f"Current price text: {sample}")
        print("-" * 70)

        # --- High-frequency polling loop ---
        poll_id = 0
        start_time = time.monotonic()

        while time.monotonic() - start_time < TOTAL_DURATION_SECONDS:
            loop_start = time.monotonic()
            poll_id += 1

            try:
                text = await price_el.text_content()
                price = parse_price(text)
            except Exception as e:
                text = str(e)
                price = None

            timestamp = datetime.now(timezone.utc).isoformat()
            status = "OK" if price is not None else "ERR"

            results.append({
                "poll_id": poll_id,
                "timestamp": timestamp,
                "price": price,
                "raw_text": text,
                "status": status,
            })

            print(f"  [{status}] #{poll_id:>6}  {timestamp}  ${price}")

            # Sleep to maintain target poll rate
            elapsed = time.monotonic() - loop_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        await browser.close()

    # --- Write CSV ---
    print("-" * 70)
    print(f"Done. {len(results)} polls collected. Writing to {OUTPUT_FILE}...")

    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["poll_id", "timestamp", "price", "raw_text", "status"])
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    # Summary stats
    prices = [r["price"] for r in results if r["price"] is not None]
    if prices:
        print(f"\nSummary:")
        print(f"  Successful reads: {len(prices)}/{len(results)}")
        print(f"  Min price:  ${min(prices):,.2f}")
        print(f"  Max price:  ${max(prices):,.2f}")
        print(f"  Last price: ${prices[-1]:,.2f}")
        unique = len(set(prices))
        print(f"  Unique values seen: {unique}")

    print(f"\nResults saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(run_scraper())
