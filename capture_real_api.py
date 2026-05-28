"""
Intercept REAL API calls made by the 51job Vue app during natural page load.
This captures exact URLs, headers, and request bodies for all API calls.
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

PROFILE_DIR = Path(__file__).parent / "chrome_profile"


async def main():
    captured = []

    async def on_request(request):
        url = request.url
        if "51job.com/api" in url or "cupid.51job.com" in url:
            headers = dict(request.headers)
            # Mask sensitive values
            for k in list(headers.keys()):
                if k.lower() in ("cookie", "user-token"):
                    headers[k] = headers[k][:60] + "..."
            captured.append({
                "url": url,
                "method": request.method,
                "headers": headers,
                "post_data": request.post_data,
            })

    async def on_response(response):
        url = response.request.url
        for c in captured:
            if c["url"] == url and "status" not in c:
                c["status"] = response.status
                c["content_type"] = response.headers.get("content-type", "")
                try:
                    body = response.body()
                    c["body_preview"] = body[:500].decode("utf-8", errors="replace")
                except Exception:
                    pass
                break

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        page.on("request", on_request)
        page.on("response", on_response)

        print("Navigating to search page (this triggers natural API calls)...")
        await page.goto(
            "https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
            wait_until="networkidle",
        )
        await asyncio.sleep(5)

        # Now change area to trigger a new search
        print("Changing area to 北京 to trigger fresh API call...")
        try:
            await page.evaluate("""
                () => {
                    const select = document.querySelector('.area-select, [class*="area"], [class*="city"], select');
                    if (select) {
                        select.value = '010000';
                        select.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            """)
            await asyncio.sleep(5)
        except Exception as e:
            print(f"  (area change failed: {e})")

        await context.close()

    # Print captured APIs
    print(f"\n{'='*60}")
    print(f"Captured {len(captured)} API calls:")
    print(f"{'='*60}")
    for i, c in enumerate(captured):
        print(f"\n[{i+1}] {c['method']} [{c.get('status','?')}] {c['url'][:200]}")
        if c.get('content_type'):
            print(f"    Content-Type: {c['content_type']}")
        if c.get('body_preview'):
            print(f"    Body: {c['body_preview'][:200]}")
        if c.get('post_data'):
            print(f"    PostData: {c['post_data'][:200]}")
        # Print notable headers
        notable = ["x-ca", "signature", "user-token", "authorization", "x-", "referer"]
        for hk, hv in c.get("headers", {}).items():
            if any(n in hk.lower() for n in notable):
                print(f"    Header[{hk}]: {hv}")

    # Save full capture
    with open(Path(__file__).parent / "real_api_captures.json", "w", encoding="utf-8") as f:
        json.dump(captured, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to real_api_captures.json")


if __name__ == "__main__":
    asyncio.run(main())
