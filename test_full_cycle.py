"""
Final test: verify 2 full cycles (40 req → clear → 40 req) = 80 total.
Past the 48-request limit. If this works, the crawler will work.
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

TRACKING_PATTERNS = [
    "acw_tc", "ssxmod_itna", "sensorsdata", "sajssdk",
    "Hm_lvt", "Hm_lpvt", "HMACCOUNT", "guid", "sensor",
    "ps", "_c_WBKFRo",
]


def build_url(keyword="机械工程师", job_area="000000", page_num=1):
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
    sig = hmac.new(HMAC_KEY, f"/api/job/search-pc?{qs}".encode(), hashlib.sha256).hexdigest()
    return f"https://we.51job.com/api/job/search-pc?{qs}&signature={sig}"


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
        let itemCount = 0, totalCount = 0;
        if (resp.headers.get('content-type')?.includes('json')) {
            try {
                const data = JSON.parse(text);
                itemCount = data?.resultbody?.job?.items?.length || 0;
                totalCount = data?.resultbody?.job?.totalCount || 0;
            } catch(e) {}
        }
        return { status: resp.status, itemCount, totalCount };
    } catch(e) {
        return { error: e.message };
    }
}
"""


async def clear_tracking(context, page):
    cookies = await context.cookies()
    deleted = 0
    for c in cookies:
        name = c["name"]
        domain = c.get("domain", "")
        if "51job" not in domain:
            continue
        if any(name.startswith(p) or p in name for p in TRACKING_PATTERNS):
            await context.clear_cookies(name=name, domain=domain)
            deleted += 1
    if deleted:
        print(f"  [CLEAR] Deleted {deleted} tracking cookies, reloading page...")
        await page.goto(
            "https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(3)
    return deleted


async def run_batch(page, context, batch_name, count):
    ok = empty = 0
    for i in range(count):
        url = build_url(page_num=(i % 80) + 1)
        result = await page.evaluate(FETCH_JS, url)

        err = result.get("error", "")
        if err:
            print(f"    [{i+1:3d}] ERROR: {err[:60]}")
        elif result.get("itemCount", 0) > 0:
            print(f"    [{i+1:3d}] OK  items={result['itemCount']}  total={result['totalCount']}")
            ok += 1
        else:
            print(f"    [{i+1:3d}] EMPTY  total={result.get('totalCount',0)}")
            empty += 1

    print(f"  [{batch_name}] ok={ok} empty={empty}")
    return ok


async def main():
    print("=" * 60)
    print("FINAL TEST: 2 cycles of 40 requests = 80 total")
    print("Limit is 48. If cycle 2 works, we win.")
    print("=" * 60)

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

        has_token = await page.evaluate("() => !!localStorage.getItem('token')")
        print(f"\nLogged in: {has_token}")
        if not has_token:
            print("ERROR: not logged in!")
            await context.close()
            return

        # === Cycle 1: 40 requests ===
        print("\n--- Cycle 1: 40 requests ---")
        await run_batch(page, context, "C1", 40)

        # === Clear cookies ===
        print("\n--- Clearing tracking cookies ---")
        await clear_tracking(context, page)

        # === Cycle 2: 40 requests (would be request #41-#80) ===
        print("\n--- Cycle 2: 40 requests (should pass the 48 limit) ---")
        ok2 = await run_batch(page, context, "C2", 40)

        await context.close()

    print("\n" + "=" * 60)
    if ok2 >= 30:
        print("PASSED! Cookie-clearing resets the WAF counter.")
        print("Ready to run full crawler: python scrape_51job_fetch.py")
    elif ok2 > 0:
        print(f"MIXED: {ok2}/40 OK in cycle 2. Might need tweaking.")
    else:
        print("FAILED: cycle 2 all empty. IP cooldown may be needed.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
