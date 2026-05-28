"""
Verify: pageSize=100 returns all fields needed for 23-column template.
Checks a single page against the actual data template.
"""
import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
from playwright.async_api import async_playwright

HMAC_KEY = b"abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
PROFILE_DIR = Path(__file__).parent / "chrome_profile"
TEMPLATE_PATH = Path(__file__).parent.parent / "数据模板.xlsx"

# Import build_row from main crawler
import sys
sys.path.insert(0, str(Path(__file__).parent))
from scrape_51job_fetch import build_row, read_template_columns


async def main():
    # Read template columns
    cols = read_template_columns()
    print(f"Template has {len(cols)} columns:")
    for i, c in enumerate(cols):
        print(f"  [{i+1:2d}] {repr(c)}")

    # Fetch one page
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        await page.goto(
            "https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(4)

        # Build URL with pageSize=100
        ts = str(int(time.time() * 1000))
        params = {
            "api_key": "51job", "timestamp": ts, "keyword": "机械工程师",
            "searchType": "2", "function": "", "industry": "",
            "jobArea": "000000", "jobArea2": "", "landmark": "", "metro": "",
            "salary": "", "workYear": "", "degree": "", "companyType": "",
            "companySize": "", "jobType": "", "issueDate": "",
            "sortType": "0", "pageNum": "1", "pageSize": "100",
        }
        qs = urlencode(params)
        sig = hmac.new(HMAC_KEY, f"/api/job/search-pc?{qs}".encode(), hashlib.sha256).hexdigest()
        url = f"https://we.51job.com/api/job/search-pc?{qs}&signature={sig}"

        result = await page.evaluate("""
            async (url) => {
                const resp = await fetch(url, {credentials: 'include'});
                const text = await resp.text();
                const data = JSON.parse(text);
                const items = data?.resultbody?.job?.items || [];
                return {items, total: data?.resultbody?.job?.totalCount || 0};
            }
        """, url)
        await context.close()

    items = result.get("items", [])
    total = result.get("total", 0)
    print(f"\nFetched {len(items)} items (total={total})")

    if not items:
        print("No items! IP may be rate-limited. Try after cooldown.")
        return

    # Run build_row on the first item
    row = build_row(1, items[0], "工程/机械")

    print(f"\n--- build_row output (first item) ---")
    for k, v in row.items():
        status = "✓" if v and v != "/" else "✗ EMPTY"
        print(f"  {status} {repr(k)}: {repr(v)[:100]}")

    # Check all items
    print(f"\n--- Field coverage across all {len(items)} items ---")
    all_keys = set()
    for item in items:
        all_keys.update(item.keys())
    print(f"API response has {len(all_keys)} distinct field keys")

    # Check each row
    filled_cols = {col: 0 for col in cols}
    for idx, item in enumerate(items):
        row = build_row(idx + 1, item, "工程/机械")
        for col in cols:
            if row.get(col) and row.get(col) != "/":
                filled_cols[col] += 1

    print(f"\nFill rate per column ({len(items)} items):")
    for col in cols:
        count = filled_cols[col]
        pct = count / len(items) * 100
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        print(f"  {pct:5.0f}% {bar} {count:3d}/{len(items):3d}  {repr(col)[:40]}")

    # Summary
    total_fields = len(cols)
    fully_filled = sum(1 for col in cols if filled_cols[col] == len(items))
    mostly_filled = sum(1 for col in cols if filled_cols[col] >= len(items) * 0.8)
    print(f"\nSummary:")
    print(f"  Fully filled (100%): {fully_filled}/{total_fields}")
    print(f"  Mostly filled (80%+): {mostly_filled}/{total_fields}")


if __name__ == "__main__":
    asyncio.run(main())
