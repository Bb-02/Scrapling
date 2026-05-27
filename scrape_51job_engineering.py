import argparse
import html
import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import requests
from scrapling.fetchers import StealthySession
from scrapling.parser import Selector

# ============================================================
# 配置常量
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = SCRIPT_DIR.parent / "数据模板.xlsx"
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
PROGRESS_FILE = SCRIPT_DIR.parent / "crawl_progress.json"
BASE_SEARCH_URL = "https://we.51job.com/pc/search"

DEFAULT_PLATFORM = "前程无忧"
DEFAULT_CATEGORY = "工程/机械"
DEFAULT_JOB_AREA = "000000"
DEFAULT_MAX_PAGES = 80
MAX_RETRIES = 3
RETRY_BASE_WAIT = 2
LIST_PAGE_WAIT = (2, 4)          # 列表页之间（浏览器）
DETAIL_DELAY = (0.3, 0.5)        # 详情页之间（HTTP，可大幅缩短）
EMPTY_PAGE_RETRY_WAIT = 3
DETAIL_CONCURRENCY = 8           # 并发抓取详情页的线程数

# ============================================================
# 日志
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ============================================================
# 城市 -> 省份 映射
# ============================================================

CITY_PROVINCE: dict[str, str] = {
    "北京": "北京市", "天津": "天津市", "上海": "上海市", "重庆": "重庆市",
    "石家庄": "河北省", "唐山": "河北省", "秦皇岛": "河北省", "邯郸": "河北省",
    "邢台": "河北省", "保定": "河北省", "张家口": "河北省", "承德": "河北省",
    "沧州": "河北省", "廊坊": "河北省", "衡水": "河北省",
    "太原": "山西省", "大同": "山西省", "阳泉": "山西省", "长治": "山西省",
    "晋城": "山西省", "朔州": "山西省", "晋中": "山西省", "运城": "山西省",
    "忻州": "山西省", "临汾": "山西省", "吕梁": "山西省",
    "呼和浩特": "内蒙古自治区", "包头": "内蒙古自治区", "乌海": "内蒙古自治区",
    "赤峰": "内蒙古自治区", "通辽": "内蒙古自治区", "鄂尔多斯": "内蒙古自治区",
    "呼伦贝尔": "内蒙古自治区", "巴彦淖尔": "内蒙古自治区", "乌兰察布": "内蒙古自治区",
    "沈阳": "辽宁省", "大连": "辽宁省", "鞍山": "辽宁省", "抚顺": "辽宁省",
    "本溪": "辽宁省", "丹东": "辽宁省", "锦州": "辽宁省", "营口": "辽宁省",
    "阜新": "辽宁省", "辽阳": "辽宁省", "盘锦": "辽宁省", "铁岭": "辽宁省",
    "朝阳": "辽宁省", "葫芦岛": "辽宁省",
    "长春": "吉林省", "吉林": "吉林省", "四平": "吉林省", "辽源": "吉林省",
    "通化": "吉林省", "白山": "吉林省", "松原": "吉林省", "白城": "吉林省",
    "哈尔滨": "黑龙江省", "齐齐哈尔": "黑龙江省", "鸡西": "黑龙江省",
    "鹤岗": "黑龙江省", "双鸭山": "黑龙江省", "大庆": "黑龙江省",
    "伊春": "黑龙江省", "佳木斯": "黑龙江省", "七台河": "黑龙江省",
    "牡丹江": "黑龙江省", "黑河": "黑龙江省", "绥化": "黑龙江省",
    "南京": "江苏省", "无锡": "江苏省", "徐州": "江苏省", "常州": "江苏省",
    "苏州": "江苏省", "南通": "江苏省", "连云港": "江苏省", "淮安": "江苏省",
    "盐城": "江苏省", "扬州": "江苏省", "镇江": "江苏省", "泰州": "江苏省",
    "宿迁": "江苏省",
    "杭州": "浙江省", "宁波": "浙江省", "温州": "浙江省", "嘉兴": "浙江省",
    "湖州": "浙江省", "绍兴": "浙江省", "金华": "浙江省", "衢州": "浙江省",
    "舟山": "浙江省", "台州": "浙江省", "丽水": "浙江省",
    "合肥": "安徽省", "芜湖": "安徽省", "蚌埠": "安徽省", "淮南": "安徽省",
    "马鞍山": "安徽省", "淮北": "安徽省", "铜陵": "安徽省", "安庆": "安徽省",
    "黄山": "安徽省", "滁州": "安徽省", "阜阳": "安徽省", "宿州": "安徽省",
    "六安": "安徽省", "亳州": "安徽省", "池州": "安徽省", "宣城": "安徽省",
    "福州": "福建省", "厦门": "福建省", "莆田": "福建省", "三明": "福建省",
    "泉州": "福建省", "漳州": "福建省", "南平": "福建省", "龙岩": "福建省",
    "宁德": "福建省",
    "南昌": "江西省", "景德镇": "江西省", "萍乡": "江西省", "九江": "江西省",
    "新余": "江西省", "鹰潭": "江西省", "赣州": "江西省", "吉安": "江西省",
    "宜春": "江西省", "抚州": "江西省", "上饶": "江西省",
    "济南": "山东省", "青岛": "山东省", "淄博": "山东省", "枣庄": "山东省",
    "东营": "山东省", "烟台": "山东省", "潍坊": "山东省", "济宁": "山东省",
    "泰安": "山东省", "威海": "山东省", "日照": "山东省", "临沂": "山东省",
    "德州": "山东省", "聊城": "山东省", "滨州": "山东省", "菏泽": "山东省",
    "郑州": "河南省", "开封": "河南省", "洛阳": "河南省", "平顶山": "河南省",
    "安阳": "河南省", "鹤壁": "河南省", "新乡": "河南省", "焦作": "河南省",
    "濮阳": "河南省", "许昌": "河南省", "漯河": "河南省", "三门峡": "河南省",
    "南阳": "河南省", "商丘": "河南省", "信阳": "河南省", "周口": "河南省",
    "驻马店": "河南省", "济源": "河南省",
    "武汉": "湖北省", "黄石": "湖北省", "十堰": "湖北省", "宜昌": "湖北省",
    "襄阳": "湖北省", "鄂州": "湖北省", "荆门": "湖北省", "孝感": "湖北省",
    "荆州": "湖北省", "黄冈": "湖北省", "咸宁": "湖北省", "随州": "湖北省",
    "长沙": "湖南省", "株洲": "湖南省", "湘潭": "湖南省", "衡阳": "湖南省",
    "邵阳": "湖南省", "岳阳": "湖南省", "常德": "湖南省", "张家界": "湖南省",
    "益阳": "湖南省", "郴州": "湖南省", "永州": "湖南省", "怀化": "湖南省",
    "娄底": "湖南省",
    "广州": "广东省", "深圳": "广东省", "珠海": "广东省", "汕头": "广东省",
    "佛山": "广东省", "韶关": "广东省", "河源": "广东省", "梅州": "广东省",
    "惠州": "广东省", "汕尾": "广东省", "东莞": "广东省", "中山": "广东省",
    "江门": "广东省", "阳江": "广东省", "湛江": "广东省", "茂名": "广东省",
    "肇庆": "广东省", "清远": "广东省", "潮州": "广东省", "揭阳": "广东省",
    "云浮": "广东省",
    "南宁": "广西壮族自治区", "柳州": "广西壮族自治区", "桂林": "广西壮族自治区",
    "梧州": "广西壮族自治区", "北海": "广西壮族自治区", "防城港": "广西壮族自治区",
    "钦州": "广西壮族自治区", "贵港": "广西壮族自治区", "玉林": "广西壮族自治区",
    "百色": "广西壮族自治区", "贺州": "广西壮族自治区", "河池": "广西壮族自治区",
    "来宾": "广西壮族自治区", "崇左": "广西壮族自治区",
    "海口": "海南省", "三亚": "海南省", "三沙": "海南省", "儋州": "海南省",
    "成都": "四川省", "自贡": "四川省", "攀枝花": "四川省", "泸州": "四川省",
    "德阳": "四川省", "绵阳": "四川省", "广元": "四川省", "遂宁": "四川省",
    "内江": "四川省", "乐山": "四川省", "南充": "四川省", "眉山": "四川省",
    "宜宾": "四川省", "广安": "四川省", "达州": "四川省", "雅安": "四川省",
    "巴中": "四川省", "资阳": "四川省",
    "贵阳": "贵州省", "六盘水": "贵州省", "遵义": "贵州省", "安顺": "贵州省",
    "毕节": "贵州省", "铜仁": "贵州省",
    "昆明": "云南省", "曲靖": "云南省", "玉溪": "云南省", "保山": "云南省",
    "昭通": "云南省", "丽江": "云南省", "普洱": "云南省", "临沧": "云南省",
    "拉萨": "西藏自治区", "日喀则": "西藏自治区", "昌都": "西藏自治区",
    "林芝": "西藏自治区", "山南": "西藏自治区", "那曲": "西藏自治区",
    "西安": "陕西省", "铜川": "陕西省", "宝鸡": "陕西省", "咸阳": "陕西省",
    "渭南": "陕西省", "延安": "陕西省", "汉中": "陕西省", "榆林": "陕西省",
    "安康": "陕西省", "商洛": "陕西省",
    "兰州": "甘肃省", "嘉峪关": "甘肃省", "金昌": "甘肃省", "白银": "甘肃省",
    "天水": "甘肃省", "武威": "甘肃省", "张掖": "甘肃省", "平凉": "甘肃省",
    "酒泉": "甘肃省", "庆阳": "甘肃省", "定西": "甘肃省", "陇南": "甘肃省",
    "西宁": "青海省", "海东": "青海省",
    "银川": "宁夏回族自治区", "石嘴山": "宁夏回族自治区", "吴忠": "宁夏回族自治区",
    "固原": "宁夏回族自治区", "中卫": "宁夏回族自治区",
    "乌鲁木齐": "新疆维吾尔自治区", "克拉玛依": "新疆维吾尔自治区",
    "吐鲁番": "新疆维吾尔自治区", "哈密": "新疆维吾尔自治区",
}


def infer_province(city: str, address: str = "") -> str:
    if not city:
        return ""
    if city in CITY_PROVINCE:
        return CITY_PROVINCE[city]
    if address:
        m = re.match(
            r"^(北京市|上海市|天津市|重庆市|[^省]+省|[^区]+自治区|香港特别行政区|澳门特别行政区)",
            address,
        )
        if m:
            return m.group(1)
    return ""


# ============================================================
# 断点续爬
# ============================================================


def load_progress() -> dict:
    if not PROGRESS_FILE.exists():
        return {"keyword_index": 0, "page": 1}

    try:
        progress = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("断点文件读取失败，将从头开始爬取：%s", PROGRESS_FILE)
        return {"keyword_index": 0, "page": 1}

    keyword_index = progress.get("keyword_index", 0)
    page = progress.get("page", 1)
    if not isinstance(keyword_index, int) or keyword_index < 0:
        keyword_index = 0
    if not isinstance(page, int) or page < 1:
        page = 1
    return {"keyword_index": keyword_index, "page": page}


def save_progress(keyword_index: int, page: int) -> None:
    PROGRESS_FILE.write_text(
        json.dumps({"keyword_index": keyword_index, "page": page}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ============================================================
# 工具函数
# ============================================================


def build_search_url(keyword: str, page_num: int, job_area: str | None) -> str:
    params = {"keyword": keyword, "searchType": "2", "pageNum": str(page_num)}
    if job_area:
        params["jobArea"] = job_area
    return f"{BASE_SEARCH_URL}?{urlencode(params)}"


def parse_company_size(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"(\d+[-~]\d+人|\d+人以上|少于\d+人|\d+人)", text)
    return m.group(1) if m else ""


def split_location(area_text: str) -> tuple[str, str]:
    if not area_text:
        return "", ""
    city = re.split(r"[-·]", area_text)[0].strip()
    province = infer_province(city)
    return province, city


def clean_description(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(str(text))
    text = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", text)
    text = re.sub(r"(?i)</?\s*(div|p|li|section|tr|h[1-6])\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("\r", "\n").replace("　", " ").replace("\xa0", " ")

    lines: list[str] = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line).strip()
        if not line:
            continue
        if line not in lines[-2:]:
            lines.append(line)
    return "\n".join(lines)


def split_job_desc(desc_text: str) -> tuple[str, str]:
    text = clean_description(desc_text)
    if not text:
        return "", ""

    requirement_pattern = re.compile(
        r"(任职要求|任职资格|职位要求|岗位要求|应聘要求|资格要求|任职条件)[:：】\]\s]*"
    )
    m = requirement_pattern.search(text)
    if not m:
        return text, ""
    work_content = text[: m.start()].strip()
    requirements = (m.group() + text[m.end():]).strip()
    return work_content, requirements


def read_template_columns() -> list[str]:
    if TEMPLATE_PATH.exists():
        df = pd.read_excel(TEMPLATE_PATH, nrows=0)
        return [str(col).strip() for col in df.columns]
    return [
        "序号", "招聘平台", "岗位类型\n一级", "岗位类型\n二级", "岗位名称",
        "岗位类型\n企业/公务员/事业单位/军队文职", "公司名称", "公司规模",
        "所在省份", "城市", "详细地址", "学历要求", "经验要求", "薪资范围",
        "福利标签", "工作内容", "任职要求", "岗位链接", "发布时间",
        "投递起始时间", "投递截止时间", "证书要求", "备注（技能要求）",
    ]


# ============================================================
# 页面解析（列表页用 Scrapling Page，详情页用 Selector）
# ============================================================


def extract_list_items(page) -> list[dict]:
    """从 Scrapling 的浏览器页面提取列表项。"""
    items: list[dict] = []
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

        items.append({
            "title": title,
            "salary": salary,
            "area": area,
            "company": company,
            "company_info": comp_info,
            "job_link": job_link,
            "job_time": job_time,
            "tags": tags,
        })
    return items


def extract_detail_from_html(html_text: str) -> dict:
    """从 HTML 字符串解析详情页（不需要浏览器）。"""
    sel = Selector(html_text)

    location = (sel.css(".msg.ltype .type_2::text").get() or "").strip()
    experience = (sel.css(".msg.ltype .type_3::text").get() or "").strip()
    education = (sel.css(".msg.ltype .type_4::text").get() or "").strip()

    benefits = [t.strip() for t in sel.css(".job-detail .tags .tag::text").getall()]
    desc_nodes = sel.css(".job_msg")
    desc_text = desc_nodes[0].get_all_text() if desc_nodes else ""
    job_content, requirements = split_job_desc(desc_text)

    category = (sel.xpath("//p[contains(., '职能类别')]//a/text()").get() or "").strip()
    address = (sel.css(".job-address::text").get() or "").strip()

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


# ============================================================
# 详情页并发抓取（核心优化点）
# ============================================================


def _fetch_one_detail(
    http_session: requests.Session,
    list_item: dict,
    detail_headers: dict,
) -> dict | None:
    """抓取并解析单个详情页（在线程池中运行）。"""
    url = list_item.get("job_link", "")
    if not url:
        return None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = http_session.get(url, headers=detail_headers, timeout=15)
            if resp.status_code == 200:
                detail = extract_detail_from_html(resp.text)
                return detail
            wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))
            time.sleep(wait)
        except Exception:
            if attempt < MAX_RETRIES:
                wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))
                time.sleep(wait)
            else:
                logger.error("详情页请求失败(%d次): %s", MAX_RETRIES, url)
    return None


def fetch_details_concurrently(
    http_session: requests.Session,
    list_items: list[dict],
    detail_headers: dict,
    concurrency: int = DETAIL_CONCURRENCY,
) -> list[dict]:
    """并发抓取所有详情页，保持原始顺序返回。"""
    results: list[dict | None] = [None] * len(list_items)

    # 先过滤出有链接的项
    valid_indices = [
        i for i, item in enumerate(list_items) if item.get("job_link")
    ]
    if not valid_indices:
        return []

    logger.info("  并发抓取%d个详情页（并发数=%d）...", len(valid_indices), concurrency)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_index = {
            pool.submit(
                _fetch_one_detail, http_session, list_items[i], detail_headers
            ): i
            for i in valid_indices
        }
        done = 0
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                logger.error("详情页解析异常 [%s]: %s", list_items[idx].get("title", ""), exc)
            done += 1
            if done % 20 == 0 or done == len(valid_indices):
                logger.info("  详情页进度: %d/%d", done, len(valid_indices))

    return results


# ============================================================
# 网络请求（列表页用浏览器）
# ============================================================


def fetch_list_page(
    session: StealthySession,
    url: str,
    label: str = "",
) -> "Page | object":
    """获取列表页，指数退避重试。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            page = session.fetch(url)
            if page is not None and page.status == 200:
                return page
            wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))
            logger.warning(
                "%s第%d次请求失败(status=%s)，%d秒后重试",
                label, attempt, getattr(page, "status", "None"), wait,
            )
            time.sleep(wait)
        except Exception as exc:
            if attempt >= MAX_RETRIES:
                raise
            wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))
            logger.warning("%s第%d次请求异常，%d秒后重试：%s", label, attempt, wait, exc)
            time.sleep(wait)
    return session.fetch(url)


def build_http_session_from_browser(session: StealthySession) -> requests.Session:
    """从 StealthySession 的浏览器中提取 cookies 并构建 requests.Session。"""
    http_session = requests.Session()
    try:
        # 通过 Playwright 获取所有 cookies
        cookies = session.page.context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        http_session.cookies.update(cookie_dict)
        logger.info("  已从浏览器同步 %d 个 cookies", len(cookies))
    except Exception:
        logger.warning("  无法获取浏览器 cookies，将不带 cookies 请求详情页")
    return http_session


# ============================================================
# 主流程
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape 51job engineering data")
    parser.add_argument("--pages", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--keyword", type=str, default=DEFAULT_CATEGORY)
    parser.add_argument("--job-area", type=str, default=DEFAULT_JOB_AREA)
    parser.add_argument("--concurrency", type=int, default=DETAIL_CONCURRENCY,
                        help="详情页并发线程数")
    args = parser.parse_args()

    progress = load_progress()
    start_page = progress["page"]
    if start_page > 1:
        logger.info("从断点继续：第%d页", start_page)

    columns = read_template_columns()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    detail_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        "Referer": "https://we.51job.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    with StealthySession(headless=True, solve_cloudflare=True) as browser_session:
        # 先访问一次搜索页，让浏览器拿到 cookies
        init_url = build_search_url(args.keyword, 1, args.job_area or None)
        fetch_list_page(browser_session, init_url, label="[初始化] ")
        http_session = build_http_session_from_browser(browser_session)

        page_num = start_page
        max_pages = args.pages if args.pages > 0 else max(args.max_pages, 1)

        while page_num <= max_pages:
            label = f"[{args.keyword}][第{page_num}页]"

            # --- 列表页（用浏览器） ---
            if page_num > start_page or all_rows:
                wait = random.uniform(*LIST_PAGE_WAIT)
                logger.info("%s 等待%.1f秒...", label, wait)
                time.sleep(wait)

            url = build_search_url(args.keyword, page_num, args.job_area or None)
            logger.info("%s 开始获取列表页", label)
            list_page = fetch_list_page(browser_session, url, label=f"{label} ")
            list_items = extract_list_items(list_page)

            if not list_items:
                logger.warning("%s 列表为空，%d秒后重试", label, EMPTY_PAGE_RETRY_WAIT)
                time.sleep(EMPTY_PAGE_RETRY_WAIT)
                list_page = fetch_list_page(browser_session, url, label=f"{label} ")
                list_items = extract_list_items(list_page)
                if not list_items:
                    logger.info("%s 确认无数据，停止翻页", label)
                    break

            logger.info("%s 获取%d条列表项", label, len(list_items))

            # --- 详情页（用 HTTP 并发抓取） ---
            detail_results = fetch_details_concurrently(
                http_session,
                list_items,
                detail_headers,
                concurrency=args.concurrency,
            )

            # --- 组装数据 ---
            for i, list_item in enumerate(list_items):
                row_idx = len(all_rows) + 1
                detail = detail_results[i] if i < len(detail_results) and detail_results[i] else {}
                all_rows.append(build_row(row_idx, list_item, detail))

            save_progress(0, page_num + 1)
            page_num += 1

            logger.info("%s 完成，累计%d条", label, len(all_rows))

    if not all_rows:
        logger.warning("未获取到任何数据")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"51job_engineering_{stamp}.xlsx"
    df = pd.DataFrame(all_rows, columns=columns)
    df.to_excel(output_path, index=False, sheet_name="Sheet1")
    logger.info("保存完成：%s（共%d条）", output_path, len(all_rows))


if __name__ == "__main__":
    raise SystemExit(main())
