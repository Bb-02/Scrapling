"""
Quick test: cupid search endpoints via browser fetch().
Only 20 requests, uses existing chrome_profile (no re-login).
"""
import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import async_playwright

HMAC_KEY = b"abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
PROFILE_DIR = Path(__file__).parent / "chrome_profile"

CUPID_ENDPOINTS = [
    "https://cupid.51job.com/open/noauth/job/search-pc",
    "https://cupid.51job.com/api/job/search-pc",
    "https://cupid.51job.com/open/noauth/job/search",
]


def build_url(base: str, keyword="机械工程师", job_area="000000", page_num=1):
    ts = str(int(time.time() * 1000))
    params = {
        "api_key": "51job", "timestamp": ts, "keyword": keyword,
        "searchType": "2", "function": "", "industry": "",
        "jobArea": job_area, "jobArea2": "", "landmark": "", "metro": "",
        "salary": "", "workYear": "", "degree": "", "companyType": "",
        "companySize": "", "jobType": "", "issueDate": "",
        "sortType": "0", "pageNum": str(page_num), "pageSize": "20",
    }
    qs = urlencode(params)

    from urllib.parse import urlparse
    path = urlparse(base).path
    sig = hmac.new(HMAC_KEY, f"{path}?{qs}".encode(), hashlib.sha256).hexdigest()
    return f"{base}?{qs}&signature={sig}"


FETCH_JS = """
async (url) => {
    try {
        const resp = await fetch(url, {
            credentials: 'include',
            headers: {
                'user-token': localStorage.getItem('token') || '',
                'accept': 'application/json, text/plain, */*',
            }
        });
        const text = await resp.text();
        const ct = resp.headers.get('content-type') || '';
        let preview = text.substring(0, 200);
        let itemCount = 0, totalCount = 0;
        if (ct.includes('json')) {
            try {
                const data = JSON.parse(text);
                itemCount = data?.resultbody?.job?.items?.length || 0;
                totalCount = data?.resultbody?.job?.totalCount || 0;
            } catch(e) {}
        }
        return { status: resp.status, ct, preview, itemCount, totalCount };
    } catch(e) {
        return { error: e.message };
    }
}
"""


async def main():
    print("Testing cupid search endpoints via browser fetch()")
    print("=" * 60)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # Navigate to we.51job.com to establish session
        print("\nOpening 51job to establish session...")
        await page.goto(
            "https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(4)

        has_token = await page.evaluate("() => !!localStorage.getItem('token')")
        print(f"Logged in: {has_token}")
        if not has_token:
            print("ERROR: No token. Login first.")
            await context.close()
            return

        for endpoint in CUPID_ENDPOINTS:
            print(f"\n--- Endpoint: {endpoint} ---")
            success = 0
            for i in range(5):
                url = build_url(endpoint, page_num=i + 1)
                result = await page.evaluate(FETCH_JS, url)
                status = result.get("status", "?")
                ct = result.get("ct", "?")
                items = result.get("itemCount", 0)
                total = result.get("totalCount", 0)
                err = result.get("error", "")
                preview = result.get("preview", "")

                if err:
                    print(f"  [{i+1}] ERROR: {err[:80]}")
                elif items > 0:
                    print(f"  [{i+1}] HTTP{status}  items={items}  total={total}  OK!")
                    success += 1
                elif "json" in ct.lower():
                    print(f"  [{i+1}] HTTP{status}  items=0  total={total}  json-empty")
                elif status == 404:
                    print(f"  [{i+1}] HTTP404  not found")
                else:
                    print(f"  [{i+1}] HTTP{status}  ct={ct[:40]}  {preview[:80]}")

            if success > 0:
                print(f"  >>> SUCCESS! {success}/5 returned data")

        await context.close()

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
