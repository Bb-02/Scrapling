"""
Test: can we increase pageSize to get more items per API call?
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


def build_url(page_size=20, page_num=1):
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
    print("=== Test pageSize parameter ===\n")

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

        for page_size in [20, 30, 40, 50, 60, 80, 100]:
            url = build_url(page_size=page_size)
            result = await page.evaluate("""
                async (url) => {
                    const resp = await fetch(url, {credentials: 'include'});
                    const text = await resp.text();
                    let items = 0, total = 0;
                    if (resp.headers.get('content-type')?.includes('json')) {
                        try {
                            const data = JSON.parse(text);
                            items = data?.resultbody?.job?.items?.length || 0;
                            total = data?.resultbody?.job?.totalCount || 0;
                        } catch(e) {}
                    }
                    return {status: resp.status, items, total};
                }
            """, url)

            status = result.get("status", "?")
            items = result.get("itemCount", result.get("items", 0))
            total = result.get("totalCount", result.get("total", 0))
            if items > 0:
                print(f"  pageSize={page_size:>3}: HTTP{status}  items={items:>3}  total={total}  ✓")
            else:
                print(f"  pageSize={page_size:>3}: HTTP{status}  items={items:>3}  total={total}  ✗")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
