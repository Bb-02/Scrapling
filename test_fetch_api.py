"""
One-shot test: login once, verify page works, then test fetch() rate limit.
Login state persists to chrome_profile/ for future runs.
"""

import asyncio
import json
import time
import hashlib
import hmac
import random
import shutil
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import async_playwright

HMAC_KEY = b"abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
PROFILE_DIR = Path(__file__).parent / "chrome_profile"


def build_url(keyword="%E6%9C%BA%E6%A2%B0%E5%B7%A5%E7%A8%8B%E5%B8%88", job_area="000000", page_num=1):
    ts = str(int(time.time() * 1000))  # ms for uniqueness
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


async def safe_evaluate(page, js, arg):
    """Evaluate JS without crashing on navigation."""
    try:
        return await page.evaluate(js, arg)
    except Exception as e:
        msg = str(e)
        if "navigation" in msg.lower() or "destroyed" in msg.lower():
            return {"error": "page_navigated"}
        return {"error": msg[:80]}


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
        const isWaf = text.includes('aliyun_waf');
        let itemCount = 0, totalCount = 0;
        if (!isWaf && resp.headers.get('content-type')?.includes('json')) {
            try {
                const data = JSON.parse(text);
                itemCount = data?.resultbody?.job?.items?.length || 0;
                totalCount = data?.resultbody?.job?.totalCount || 0;
            } catch(e) {}
        }
        return { status: resp.status, isWaf, itemCount, totalCount };
    } catch(e) {
        return { error: e.message };
    }
}
"""


async def main():
    # Don't clean profile if exists - reuse login
    first_run = not PROFILE_DIR.exists()
    PROFILE_DIR.mkdir(exist_ok=True)

    async with async_playwright() as p:
        print("[1/4] Launching browser...")
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        print("[2/4] Opening 51job search page...")
        await page.goto(
            "https://we.51job.com/pc/search?keyword=%E6%9C%BA%E6%A2%B0%E5%B7%A5%E7%A8%8B%E5%B8%88&jobArea=000000",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(5)

        diag = await page.evaluate("""() => {
            const items = document.querySelectorAll('.joblist-item');
            const token = localStorage.getItem('token') || '';
            return { jobItems: items.length, hasToken: !!token };
        }""")
        print(f"    Page: {diag['jobItems']} job items, logged in: {diag['hasToken']}")

        if first_run and not diag["hasToken"]:
            print("    [LOGIN] Login in the browser, then press Enter...")
            input()
            await asyncio.sleep(5)
            diag = await page.evaluate("""() => {
                const items = document.querySelectorAll('.joblist-item');
                const token = localStorage.getItem('token') || '';
                return { jobItems: items.length, hasToken: !!token };
            }""")
            print(f"    After login: {diag['jobItems']} job items, hasToken: {diag['hasToken']}")

        if diag["jobItems"] == 0:
            print("[WARN] 0 job items. IP may be blocked. Check the browser manually.")
            print("If you see job listings, press Enter to proceed. Otherwise Ctrl+C.")
            try:
                input()
            except KeyboardInterrupt:
                await context.close()
                return

        print(f"\n[3/4] Page OK. Running 100 fetch() calls with 0.5-1.5s delay...")
        print(f"      (Delay prevents slider captcha)")
        print(f"{'#':>5} {'HTTP':>5} {'items':>8} {'total':>10} status")
        print("-" * 55)

        results = []
        start_time = time.time()

        for i in range(100):
            url = build_url(page_num=(i % 80) + 1)

            # Random delay 0.5-1.5s to avoid triggering captcha
            delay = random.uniform(0.5, 1.5)
            await asyncio.sleep(delay)

            result = await safe_evaluate(page, FETCH_JS, url)

            status = result.get("status", "?")
            is_waf = result.get("isWaf", False)
            cnt = result.get("itemCount", 0)
            total = result.get("totalCount", 0)
            error = result.get("error", "")

            if error == "page_navigated":
                print(f"{i+1:5d}  {'NAV':>5}  {'-':>8}  {'-':>10} page navigated, stopping")
                break
            elif error:
                print(f"{i+1:5d} {'ERR':>5} {'-':>8} {'-':>10} {error[:35]}")
                results.append(("error", error))
            elif is_waf:
                print(f"{i+1:5d} {status:>5} {'-':>8} {'-':>10} WAF-BLOCKED")
                results.append(("waf", None))
            elif cnt > 0:
                print(f"{i+1:5d} {status:>5} {cnt:>8} {total:>10} OK")
                results.append(("ok", cnt))
            else:
                print(f"{i+1:5d} {status:>5} {cnt:>8} {total:>10} empty")
                results.append(("empty", 0))

        elapsed = time.time() - start_time
        ok_count = sum(1 for r in results if r[0] == "ok")
        waf_count = sum(1 for r in results if r[0] == "waf")
        first_waf_at = next((i + 1 for i, r in enumerate(results) if r[0] == "waf"), None)

        print(f"\n[4/4] {'=' * 60}")
        print(f"Completed {len(results)} requests in {elapsed:.1f}s")
        print(f"Success: {ok_count}, Empty: {sum(1 for r in results if r[0]=='empty')}, WAF-blocked: {waf_count}")
        if first_waf_at:
            print(f"First WAF block at request #{first_waf_at}")
        elif ok_count >= 50:
            print("No WAF blocks! fetch() with delay avoids rate limiting!")
            print("Estimated: ~1200 req/hr per browser, 7 categories in 2-4 hours")
        else:
            print(f"Only {ok_count} successful. May need longer delays.")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
