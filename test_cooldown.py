"""
Measure 51job cooldown — NO probing during wait (avoids resetting timer).
"""
import asyncio
import hashlib
import hmac
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from playwright.async_api import async_playwright

HMAC_KEY = b"abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
PROFILE_DIR = Path(__file__).parent / "chrome_profile"
WAIT_MINUTES = 20  # one clean wait, no probing


def build_url():
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
    return f"https://we.51job.com/api/job/search-pc?{qs}&signature={sig}"


FETCH_JS = """
async (url) => {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), 10000);
    try {
        const r = await fetch(url, {
            credentials: 'include', signal: c.signal,
            headers: {
                'user-token': localStorage.getItem('token') || '',
                'accept': 'application/json',
            }
        });
        clearTimeout(t);
        const txt = await r.text();
        if (txt.includes('aliyun_waf')) return 'blocked';
        const d = JSON.parse(txt);
        return d?.resultbody?.job?.items?.length > 0 ? 'ok' : 'empty';
    } catch(e) { clearTimeout(t); return 'blocked'; }
}
"""


async def trigger_and_close():
    """Fire requests until blocked, close browser, return trigger time."""
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        await page.goto(
            "https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(4)

        token = await page.evaluate("() => !!localStorage.getItem('token')")
        if not token:
            print("[FAIL] Not logged in!")
            await ctx.close()
            return None

        print("Triggering slider...")
        for i in range(300):
            await asyncio.sleep(0.2)
            try:
                result = await asyncio.wait_for(page.evaluate(FETCH_JS, build_url()), timeout=15)
            except Exception:
                print(f"  SLIDER after {i + 1} requests!")
                t = datetime.now()
                await ctx.close()
                return t
            if result != 'ok':
                print(f"  SLIDER after {i + 1} requests ({result})")
                t = datetime.now()
                await ctx.close()
                return t
            if (i + 1) % 10 == 0:
                print(f"  {i + 1} OK...")

        print("  300 passed — IP too clean!")
        await ctx.close()
        return None


async def test_once():
    """Open browser, one request, return True if unblocked."""
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        await page.goto(
            "https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
            wait_until="domcontentloaded", timeout=15000,
        )
        await asyncio.sleep(4)
        try:
            result = await asyncio.wait_for(page.evaluate(FETCH_JS, build_url()), timeout=15)
        except Exception:
            await ctx.close()
            return False
        await ctx.close()
        return result == 'ok'


async def main():
    print("=" * 50)
    print("51job IP Cooldown — Clean Test")
    print(f"Strategy: trigger → close → wait {WAIT_MINUTES}min → test ONCE")
    print("=" * 50)

    # Step 1: trigger block
    print("\n[Step 1] Trigger slider + close browser\n")
    trigger_time = await trigger_and_close()
    if trigger_time is None:
        print("Aborting.")
        return

    print(f"\nSlider at: {trigger_time.strftime('%H:%M:%S')}")
    print(f"Browser closed. Waiting {WAIT_MINUTES} min with ZERO requests...\n")

    # Step 2: wait — no browser, no requests
    for remaining in range(WAIT_MINUTES, 0, -1):
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {remaining} min remaining...", end='\r')
        time.sleep(60)
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] Wait complete!          ")

    # Step 3: test ONCE
    print(f"\n[Step 3] Opening browser to test...")
    ok = await test_once()
    elapsed = (datetime.now() - trigger_time).total_seconds() / 60

    if ok:
        safe = int((WAIT_MINUTES + 5) * 60)
        print(f"\n{'=' * 50}")
        print(f"RECOVERED after {elapsed:.0f} min wait!")
        print(f"Set COOLDOWN_SECONDS = {safe} ({WAIT_MINUTES + 5} min)")
        print(f"{'=' * 50}")
    else:
        print(f"\n{'=' * 50}")
        print(f"Still BLOCKED after {elapsed:.0f} min!")
        print(f"Cooldown > {WAIT_MINUTES} min — try longer wait next time")
        print(f"{'=' * 50}")


if __name__ == "__main__":
    asyncio.run(main())
