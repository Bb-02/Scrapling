"""
Test: clear acw_tc (Alibaba WAF tracking cookie) to reset counter.
Keeps login cookies (uid, 51job, 51jobv10) and localStorage token.
No re-login needed.
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

# Cookies safe to delete (tracking only, not auth)
TRACKING_COOKIE_PATTERNS = [
    "acw_tc",              # Alibaba WAF tracker
    "ssxmod_itna", "ssxmod_itna2",  # Sensorsdata
    "sensorsdata", "sajssdk",       # Sensorsdata
    "Hm_lvt", "Hm_lpvt",           # Baidu analytics
    "HMACCOUNT",                     # Baidu analytics
    "guid",                          # Device GUID
    "sensor",                        # Sensorsdata
    "ps",                            # Unknown (platform session?)
    "_c_WBKFRo",                    # Unknown tracking
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


async def main():
    print("=== Clear acw_tc Cookie Test ===\n")
    print("This will delete Alibaba WAF tracking cookies while")
    print("keeping login cookies (uid, 51job, 51jobv10).\n")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        await page.goto("https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
                        wait_until="domcontentloaded")
        await asyncio.sleep(4)

        has_token = await page.evaluate("() => !!localStorage.getItem('token')")
        print(f"Logged in: {has_token}")
        if not has_token:
            print("ERROR: not logged in")
            await context.close()
            return

        # Show cookies before cleaning
        cookies_before = await context.cookies()
        c51_before = [c for c in cookies_before if "51job" in c.get("domain", "")]
        print(f"\n51job cookies before cleaning ({len(c51_before)}):")
        for c in c51_before:
            name = c["name"]
            keep = not any(name.startswith(p) or p in name for p in TRACKING_COOKIE_PATTERNS)
            print(f"  [{c['domain']}] {name}  {'[KEEP]' if keep else '[DELETE]'}")

        # Delete tracking cookies
        deleted = 0
        kept = 0
        for c in cookies_before:
            name = c["name"]
            is_tracking = any(name.startswith(p) or p in name for p in TRACKING_COOKIE_PATTERNS)
            if is_tracking and "51job" in c.get("domain", ""):
                await context.clear_cookies(name=name, domain=c["domain"])
                deleted += 1
            elif "51job" in c.get("domain", ""):
                kept += 1

        print(f"\nDeleted: {deleted} tracking cookies")
        print(f"Kept: {kept} auth cookies")

        # Verify cookies after
        cookies_after = await context.cookies()
        c51_after = [c for c in cookies_after if "51job" in c.get("domain", "")]
        print(f"51job cookies after cleaning ({len(c51_after)}):")
        for c in c51_after:
            print(f"  [{c['domain']}] {c['name']}")

        # Quick test: 10 requests (should be well under limit if counter reset)
        print(f"\n--- Testing: 15 fetch() calls ---")
        ok = empty = 0
        for i in range(15):
            url = build_url(page_num=i + 1)
            result = await page.evaluate(FETCH_JS, url)

            err = result.get("error", "")
            if err:
                print(f"  [{i+1:3d}] ERROR: {err[:60]}")
            elif result.get("itemCount", 0) > 0:
                print(f"  [{i+1:3d}] OK  items={result['itemCount']}  total={result['totalCount']}")
                ok += 1
            else:
                print(f"  [{i+1:3d}] EMPTY  total={result.get('totalCount',0)}")
                empty += 1

        print(f"\nResult: ok={ok} empty={empty}")

        # Check if token still valid
        still_has_token = await page.evaluate("() => !!localStorage.getItem('token')")
        print(f"Token preserved: {still_has_token}")

        if ok == 15:
            print("\n*** SUCCESS! All 15 requests returned data. Counter was reset! ***")
            print("This means deleting acw_tc resets the 48-request WAF limit.")
        elif ok > 0:
            print(f"\nPartial success: {ok}/15 returned data.")
        else:
            print("\nAll empty. Counter may not have been reset via cookies alone.")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
