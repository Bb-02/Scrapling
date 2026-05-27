"""
51job 搜索 API 客户端。

通过 StealthySession 浏览器加载搜索页，使用 capture_xhr 拦截 Vue SPA
对 /api/job/search-pc 的请求，直接获取结构化 JSON 数据。

需要 real_chrome=True 绕过 Aliyun WAF。
"""

import json
import logging
import re
import time
from urllib.parse import urlencode

from scrapling.fetchers import StealthySession

logger = logging.getLogger(__name__)

BASE_SEARCH_URL = "https://we.51job.com/pc/search"

# 51job 全国省份代码（省级行政区）
PROVINCE_CODES: dict[str, str] = {
    "北京": "010000", "上海": "020000", "天津": "030000", "重庆": "040000",
    "河北": "050000", "山西": "060000", "内蒙古": "070000", "辽宁": "080000",
    "吉林": "090000", "黑龙江": "100000", "江苏": "110000", "浙江": "120000",
    "安徽": "130000", "福建": "140000", "江西": "150000", "山东": "160000",
    "河南": "170000", "湖北": "180000", "湖南": "190000", "广东": "200000",
    "广西": "210000", "海南": "220000", "四川": "230000", "贵州": "240000",
    "云南": "250000", "西藏": "260000", "陕西": "270000", "甘肃": "280000",
    "青海": "290000", "宁夏": "300000", "新疆": "310000",
    "香港": "320000", "澳门": "330000", "台湾": "340000",
}

# 主要城市代码
MAJOR_CITY_CODES: dict[str, str] = {
    "北京": "010000", "上海": "020000", "广州": "200300", "深圳": "200400",
    "杭州": "120200", "成都": "230200", "武汉": "180200", "南京": "110200",
    "西安": "270200", "重庆": "040000", "苏州": "110500", "天津": "030000",
    "长沙": "190200", "郑州": "170200", "东莞": "200800", "青岛": "160200",
    "沈阳": "080200", "宁波": "120400", "昆明": "250200", "大连": "080300",
    "合肥": "130200", "福州": "140200", "厦门": "140300", "济南": "160100",
    "哈尔滨": "100200", "无锡": "110400", "佛山": "200600", "长春": "090200",
    "石家庄": "050200", "贵阳": "240200", "南宁": "210200", "南昌": "150200",
    "太原": "060200", "兰州": "280200", "温州": "120500", "珠海": "200500",
    "中山": "201000", "惠州": "201200", "常州": "110700", "南通": "110800",
    "徐州": "111400", "嘉兴": "121000", "绍兴": "120800", "金华": "120900",
    "烟台": "160600", "潍坊": "161000", "洛阳": "170500", "芜湖": "130500",
    "海口": "220200", "三亚": "220300", "呼和浩特": "070200", "乌鲁木齐": "310200",
}


def parse_api_response(json_data: dict) -> list[dict]:
    """从搜索 API JSON 响应中提取职位列表。

    API 返回的 items 字段包含: jobName, fullCompanyName, provideSalaryString,
    degreeString, workYearString, companySizeString, jobAreaString,
    jobHref, jobTags, industryType1Str, issueDateString, jobDescribe 等。
    """
    items = []
    job_list = (
        json_data.get("resultbody", {})
        .get("job", {})
        .get("items", [])
    )
    for job in job_list:
        items.append({
            "jobId": job.get("jobId", ""),
            "jobName": job.get("jobName", ""),
            "companyName": job.get("companyName", ""),
            "fullCompanyName": job.get("fullCompanyName", ""),
            "companySize": job.get("companySizeString", ""),
            "salary": job.get("provideSalaryString", ""),
            "workArea": job.get("workArea", "") or job.get("jobAreaString", ""),
            "jobAreaString": job.get("jobAreaString", ""),
            "degree": job.get("degreeString", ""),
            "workYear": job.get("workYearString", ""),
            "jobHref": job.get("jobHref", ""),
            "companyHref": job.get("companyHref", ""),
            "jobTags": job.get("jobTagsList", []) or job.get("jobTags", []),
            "industryType1": job.get("industryType1Str", ""),
            "industryType2": job.get("industryType2Str", ""),
            "issueDate": job.get("issueDateString", ""),
            "updateDate": job.get("updateDateTime", ""),
            "jobDescribe": job.get("jobDescribe", ""),
            "companyType": job.get("companyTypeString", ""),
            "companyIndustry1": job.get("companyIndustryType1Str", ""),
            "companyIndustry2": job.get("companyIndustryType2Str", ""),
            "welfareList": job.get("jobWelfareCodeDataList", []),
            "hrName": job.get("hrName", ""),
            "lon": job.get("lon", ""),
            "lat": job.get("lat", ""),
            "isIntern": job.get("isIntern", False),
            "isPromotion": job.get("isPromotion", False),
        })
    return items


def get_total_count(json_data: dict) -> int:
    """从 API 响应中提取总职位数。"""
    return (
        json_data.get("resultbody", {})
        .get("job", {})
        .get("totalCount", 0)
    )


class SearchAPIClient:
    """通过浏览器 + XHR 拦截获取 51job 搜索数据。"""

    def __init__(self, headless: bool = True, timeout: int = 60000):
        self.headless = headless
        self.timeout = timeout

    def search_one_page(
        self,
        session: StealthySession,
        keyword: str,
        job_area: str,
        page_num: int = 1,
        page_size: int = 20,
    ) -> dict | None:
        """搜索单页，返回 API JSON 或 None。"""
        params = {
            "keyword": keyword,
            "searchType": "2",
            "pageNum": str(page_num),
            "jobArea": job_area,
        }
        url = f"{BASE_SEARCH_URL}?{urlencode(params)}"

        for attempt in range(3):
            try:
                resp = session.fetch(url)

                for xhr in resp.captured_xhr:
                    if "/api/job/search-pc" in xhr.url and xhr.status == 200:
                        body = (
                            xhr.body.decode("utf-8")
                            if isinstance(xhr.body, bytes)
                            else str(xhr.body)
                        )
                        if "aliyun_waf" not in body:
                            try:
                                return json.loads(body)
                            except json.JSONDecodeError:
                                continue

                time.sleep(2 * (attempt + 1))
            except Exception:
                time.sleep(2 * (attempt + 1))
        return None

    def search_all_pages(
        self,
        session: StealthySession,
        keyword: str,
        job_area: str,
        max_pages: int = 80,
        on_page: callable = None,
    ) -> list[dict]:
        """搜索全部页面，返回所有职位列表。

        on_page(page_num, total_pages, items) 可选回调。
        """
        all_items: list[dict] = []
        first_data = self.search_one_page(session, keyword, job_area, page_num=1)
        if first_data is None:
            return all_items

        total_count = get_total_count(first_data)
        total_pages = min(
            max_pages,
            (total_count + 19) // 20 if total_count else max_pages,
        )
        logger.info(
            "[%s][%s] 总数=%d, 页数=%d",
            keyword, job_area, total_count, total_pages,
        )

        first_items = parse_api_response(first_data)
        all_items.extend(first_items)
        if on_page:
            on_page(1, total_pages, first_items)

        for page_num in range(2, total_pages + 1):
            time.sleep(0.5)  # 页面间短暂间隔
            data = self.search_one_page(session, keyword, job_area, page_num=page_num)
            if data is None:
                logger.warning("[%s][%s] 第%d页失败", keyword, job_area, page_num)
                continue

            items = parse_api_response(data)
            all_items.extend(items)
            if on_page:
                on_page(page_num, total_pages, items)

            if page_num % 10 == 0:
                logger.info(
                    "[%s][%s] 进度: %d/%d 页, 累计 %d 条",
                    keyword, job_area, page_num, total_pages, len(all_items),
                )

        return all_items

    def crawl_with_splitting(
        self,
        keywords: list[str],
        job_areas: list[tuple[str, str]],  # [(name, code), ...]
        max_pages_per_search: int = 80,
        on_keyword_start: callable = None,
        on_page: callable = None,
    ) -> list[dict]:
        """多关键词 × 多地域爬取。

        keywords: 搜索关键词列表（子标签）
        job_areas: [(地域名, 地域代码), ...]
        """
        all_items: list[dict] = []
        seen_job_ids: set[str] = set()

        with StealthySession(
            headless=self.headless,
            solve_cloudflare=True,
            real_chrome=True,
            network_idle=True,
            wait=3000,
            timeout=self.timeout,
            google_search=False,
            hide_canvas=True,
            block_webrtc=True,
        ) as session:
            for ki, keyword in enumerate(keywords):
                if on_keyword_start:
                    on_keyword_start(keyword, ki, len(keywords))

                for area_name, area_code in job_areas:
                    logger.info("搜索: keyword=%s, area=%s(%s)", keyword, area_name, area_code)
                    items = self.search_all_pages(
                        session, keyword, area_code,
                        max_pages=max_pages_per_search,
                        on_page=on_page,
                    )

                    new_count = 0
                    for item in items:
                        jid = item.get("jobId", "")
                        if jid and jid not in seen_job_ids:
                            seen_job_ids.add(jid)
                            item["_search_keyword"] = keyword
                            item["_search_area"] = area_name
                            all_items.append(item)
                            new_count += 1

                    logger.info(
                        "[%s][%s] 新增 %d 条 (去重后), 累计 %d 条",
                        keyword, area_name, new_count, len(all_items),
                    )

        return all_items
