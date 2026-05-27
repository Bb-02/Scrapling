import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
from playwright.sync_api import sync_playwright

TEMPLATE_PATH = Path("../数据模板.xlsx")
OUTPUT_DIR = Path("../output")
BASE_SEARCH_URL = "https://we.51job.com/pc/search"
DEFAULT_PLATFORM = "前程无忧"
DEFAULT_CATEGORY = "工程/机械"


def build_search_url(keyword: str, page_num: int, job_area: str | None) -> str:
    params = {
        "keyword": keyword,
        "searchType": "2",
        "pageNum": str(page_num),
    }
    if job_area:
        params["jobArea"] = job_area
    return f"{BASE_SEARCH_URL}?{urlencode(params)}"


def parse_company_size(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"(\d+[-~]\d+人|\d+人以上|少于\d+人|\d+人)", text)
    return match.group(1) if match else ""


def split_location(area_text: str) -> tuple[str, str]:
    if not area_text:
        return "", ""
    city = re.split(r"[-·]", area_text)[0].strip()
    return "", city


def split_job_desc(desc_text: str) -> tuple[str, str]:
    if not desc_text:
        return "", ""
    text = desc_text.replace("\r", "").strip()
    if "任职要求" in text:
        parts = text.split("任职要求", 1)
        return parts[0].strip(), f"任职要求{parts[1].strip()}"
    return text, ""


def read_template_columns() -> list[str]:
    df = pd.read_excel(TEMPLATE_PATH, nrows=0)
    return [str(col).strip() for col in df.columns]


def extract_list_items(page) -> list[dict]:
    page.wait_for_selector(".joblist-item", timeout=30000)
    items = []
    for item in page.query_selector_all(".joblist-item"):
        title_el = item.query_selector(".jname")
        salary_el = item.query_selector(".sal")
        area_el = item.query_selector(".joblist-item-jobinfo .area")
        company_el = item.query_selector(".cname")
        comp_el = item.query_selector(".comp")
        link_el = item.query_selector("a")
        sensors_el = item.query_selector(".joblist-item-job")

        title = title_el.inner_text().strip() if title_el else ""
        salary = salary_el.inner_text().strip() if salary_el else ""
        area = area_el.inner_text().strip() if area_el else ""
        company = company_el.inner_text().strip() if company_el else ""
        comp_info = comp_el.inner_text().strip() if comp_el else ""
        job_link = link_el.get_attribute("href") if link_el else ""

        tags = [
            t.inner_text().strip()
            for t in item.query_selector_all(".joblist-item-tags .tag")
        ]

        job_time = ""
        if sensors_el:
            sensors_raw = sensors_el.get_attribute("sensorsdata") or ""
            try:
                sensors = json.loads(html.unescape(sensors_raw))
                job_time = sensors.get("jobTime", "")
            except json.JSONDecodeError:
                job_time = ""

        items.append(
            {
                "title": title,
                "salary": salary,
                "area": area,
                "company": company,
                "company_info": comp_info,
                "job_link": job_link,
                "job_time": job_time,
                "tags": tags,
            }
        )
    return items


def extract_detail(page) -> dict:
    page.wait_for_selector(".job-detail", timeout=30000)
    location_el = page.query_selector(".msg.ltype .type_2")
    experience_el = page.query_selector(".msg.ltype .type_3")
    education_el = page.query_selector(".msg.ltype .type_4")

    location = location_el.inner_text().strip() if location_el else ""
    experience = experience_el.inner_text().strip() if experience_el else ""
    education = education_el.inner_text().strip() if education_el else ""

    benefits = [
        t.inner_text().strip()
        for t in page.query_selector_all(".job-detail .tags .tag")
    ]
    desc_el = page.query_selector(".job_msg")
    desc_text = desc_el.inner_text().strip() if desc_el else ""
    job_content, requirements = split_job_desc(desc_text)

    category_el = page.query_selector("p:has-text('职能类别') a")
    category = category_el.inner_text().strip() if category_el else ""

    address_el = page.query_selector(".job-address")
    address = address_el.inner_text().strip() if address_el else ""

    return {
        "location": location,
        "experience": experience,
        "education": education,
        "benefits": benefits,
        "job_content": job_content,
        "requirements": requirements,
        "category": category,
        "address": address,
    }


def build_row(idx: int, list_item: dict, detail_item: dict) -> dict:
    area_text = detail_item.get("location") or list_item.get("area", "")
    province, city = split_location(area_text)
    company_size = parse_company_size(list_item.get("company_info", ""))

    return {
        "序号": idx,
        "招聘平台": DEFAULT_PLATFORM,
        "岗位类型\n一级": DEFAULT_CATEGORY,
        "岗位类型\n二级": detail_item.get("category", ""),
        "岗位名称": list_item.get("title", ""),
        "岗位类型\n企业/公务员/事业单位/军队文职": "企业",
        "公司名称": list_item.get("company", ""),
        "公司规模": company_size,
        "所在省份": province,
        "城市": city,
        "详细地址": detail_item.get("address", ""),
        "学历要求": detail_item.get("education", ""),
        "经验要求": detail_item.get("experience", ""),
        "薪资范围": list_item.get("salary", ""),
        "福利标签": " / ".join(detail_item.get("benefits", [])),
        "工作内容": detail_item.get("job_content", ""),
        "任职要求": detail_item.get("requirements", ""),
        "岗位链接": list_item.get("job_link", ""),
        "发布时间": list_item.get("job_time", ""),
        "投递起始时间": "",
        "投递截止时间": "",
        "证书要求": "",
        "备注（技能要求）": " / ".join(list_item.get("tags", [])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape 51job engineering data")
    parser.add_argument("--pages", type=int, default=1, help="Number of pages to scrape")
    parser.add_argument("--keyword", type=str, default=DEFAULT_CATEGORY)
    parser.add_argument("--job-area", type=str, default="", help="Job area code, e.g. 030200")
    args = parser.parse_args()

    columns = read_template_columns()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        list_page = context.new_page()

        for page_num in range(1, args.pages + 1):
            url = build_search_url(args.keyword, page_num, args.job_area or None)
            list_page.goto(url, wait_until="networkidle")
            list_items = extract_list_items(list_page)

            for list_item in list_items:
                detail_page = context.new_page()
                detail_page.goto(list_item["job_link"], wait_until="networkidle")
                detail = extract_detail(detail_page)
                detail_page.close()

                rows.append(build_row(len(rows) + 1, list_item, detail))

        browser.close()

    df = pd.DataFrame(rows, columns=columns)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"51job_engineering_{stamp}.xlsx"
    df.to_excel(output_path, index=False, sheet_name="Sheet1")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
