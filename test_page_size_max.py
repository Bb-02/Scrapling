"""
Find max pageSize and verify data integrity.
Tests: 20, 50, 100, 150, 200, 300, 500
Verifies: item count matches pageSize, jobName present in all items.
"""
import asyncio
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import async_playwright

HMAC_KEY = b"abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
PROFILE_DIR = Path(__file__).parent / "chrome_profile"


def build_url(page_size, page_num=1):
    ts = str(int(time.time() * 1000))
    params = {
        "api_key": "51job", "timestamp": ts, "keyword": "机械工程师",
        "searchType": "2", "function": "", "industry": "",
        "jobArea": "000000", "jobArea2": "", "landmark": "", "metro": "",
        "salary": "", "workYear": "", "degree": "", "companyType": "",
        "companySize": "", "jobType": "", "issueDate": "",
        "sortType": "0", "pageNum": str(page_num), "pageSize": str(page_size),
    }
    qs = urlencode(params)
    sig = hmac.new(HMAC_KEY, f"/api/job/search-pc?{qs}".encode(), hashlib.sha256).hexdigest()
    return f"https://we.51job.com/api/job/search-pc?{qs}&signature={sig}"


async def main():
    print("=== Max pageSize + integrity test ===\n")
    print(f"{'size':>5} {'status':>6} {'items':>6} {'total':>8} {'fieldsOK':>9} note")
    print("-" * 65)

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

        for size in [20, 50, 100, 150, 200, 300, 500]:
            url = build_url(page_size=size)
            result = await page.evaluate("""
                async (url) => {
                    const resp = await fetch(url, {credentials: 'include'});
                    const text = await resp.text();
                    if (!resp.headers.get('content-type')?.includes('json'))
                        return {status: resp.status, items: 0, total: 0, note: 'not_json'};
                    const data = JSON.parse(text);
                    const job = data?.resultbody?.job || {};
                    const items = job.items || [];
                    // Check key fields present in all items
                    let fieldsOk = true;
                    if (items.length > 0) {
                        for (const item of items) {
                            if (!item.jobName || !item.jobId) { fieldsOk = false; break; }
                        }
                    }
                    return {
                        status: resp.status,
                        items: items.length,
                        total: job.totalCount || 0,
                        fieldsOk: fieldsOk,
                        note: items.length > 0 ? 'ok' : 'empty',
                    };
                }
            """, url)

            status = result.get("status", "?")
            items = result.get("items", 0)
            total = result.get("total", 0)
            fields_ok = result.get("fieldsOk", False)
            note = result.get("note", "")

            marker = "✓" if items > 0 and fields_ok else "✗"
            print(f"  {size:>4}  HTTP{status:>4}  {items:>5}  {total:>8}  {'YES' if fields_ok else 'NO':>8}   {marker}")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
