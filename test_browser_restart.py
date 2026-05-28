"""
Test: does restarting browser reset the 48-request counter?
Uses existing chrome_profile, no re-login needed.
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
    path = "/api/job/search-pc"
    sig = hmac.new(HMAC_KEY, f"{path}?{qs}".encode(), hashlib.sha256).hexdigest()
    return f"https://we.51job.com{path}?{qs}&signature={sig}"


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
        const isWaf = text.includes('aliyun_waf');
        return { status: resp.status, isWaf, itemCount, totalCount };
    } catch(e) {
        return { error: e.message };
    }
}
"""


async def run_batch(page, label, count):
    ok = empty = err_count = 0
    for i in range(count):
        url = build_url(page_num=(i % 80) + 1)
        result = await page.evaluate(FETCH_JS, url)

        e = result.get("error", "")
        if e:
            print(f"  [{i+1:3d}] ERROR: {e[:60]}")
            err_count += 1
        elif result.get("isWaf"):
            print(f"  [{i+1:3d}] WAF-BLOCKED")
            err_count += 1
        elif result.get("itemCount", 0) > 0:
            print(f"  [{i+1:3d}] OK  items={result['itemCount']}  total={result['totalCount']}")
            ok += 1
        else:
            print(f"  [{i+1:3d}] EMPTY  total={result.get('totalCount',0)}")
            empty += 1

    print(f"  [{label}] ok={ok}  empty={empty}  err={err_count}")
    return ok, empty, err_count


async def main():
    print("=== Browser Restart Counter Reset Test ===")
    print(f"Using profile: {PROFILE_DIR}")
    print()

    # ---- Round 1 ----
    print("[Round 1] Opening browser, doing 40 requests...")
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
        print(f"Logged in: {has_token}\n")
        if not has_token:
            print("ERROR: not logged in")
            await context.close()
            return

        await run_batch(page, "Round1", 40)
        await context.close()
        print("Browser closed.\n")

    # ---- Pause ----
    print("[Pause] 5 seconds...\n")
    await asyncio.sleep(5)

    # ---- Round 2: fresh browser, same profile ----
    print("[Round 2] Re-opening browser with SAME profile, 20 more requests...")
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

        await run_batch(page, "Round2", 20)
        await context.close()
        print("Browser closed.\n")

    print("Done. If Round2 got data past request #48, restarting works.")
    print("If Round2 was all empty/WAF, the counter survives browser restart.")


if __name__ == "__main__":
    asyncio.run(main())
