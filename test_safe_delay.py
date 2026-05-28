"""
Find the minimum safe delay that never triggers the 48-request limit.
Also saves fetched data to verify quality.
"""
import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
from playwright.async_api import async_playwright

HMAC_KEY = b"abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
PROFILE_DIR = Path(__file__).parent / "chrome_profile"
OUTPUT_DIR = Path(__file__).parent.parent / "output"

TEST_DELAYS = [10.0, 8.0, 6.0, 4.0, 3.0]
REQUESTS_PER_DELAY = 60

# Diverse keywords for quality testing
TEST_KEYWORDS = [
    "机械工程师", "电气工程师", "结构工程师", "模具工程师", "质量管理",
]

TEST_AREAS = [
    ("全国", "000000"), ("北京", "010000"), ("上海", "020000"),
    ("广东", "200000"), ("江苏", "110000"), ("浙江", "120000"),
    ("四川", "230000"), ("湖北", "180000"), ("山东", "160000"),
    ("河南", "170000"),
]

TEMPLATE_PATH = Path(__file__).parent.parent / "数据模板.xlsx"


def build_url(keyword, job_area, page_num=1):
    ts = str(int(time.time() * 1000))
    params = {
        "api_key": "51job", "timestamp": ts, "keyword": keyword,
        "searchType": "2", "function": "", "industry": "",
        "jobArea": job_area, "jobArea2": "", "landmark": "", "metro": "",
        "salary": "", "workYear": "", "degree": "", "companyType": "",
        "companySize": "", "jobType": "", "issueDate": "",
        "sortType": "0", "pageNum": str(page_num), "pageSize": "100",
    }
    qs = urlencode(params)
    sig = hmac.new(HMAC_KEY, f"/api/job/search-pc?{qs}".encode(), hashlib.sha256).hexdigest()
    return f"https://we.51job.com/api/job/search-pc?{qs}&signature={sig}"


def read_template_columns():
    if TEMPLATE_PATH.exists():
        df = pd.read_excel(TEMPLATE_PATH, nrows=0)
        return [str(c).strip() for c in df.columns]
    return [f"col_{i}" for i in range(23)]


async def main():
    print("=== Find Safe Delay + Data Quality Test ===\n")
    print(f"Testing {REQUESTS_PER_DELAY} requests at each delay level")
    print(f"Keywords: {len(TEST_KEYWORDS)}, Areas: {len(TEST_AREAS)}")
    print(f"{'Delay':>6} {'OK':>5} {'Empty':>6} {'Result'}")
    print("-" * 45)

    all_items = []  # store raw items
    col_names = read_template_columns()

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
            print("ERROR: not logged in!")
            await context.close()
            return

        for delay in TEST_DELAYS:
            print(f"  Delay={delay}s: ", end="", flush=True)
            ok = empty = 0

            for i in range(REQUESTS_PER_DELAY):
                # Rotate keywords and areas each request
                kw = TEST_KEYWORDS[i % len(TEST_KEYWORDS)]
                area_name, area_code = TEST_AREAS[(i // len(TEST_KEYWORDS)) % len(TEST_AREAS)]
                url = build_url(kw, area_code, page_num=1)

                await asyncio.sleep(delay)

                try:
                    result = await page.evaluate("""
                        async (url) => {
                            try {
                                const resp = await fetch(url, {credentials: 'include'});
                                const text = await resp.text();
                                let items = [], total = 0;
                                if (resp.headers.get('content-type')?.includes('json')) {
                                    const data = JSON.parse(text);
                                    const job = data?.resultbody?.job || {};
                                    items = job.items || [];
                                    total = job.totalCount || 0;
                                }
                                return {status: resp.status, items, total};
                            } catch(e) { return {error: e.message}; }
                        }
                    """, url)
                except Exception as e:
                    msg = str(e)
                    if "navigation" in msg.lower():
                        print(f"\n    CAPTCHA at request #{i+1}!")
                        ok = -1
                        break
                    ok = -2
                    break

                items = result.get("items", [])
                if items:
                    ok += 1
                    for item in items:
                        all_items.append({
                            "keyword": kw,
                            "area": area_name,
                            "jobName": item.get("jobName", ""),
                            "fullCompanyName": item.get("fullCompanyName", ""),
                            "provideSalaryString": item.get("provideSalaryString", ""),
                            "jobAreaString": item.get("jobAreaString", ""),
                            "degreeString": item.get("degreeString", ""),
                            "workYearString": item.get("workYearString", ""),
                            "companySizeString": item.get("companySizeString", ""),
                            "jobHref": item.get("jobHref", ""),
                            "issueDateString": item.get("issueDateString", ""),
                        })
                else:
                    empty += 1

                if i % 20 == 19:
                    print(".", end="", flush=True)

            if ok == -1:
                print(f" SLIDER — IP dirty, stopping further tests")
                break
            elif ok == -2:
                print(f" ERROR")
                break
            elif ok >= 55:
                print(f" {ok}+  PASS")
                if delay == TEST_DELAYS[-1]:
                    print(f"\n  *** All delays safe! Fastest: {delay}s")
                # continue to try faster delay
            else:
                print(f" {ok} FAIL — rate limit hit, stopping")
                break

        await context.close()

    # ---- Save + quality report ----
    print(f"\n{'='*60}")
    print(f"Total items collected: {len(all_items)}")
    if not all_items:
        print("No data to save.")
        return

    # Dedup by jobHref
    seen = set()
    unique = []
    for item in all_items:
        url = item.get("jobHref", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(item)

    print(f"Unique items: {len(unique)}")

    # Quality check each item
    fields = ["jobName", "fullCompanyName", "provideSalaryString", "jobAreaString",
              "degreeString", "workYearString", "companySizeString", "jobHref"]
    complete = sum(1 for item in unique if all(item.get(f) for f in fields))
    missing_jobname = sum(1 for item in unique if not item.get("jobName"))
    missing_salary = sum(1 for item in unique if not item.get("provideSalaryString"))
    missing_company = sum(1 for item in unique if not item.get("fullCompanyName"))

    print(f"\nQuality check ({len(unique)} unique items):")
    print(f"  All 8 key fields present: {complete}/{len(unique)}")
    print(f"  Missing jobName: {missing_jobname}")
    print(f"  Missing salary: {missing_salary}")
    print(f"  Missing companyName: {missing_company}")

    # Save to Excel
    OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"delay_test_{stamp}.xlsx"
    df = pd.DataFrame(unique, columns=["keyword", "area"] + fields)
    df.to_excel(path, index=False)
    print(f"\nSaved to: {path}")
    print(f"Sample (first 5):")
    for _, row in df.head(5).iterrows():
        print(f"  [{row['keyword']}@{row['area']}] {row['jobName']} | {row['fullCompanyName']} | {row['provideSalaryString']}")


if __name__ == "__main__":
    asyncio.run(main())
