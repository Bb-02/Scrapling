"""
Test cupid noauth endpoints.
Strategy: navigate to cupid page first (same origin), then fetch API.
No CORS, no WAF (cupid doesn't have Alibaba WAF).
"""
import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

from playwright.async_api import async_playwright

HMAC_KEY = b"abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
PROFILE_DIR = Path(__file__).parent / "chrome_profile"


def sign_url(base: str, **params) -> str:
    """Build signed cupid URL with HMAC-SHA256."""
    ts = str(int(time.time()))
    all_params = {"api_key": "51job", "timestamp": ts, **params}
    qs = urlencode(all_params)
    path = urlparse(base).path
    sig = hmac.new(HMAC_KEY, f"{path}?{qs}".encode(), hashlib.sha256).hexdigest()
    return f"{base}?{qs}&signature={sig}"


async def fetch_json(page, url):
    """Fetch JSON with correct headers."""
    js = """
    async (url) => {
        try {
            const resp = await fetch(url, {
                credentials: 'include',
                headers: {
                    'accept': 'application/json, text/plain, */*',
                    'user-token': localStorage.getItem('token') || '',
                }
            });
            const text = await resp.text();
            let data = null;
            try { data = JSON.parse(text); } catch(e) {}
            return {
                status: resp.status,
                contentType: resp.headers.get('content-type') || '',
                textPreview: text.substring(0, 500),
                data: data ? JSON.stringify(data).substring(0, 500) : null,
            };
        } catch(e) {
            return { error: e.message };
        }
    }
    """
    return await page.evaluate(js, url)


async def goto_json(page, url):
    """Navigate to URL and read JSON body directly."""
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
    if not resp:
        return {"error": "no response"}
    text = await page.evaluate("() => document.body?.innerText || document.body?.textContent || ''")
    return {
        "status": resp.status,
        "contentType": resp.headers.get("content-type", ""),
        "text": text[:800],
    }


async def main():
    print("=== cupid noauth endpoint test ===\n")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # Step 1: Open any cupid page to get into cupid origin
        print("[1] Navigating to cupid to establish origin...")
        await page.goto("https://cupid.51job.com", wait_until="domcontentloaded")
        await asyncio.sleep(3)
        print(f"    Current page: {page.url}\n")

        # Step 2: Try fetching cupid APIs from cupid origin (no CORS!)
        ts = str(int(time.time()))
        endpoints = {
            "SEO-xy": sign_url(
                "https://cupid.51job.com/open/noauth/jobs/seo-job-list/xy",
            ),
            "SEO-normal": sign_url(
                "https://cupid.51job.com/open/noauth/jobs/seo-job-list/normal",
            ),
            "recommend": sign_url(
                "https://cupid.51job.com/open/noauth/recommend/web",
                pageSize="50", pageNum="1", type="recommend", source="1",
            ),
            "search-noauth": sign_url(
                "https://cupid.51job.com/open/noauth/job/search-pc",
                keyword="机械工程师", jobArea="000000",
                searchType="2", sortType="0", pageNum="1", pageSize="20",
            ),
        }

        for name, url in endpoints.items():
            print(f"  [{name}] fetching...")
            result = await fetch_json(page, url)
            status = result.get("status", "?")
            ct = result.get("contentType", "")
            err = result.get("error", "")
            if err:
                print(f"    ERROR: {err}")
            elif "json" in ct.lower():
                print(f"    HTTP{status} JSON  preview: {result.get('data', result.get('textPreview',''))[:300]}")
            else:
                print(f"    HTTP{status} ct={ct[:60]}  {result.get('textPreview','')[:200]}")
            print()

        # Step 3: Also try via direct navigation (page.goto to the JSON URL)
        print("[2] Trying direct navigation to cupid API URLs...\n")
        for name in ["SEO-xy", "search-noauth"]:
            url = endpoints[name]
            print(f"  [{name}] goto...")
            result = await goto_json(page, url)
            print(f"    HTTP{result.get('status','?')}  {result.get('text','')[:300]}")
            print()
            await page.goto("https://cupid.51job.com", wait_until="domcontentloaded")
            await asyncio.sleep(2)

        await context.close()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
