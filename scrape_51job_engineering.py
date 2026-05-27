import argparse
import html
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
from scrapling.fetchers import StealthySession

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = SCRIPT_DIR.parent / "数据模板.xlsx"
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
BASE_SEARCH_URL = "https://we.51job.com/pc/search"
DEFAULT_PLATFORM = "前程无忧"
DEFAULT_CATEGORY = "工程/机械"
DEFAULT_MAX_PAGES = 80
DEFAULT_JOB_AREA = "000000"


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
    items = []
    for item in page.css(".joblist-item"):
        title = item.css(".jname::text").get() or ""
        salary = item.css(".sal::text").get() or ""
        area_parts = item.css(".joblist-item-jobinfo .area *::text").getall()
        area = "".join(area_parts).strip()
        company = item.css(".cname::text").get() or ""
        comp_info = "".join(item.css(".comp::text").getall()).strip()
        job_link = item.css(".jname::attr(href)").get() or ""
        if not job_link:
            links = item.css("a::attr(href)").getall()
            for link in links:
                if re.search(r"https?://jobs\.51job\.com/[^/]+/\d+\.html", link):
                    job_link = link
                    break
            if not job_link:
                for link in links:
                    if "jobs.51job.com" in link:
                        job_link = link
                        break
            if not job_link and links:
                job_link = links[0]

        tags = [t.strip() for t in item.css(".joblist-item-tags .tag::text").getall()]

        job_time = ""
        sensors_raw = item.css(".joblist-item-job::attr(sensorsdata)").get() or ""
        if sensors_raw:
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
    location = page.css(".msg.ltype .type_2::text").get() or ""
    experience = page.css(".msg.ltype .type_3::text").get() or ""
    education = page.css(".msg.ltype .type_4::text").get() or ""

    benefits = [t.strip() for t in page.css(".job-detail .tags .tag::text").getall()]
    desc_nodes = page.css(".job_msg")
    desc_text = desc_nodes[0].get_all_text() if desc_nodes else ""
    job_content, requirements = split_job_desc(desc_text)

    category = page.xpath("//p[contains(., '职能类别')]//a/text()").get() or ""
    address = page.css(".job-address::text").get() or ""

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


def fetch_with_retry(session: StealthySession, url: str, retries: int = 3):
    for attempt in range(1, retries + 1):
        page = session.fetch(url)
        if page and page.status == 200:
            return page
        time.sleep(2 + attempt)
    return session.fetch(url)


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
    parser.add_argument(
        "--pages",
        type=int,
        default=0,
        help="Number of pages to scrape; 0 means auto until empty",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help="Safety cap when --pages=0",
    )
    parser.add_argument("--keyword", type=str, default=DEFAULT_CATEGORY)
    parser.add_argument(
        "--job-area",
        type=str,
        default=DEFAULT_JOB_AREA,
        help="Job area code, e.g. 000000 for nationwide",
    )
    args = parser.parse_args()

    columns = read_template_columns()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    with StealthySession(headless=True, solve_cloudflare=True) as session:
        page_num = 1
        max_pages = args.pages if args.pages > 0 else max(args.max_pages, 1)
        while page_num <= max_pages:
            url = build_search_url(args.keyword, page_num, args.job_area or None)
            list_page = fetch_with_retry(session, url)
            list_items = extract_list_items(list_page)

            if not list_items:
                time.sleep(3)
                list_page = fetch_with_retry(session, url)
                list_items = extract_list_items(list_page)
                if not list_items:
                    break

            for list_item in list_items:
                if not list_item.get("job_link"):
                    continue
                time.sleep(random.uniform(1.2, 2.5))
                detail_page = fetch_with_retry(session, list_item["job_link"])
                detail = extract_detail(detail_page)
                rows.append(build_row(len(rows) + 1, list_item, detail))

            page_num += 1

    df = pd.DataFrame(rows, columns=columns)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"51job_engineering_{stamp}.xlsx"
    df.to_excel(output_path, index=False, sheet_name="Sheet1")
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
