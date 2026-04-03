"""
Bitcoin Real-Time Index (BRTI) Price Scraper
Scrapes from CF Benchmarks: https://www.cfbenchmarks.com/data/indices/BRTI

For theoretical/educational purposes only.
"""

import time
import json
import csv
import asyncio
import aiohttp
from datetime import datetime, timezone

# CF Benchmarks API endpoint for BRTI
API_URL = "https://www.cfbenchmarks.com/api/v1/values?id=BRTI"

# --- Configuration ---
REQUESTS_PER_SECOND = 5        # How many requests per second
TOTAL_DURATION_SECONDS = 60    # How long to run (seconds)
OUTPUT_FILE = "brti_prices.csv"
# ---------------------


async def fetch_price(session: aiohttp.ClientSession, request_id: int) -> dict | None:
    """Fetch the latest BRTI price from the CF Benchmarks API."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.cfbenchmarks.com/data/indices/BRTI",
    }
    try:
        async with session.get(API_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                # The API returns a payload with server timestamp and value(s)
                # Adjust parsing based on actual response structure
                timestamp = datetime.now(timezone.utc).isoformat()
                price = None

                # Try common response shapes
                if isinstance(data, dict):
                    if "payload" in data:
                        entries = data["payload"]
                        if isinstance(entries, list) and entries:
                            price = entries[-1].get("value") or entries[-1].get("price")
                        elif isinstance(entries, dict):
                            price = entries.get("value") or entries.get("price")
                    elif "value" in data:
                        price = data["value"]
                    elif "price" in data:
                        price = data["price"]

                return {
                    "request_id": request_id,
                    "timestamp": timestamp,
                    "price": price,
                    "raw": data,
                    "status": resp.status,
                }
            else:
                return {
                    "request_id": request_id,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "price": None,
                    "raw": await resp.text(),
                    "status": resp.status,
                }
    except Exception as e:
        return {
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price": None,
            "raw": str(e),
            "status": "error",
        }


async def scrape_loop():
    """Main scraping loop — fires REQUESTS_PER_SECOND concurrently each second."""
    interval = 1.0 / REQUESTS_PER_SECOND
    total_requests = REQUESTS_PER_SECOND * TOTAL_DURATION_SECONDS
    results = []

    print(f"Starting BRTI scraper: {REQUESTS_PER_SECOND} req/s for {TOTAL_DURATION_SECONDS}s")
    print(f"Total requests planned: {total_requests}")
    print("-" * 60)

    connector = aiohttp.TCPConnector(limit=REQUESTS_PER_SECOND * 2)
    async with aiohttp.ClientSession(connector=connector) as session:
        request_id = 0
        start_time = time.monotonic()

        while time.monotonic() - start_time < TOTAL_DURATION_SECONDS:
            batch_start = time.monotonic()

            # Fire a batch of concurrent requests for this second
            tasks = []
            for _ in range(REQUESTS_PER_SECOND):
                request_id += 1
                tasks.append(fetch_price(session, request_id))

            batch_results = await asyncio.gather(*tasks)

            for r in batch_results:
                if r:
                    results.append(r)
                    status_icon = "OK" if r["price"] is not None else f"!{r['status']}"
                    print(f"  [{status_icon}] #{r['request_id']:>5}  {r['timestamp']}  ${r['price']}")

            # Sleep the remainder of 1 second
            elapsed = time.monotonic() - batch_start
            if elapsed < 1.0:
                await asyncio.sleep(1.0 - elapsed)

    # Write results to CSV
    print("-" * 60)
    print(f"Done. {len(results)} responses collected. Writing to {OUTPUT_FILE}...")

    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["request_id", "timestamp", "price", "status"])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "request_id": r["request_id"],
                "timestamp": r["timestamp"],
                "price": r["price"],
                "status": r["status"],
            })

    # Also dump first raw response for debugging the API shape
    if results:
        print(f"\nSample raw API response (request #1):")
        print(json.dumps(results[0]["raw"], indent=2) if isinstance(results[0]["raw"], dict) else results[0]["raw"])

    print(f"\nResults saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(scrape_loop())
