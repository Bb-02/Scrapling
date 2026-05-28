"""
Test SEO job-list endpoints from we.51job.com (cross-origin, like the real app).
These returned HTTP 200 in the captured API calls.
"""
import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright

PROFILE_DIR = Path(__file__).parent / "chrome_profile"


async def fetch_from_page(page, url, headers=None):
    """Fetch from we.51job.com page (cross-origin to cupid)."""
    h = headers or {}
    h_str = json.dumps(h)
    js = f"""
    async (url) => {{
        try {{
            const resp = await fetch(url, {{
                credentials: 'include',
                headers: {h_str},
            }});
            const text = await resp.text();
            let parsed = null;
            try {{ parsed = JSON.parse(text); }} catch(e) {{}}
            return {{
                ok: resp.ok,
                status: resp.status,
                textLen: text.length,
                preview: text.substring(0, 800),
                parsedKeys: parsed ? Object.keys(parsed) : null,
                itemsLen: parsed?.resultbody?.items?.length || parsed?.resultbody?.job?.items?.length || 0,
            }};
        }} catch(e) {{
            return {{ error: e.message }};
        }}
    }}
    """
    return await page.evaluate(js, url)


async def main():
    print("=== Testing SEO endpoints from we.51job.com ===\n")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # Navigate to we.51job.com (important: this is where the real app makes cupid calls from)
        print("[1] Opening we.51job.com search page...")
        await page.goto(
            "https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(4)

        has_token = await page.evaluate("() => !!localStorage.getItem('token')")
        print(f"Logged in: {has_token}\n")

        ts = str(int(time.time()))
        headers_no_auth = {
            "accept": "application/json, text/plain, */*",
        }
        headers_with_token = {
            "user-token": await page.evaluate("() => localStorage.getItem('token') || ''"),
            "accept": "application/json, text/plain, */*",
        }

        # Test with / without user-token (custom header may trigger CORS preflight)
        for label, hdrs in [("with user-token", headers_with_token), ("without user-token", headers_no_auth)]:
            print(f"\n--- Testing {label} ---")
            tests = [
                ("SEO-xy", f"https://cupid.51job.com/open/noauth/jobs/seo-job-list/xy?api_key=51job&timestamp={ts}"),
                ("SEO-normal", f"https://cupid.51job.com/open/noauth/jobs/seo-job-list/normal?api_key=51job&timestamp={ts}"),
                ("search-pc", f"https://we.51job.com/api/job/search-pc?api_key=51job&timestamp={ts}&keyword=机械工程师&searchType=2&sortType=0&jobArea=000000&pageNum=1&pageSize=20"),
            ]

            for name, url in tests:
                print(f"  [{name}]", end=" ")
                result = await fetch_from_page(page, url, hdrs)
                ok = result.get("ok", False)
                err = result.get("error", "")
                if err:
                    print(f"ERROR: {err}")
                elif ok:
                    print(f"OK! textLen={result['textLen']}, itemsLen={result['itemsLen']}")
                    print(f"    Keys: {result.get('parsedKeys')}")
                    print(f"    Preview: {result.get('preview', '')[:400]}")
                else:
                    print(f"HTTP{result.get('status')} {result.get('preview','')[:200]}")

        await context.close()

    print("Done. If SEO endpoints return job data, we can use cupid instead of we.51job.com.")


if __name__ == "__main__":
    asyncio.run(main())
