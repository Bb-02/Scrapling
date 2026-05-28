"""
Diagnose: list all localStorage keys and cookies for we.51job.com.
Goal: find the tracking/WAF counter key without touching login token.
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

PROFILE_DIR = Path(__file__).parent / "chrome_profile"


async def main():
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

        # ---- localStorage ----
        ls = await page.evaluate("""
            () => {
                const result = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const key = localStorage.key(i);
                    const val = localStorage.getItem(key);
                    result[key] = val.length > 120 ? val.substring(0, 120) + '...' : val;
                }
                return result;
            }
        """)
        print("=" * 60)
        print("localStorage keys:")
        print("=" * 60)
        for k, v in sorted(ls.items()):
            marker = " *** LOGIN ***" if k in ("token", "user-token", "userToken") else ""
            print(f"  {k}: {v}{marker}")

        # ---- cookies for 51job domains ----
        cookies = await context.cookies()
        c51 = [c for c in cookies if "51job" in c.get("domain", "")]
        print(f"\n{'=' * 60}")
        print(f"Cookies for 51job domains ({len(c51)} total):")
        print("=" * 60)
        for c in c51:
            domain = c["domain"]
            name = c["name"]
            val = c["value"][:120]
            http = "HttpOnly" if c.get("httpOnly") else ""
            secure = "Secure" if c.get("secure") else ""
            print(f"  [{domain}] {name} = {val}  {http} {secure}")

        # ---- sessionStorage ----
        ss = await page.evaluate("""
            () => {
                const result = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    const val = sessionStorage.getItem(key);
                    result[key] = val.length > 120 ? val.substring(0, 120) + '...' : val;
                }
                return result;
            }
        """)
        print(f"\n{'=' * 60}")
        print("sessionStorage keys:")
        print("=" * 60)
        if ss:
            for k, v in sorted(ss.items()):
                print(f"  {k}: {v}")
        else:
            print("  (empty)")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
