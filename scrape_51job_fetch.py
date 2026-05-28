"""
51job crawler using browser-internal fetch() API calls.
Single browser process, persistent login, far fewer WAF issues.

Usage:
    python scrape_51job_fetch.py                     # full crawl
    python scrape_51job_fetch.py --category 工程/机械  # specific category
    python scrape_51job_fetch.py --no-scout           # skip scout pruning
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import re
import sys
import time
import random
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
from playwright.async_api import async_playwright

# ============================================================
# Constants
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = SCRIPT_DIR.parent / "数据模板.xlsx"
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
PROFILE_DIR = SCRIPT_DIR / "chrome_profile"
PROGRESS_FILE = OUTPUT_DIR / "fetch_progress.json"

HMAC_KEY = b"abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
SEARCH_API_PATH = "/api/job/search-pc"
MAX_PAGES = 80
PAGE_SIZE = 20
DELAY_MIN = 0.5
DELAY_MAX = 1.5
SAFE_FETCH_LIMIT = 40  # requests before clearing WAF tracking cookies

TRACKING_COOKIE_PATTERNS = [
    "acw_tc", "ssxmod_itna", "sensorsdata", "sajssdk",
    "Hm_lvt", "Hm_lpvt", "HMACCOUNT", "guid", "sensor",
    "ps", "_c_WBKFRo",
]

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

SCOUT_AREAS: list[tuple[str, str]] = [
    ("广东", "200000"), ("江苏", "110000"), ("浙江", "120000"),
    ("北京", "010000"), ("上海", "020000"),
]

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
    "东营": "山东省", "烟台": "山东省", "威海": "山东省", "日照": "山东省",
    "临沂": "山东省", "德州": "山东省", "聊城": "山东省", "滨州": "山东省",
    "菏泽": "山东省", "潍坊": "山东省", "济宁": "山东省", "泰安": "山东省",
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

# ============================================================
# Category / keyword definitions
# ============================================================

CATEGORIES: dict[str, list[str]] = {
    "工程/机械": [
        "机械工程师", "机电工程师", "结构工程师", "模具工程师", "设备工程师",
        "机械设计", "机械绘图员", "机械维修", "机械工艺", "机械制图",
        "机械自动化", "电气工程师", "自动化工程师", "工业工程师", "材料工程师",
        "焊接工程师", "铸造工程师", "锻造工程师", "冲压工程师", "注塑工程师",
        "CNC工程师", "数控编程", "质量管理", "质量工程师", "机械质检",
        "工程监理", "工程项目管理", "土木工程", "建筑工程", "暖通工程师",
        "给排水工程师", "水利工程", "岩土工程", "测绘工程师", "安全工程师",
        "焊接", "钣金", "车工", "磨工", "铣工", "钳工", "电焊工",
        "装配工", "维修工", "电工", "技工", "操作工", "生产技术",
        "工艺工程师", "PE工程师", "IE工程师", "NPI工程师", "ME工程师",
        "测试工程师", "可靠性工程师", "实验室技术员", "工程经理", "项目工程师",
        "研发工程师", "产品工程师", "制冷工程师", "热能工程师", "液压工程师",
        "气动工程师", "船舶工程师", "汽车工程师", "医疗器械工程师",
        "仪器仪表工程师", "机器人工程师", "无人机工程师",
    ],
}


def all_keywords() -> list[tuple[str, str]]:
    """Return all (category, keyword) pairs."""
    result = []
    for cat, kws in CATEGORIES.items():
        for kw in kws:
            result.append((cat, kw))
    return result


# ============================================================
# HMAC signature
# ============================================================

def build_signed_url(keyword: str, job_area: str, page_num: int = 1) -> str:
    ts = str(int(time.time() * 1000))
    params = {
        "api_key": "51job", "timestamp": ts, "keyword": keyword,
        "searchType": "2", "function": "", "industry": "",
        "jobArea": job_area, "jobArea2": "", "landmark": "", "metro": "",
        "salary": "", "workYear": "", "degree": "", "companyType": "",
        "companySize": "", "jobType": "", "issueDate": "",
        "sortType": "0", "pageNum": str(page_num), "pageSize": str(PAGE_SIZE),
    }
    qs = urlencode(params)
    sig = hmac.new(HMAC_KEY, f"{SEARCH_API_PATH}?{qs}".encode(), hashlib.sha256).hexdigest()
    return f"https://we.51job.com{SEARCH_API_PATH}?{qs}&signature={sig}"


# ============================================================
# fetch() JS
# ============================================================

FETCH_PAGE_JS = """
async (url) => {
    try {
        const resp = await fetch(url, {
            credentials: 'include',
            headers: {
                'user-token': localStorage.getItem('token') || '',
                'accept': 'application/json, text/plain, */*',
            }
        });
        const text = await resp.text();
        if (text.includes('aliyun_waf')) {
            return { waf: true };
        }
        if (!resp.headers.get('content-type')?.includes('json')) {
            return { error: 'not_json', status: resp.status, preview: text.substring(0, 200) };
        }
        const data = JSON.parse(text);
        const job = data?.resultbody?.job || {};
        return {
            items: job.items || [],
            totalCount: job.totalCount || 0,
            pageNum: job.pageNum || 0,
        };
    } catch(e) {
        return { error: e.message };
    }
}
"""

# ============================================================
# Data parsing
# ============================================================

def parse_location(job_area_string: str) -> tuple[str, str]:
    if not job_area_string:
        return "", ""
    parts = re.split(r"[·\-]", str(job_area_string))
    city = parts[0].strip() if parts else ""
    province = CITY_PROVINCE.get(city, "")
    if city in ("北京", "上海", "天津", "重庆"):
        province = city + "市"
    return province, city


def parse_job_description(job_describe: str) -> tuple[str, str]:
    if not job_describe:
        return "", ""
    text = str(job_describe)
    m = re.compile(
        r"(任职要求|任职资格|职位要求|岗位要求|应聘要求|资格要求|任职条件)[:：】\]\s]*"
    ).search(text)
    if not m:
        return text, ""
    return text[:m.start()].strip(), (m.group() + text[m.end():]).strip()


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


def build_row(idx: int, item: dict, category: str) -> dict:
    province, city = parse_location(item.get("jobAreaString", ""))

    # welfare
    welfare = item.get("welfareList", []) or item.get("jobWelfareCodeDataList", [])
    if isinstance(welfare, list) and welfare:
        if isinstance(welfare[0], dict):
            welfare_str = " / ".join(
                w.get("chineseTitle", "") or w.get("typeTitle", "") for w in welfare
            )
        else:
            welfare_str = " / ".join(str(w) for w in welfare)
    else:
        welfare_str = ""

    # tags
    tags = item.get("jobTags", []) or item.get("jobTagsList", [])
    if isinstance(tags, list):
        tags_str = " / ".join(
            t.get("jobTagName", "") if isinstance(t, dict) else str(t) for t in tags
        )
    else:
        tags_str = str(tags) if tags else ""

    # industry
    func_types = []
    for k in ("industryType1", "industryType2"):
        v = item.get(k, "") or item.get(k + "Str", "")
        if v and v not in func_types:
            func_types.append(v)

    # description
    work_content, requirements = parse_job_description(
        item.get("jobDescribe", "") or item.get("jobContent", "")
    )

    job_href = item.get("jobHref", "")
    if not job_href:
        jid = item.get("jobId", "")
        city_code = item.get("jobArea", "")
        if jid:
            job_href = f"https://jobs.51job.com/{city_code}/{jid}.html"

    return {
        "序号": idx,
        "招聘平台": "前程无忧",
        "岗位类型\n一级": category,
        "岗位类型\n二级": " / ".join(func_types) if func_types else "/",
        "岗位名称": item.get("jobName", ""),
        "岗位类型\n企业/公务员/事业单位/军队文职": "企业",
        "公司名称": item.get("fullCompanyName", "") or item.get("companyName", ""),
        "公司规模": item.get("companySize", "") or item.get("companySizeString", "/"),
        "所在省份": province,
        "城市": city,
        "详细地址": item.get("jobAreaString", ""),
        "学历要求": item.get("degree", "") or item.get("degreeString", "/"),
        "经验要求": item.get("workYear", "") or item.get("workYearString", "/"),
        "薪资范围": item.get("salary", "") or item.get("provideSalaryString", "/"),
        "福利标签": welfare_str or "/",
        "工作内容": work_content or "/",
        "任职要求": requirements or "/",
        "岗位链接": job_href,
        "发布时间": item.get("issueDate", "") or item.get("issueDateString", ""),
        "投递起始时间": "/",
        "投递截止时间": "/",
        "证书要求": "/",
        "备注（技能要求）": tags_str or "/",
    }


# ============================================================
# Progress management
# ============================================================

def load_progress() -> dict:
    if not PROGRESS_FILE.exists():
        return {
            "cat_idx": 0, "kw_idx": 0, "area_idx": 0,
            "phase": "scout", "total": 0, "seen_ids": [],
        }
    with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_progress(progress: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ============================================================
# Main crawler
# ============================================================

async def crawl(
    keywords: list[tuple[str, str]],  # [(category, keyword), ...]
    areas: list[tuple[str, str]],     # [(name, code), ...]
    scout_areas: list[tuple[str, str]],
    no_scout: bool = False,
):
    logger = logging.getLogger("crawl")
    PROFILE_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    progress = load_progress()
    cat_idx = progress.get("cat_idx", 0)
    kw_idx = progress.get("kw_idx", 0)
    area_idx = progress.get("area_idx", 0)
    phase = progress.get("phase", "scout")
    all_rows: list[dict] = progress.get("all_rows", [])
    seen_ids: set[str] = set(progress.get("seen_ids", []))
    request_count = [0]  # mutable counter for _crawl_areas

    # Resume from saved output
    resume_path = OUTPUT_DIR / "fetch_output.xlsx"
    if resume_path.exists() and not all_rows:
        try:
            df = pd.read_excel(resume_path)
            for _, row in df.iterrows():
                d = row.to_dict()
                jid = str(d.get("岗位链接", ""))
                if jid and jid not in seen_ids:
                    seen_ids.add(jid)
                    all_rows.append(d)
            logger.info("Resumed %d rows from %s", len(all_rows), resume_path.name)
        except Exception:
            pass

    scout_codes = {c for _, c in scout_areas}
    full_areas = [(n, c) for n, c in areas if c not in scout_codes] if not no_scout else areas

    async with async_playwright() as p:
        logger.info("Launching browser (persistent profile: %s)", PROFILE_DIR)
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            channel="chrome",
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()

        # Open search page to establish WAF session
        logger.info("Opening search page...")
        await page.goto(
            "https://we.51job.com/pc/search?keyword=%E6%9C%BA%E6%A2%B0%E5%B7%A5%E7%A8%8B%E5%B8%88&jobArea=000000",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(5)

        has_token = await page.evaluate("() => !!localStorage.getItem('token')")
        job_items = await page.evaluate("() => document.querySelectorAll('.joblist-item').length")
        logger.info("Page: %d job items, logged in: %s", job_items, has_token)

        if not has_token:
            print("\n[LOGIN REQUIRED] Please login in the browser, then press Enter...")
            input()
            await asyncio.sleep(3)
            has_token = await page.evaluate("() => !!localStorage.getItem('token')")
            if not has_token:
                logger.error("Login failed. Exiting.")
                await context.close()
                return
            logger.info("Login OK")

        if job_items == 0:
            logger.warning("0 job items on page. Check if IP is blocked.")
            print("Check browser: can you see job listings? Press Enter to continue or Ctrl+C to abort.")
            try:
                input()
            except KeyboardInterrupt:
                await context.close()
                return

        # Main crawl loop
        total_kw = len(keywords)
        logger.info("Starting crawl: %d keywords, %d scout areas, %d full areas",
                    total_kw, len(scout_areas), len(full_areas))

        try:
            while kw_idx < total_kw:
                category, keyword = keywords[kw_idx]
                logger.info("[%d/%d] category=%s keyword=%s phase=%s",
                           kw_idx + 1, total_kw, category, keyword, phase)

                if phase == "scout":
                    before = len(all_rows)
                    await _crawl_areas(
                        page, context, category, keyword, scout_areas,
                        all_rows, seen_ids, request_count, logger,
                    )
                    progress["total"] = len(all_rows)
                    progress["seen_ids"] = list(seen_ids)

                    if no_scout or len(all_rows) > before:
                        logger.info("Scout passed (+%d rows), starting full crawl",
                                   len(all_rows) - before)
                        phase = "full"
                        area_idx = 0
                    else:
                        logger.info("Scout empty, skipping keyword")
                        kw_idx += 1
                        area_idx = 0
                        phase = "scout"
                    progress.update(cat_idx=cat_idx, kw_idx=kw_idx,
                                   area_idx=area_idx, phase=phase)

                elif phase == "full":
                    await _crawl_areas(
                        page, context, category, keyword, full_areas,
                        all_rows, seen_ids, request_count, logger,
                    )
                    kw_idx += 1
                    area_idx = 0
                    phase = "scout"
                    progress["total"] = len(all_rows)
                    progress["seen_ids"] = list(seen_ids)
                    progress.update(cat_idx=cat_idx, kw_idx=kw_idx,
                                   area_idx=area_idx, phase=phase)

                # Auto-save every keyword
                save_progress(progress)
                _save_output(all_rows)
                logger.info("Saved: %d total rows", len(all_rows))

        except KeyboardInterrupt:
            logger.warning("Interrupted. Saving progress...")
            progress.update(cat_idx=cat_idx, kw_idx=kw_idx, area_idx=area_idx, phase=phase)
            progress["total"] = len(all_rows)
            progress["seen_ids"] = list(seen_ids)
            save_progress(progress)
            _save_output(all_rows)
            logger.info("Progress saved. Resume with: python scrape_51job_fetch.py")
        finally:
            await context.close()

    _save_output(all_rows)
    logger.info("Done! Total: %d rows", len(all_rows))


async def _clear_tracking_cookies(context, page, logger) -> int:
    """Delete WAF tracking cookies, keep login cookies. Returns number deleted."""
    cookies = await context.cookies()
    deleted = 0
    for c in cookies:
        name = c["name"]
        domain = c.get("domain", "")
        if "51job" not in domain:
            continue
        is_tracking = any(name.startswith(p) or p in name for p in TRACKING_COOKIE_PATTERNS)
        if is_tracking:
            await context.clear_cookies(name=name, domain=domain)
            deleted += 1
    if deleted:
        logger.info("Cleared %d tracking cookies, reloading page...", deleted)
        await page.goto(
            "https://we.51job.com/pc/search?keyword=%E6%9C%BA%E6%A2%B0%E5%B7%A5%E7%A8%8B%E5%B8%88&jobArea=000000",
            wait_until="domcontentloaded",
        )
        await asyncio.sleep(3)
    return deleted


async def _crawl_areas(
    page, context, category: str, keyword: str, areas: list[tuple[str, str]],
    all_rows: list[dict], seen_ids: set[str], request_count: list[int], logger,
):
    for ai, (area_name, area_code) in enumerate(areas):
        logger.info("  [%d/%d] %s @ %s(%s)", ai + 1, len(areas), keyword, area_name, area_code)

        # Paginate through all pages
        for page_num in range(1, MAX_PAGES + 1):
            # Reset WAF counter before hitting limit
            if request_count[0] >= SAFE_FETCH_LIMIT:
                await _clear_tracking_cookies(context, page, logger)
                request_count[0] = 0

            url = build_signed_url(keyword, area_code, page_num)

            # Random delay to avoid triggering captcha
            delay = random.uniform(DELAY_MIN, DELAY_MAX)
            await asyncio.sleep(delay)

            result = await _safe_fetch(page, url)
            request_count[0] += 1

            if result.get("waf"):
                logger.warning("WAF block at page %d!", page_num)
                break

            if result.get("error"):
                logger.warning("Fetch error at page %d: %s", page_num, result["error"])
                # If page navigated (captcha), pause and wait
                if "navigated" in str(result.get("error", "")):
                    print("\n[CAPTCHA?] Browser page changed. Solve captcha if shown, then press Enter...")
                    input()
                    continue
                break

            items = result.get("items", [])
            total_count = result.get("totalCount", 0)

            if not items:
                break  # no more results

            new_count = 0
            for item in items:
                jid = item.get("jobHref", "") or str(item.get("jobId", ""))
                if jid and jid not in seen_ids:
                    seen_ids.add(jid)
                    all_rows.append(build_row(len(all_rows) + 1, item, category))
                    new_count += 1

            # If we got fewer items than page_size or reached total_count, stop paginating
            if len(items) < PAGE_SIZE:
                break
            if total_count and page_num * PAGE_SIZE >= total_count:
                break

        # Brief pause between areas
        await asyncio.sleep(random.uniform(1.0, 2.0))


async def _safe_fetch(page, url: str) -> dict:
    try:
        return await page.evaluate(FETCH_PAGE_JS, url)
    except Exception as e:
        msg = str(e)
        if "navigation" in msg.lower() or "destroyed" in msg.lower():
            return {"error": "page_navigated"}
        return {"error": msg[:100]}


def _save_output(rows: list[dict]) -> None:
    if not rows:
        return
    OUTPUT_DIR.mkdir(exist_ok=True)
    columns = read_template_columns()
    df = pd.DataFrame(rows, columns=columns)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"fetch_output_{stamp}.xlsx"
    df.to_excel(path, index=False, sheet_name="Sheet1")

    # Also save as latest for resume
    latest = OUTPUT_DIR / "fetch_output.xlsx"
    df.to_excel(latest, index=False, sheet_name="Sheet1")


# ============================================================
# Entry point
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="51job fetch-based crawler")
    parser.add_argument("--category", type=str, default=None,
                       help="Category name (default: all)")
    parser.add_argument("--keywords", type=str, nargs="*", default=None,
                       help="Specific keywords (overrides category)")
    parser.add_argument("--no-scout", action="store_true", default=False)
    parser.add_argument("--delay-min", type=float, default=0.5)
    parser.add_argument("--delay-max", type=float, default=1.5)
    args = parser.parse_args()

    global DELAY_MIN, DELAY_MAX
    DELAY_MIN = args.delay_min
    DELAY_MAX = args.delay_max

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    logger = logging.getLogger("crawl")

    # Build keyword list
    if args.keywords:
        keywords = [(args.category or "未分类", kw) for kw in args.keywords]
    elif args.category and args.category in CATEGORIES:
        keywords = [(args.category, kw) for kw in CATEGORIES[args.category]]
    elif args.category:
        logger.error("Unknown category: %s. Available: %s", args.category, list(CATEGORIES.keys()))
        return
    else:
        keywords = all_keywords()

    # Build area list
    areas = list(PROVINCE_CODES.items())
    scout_areas = [] if args.no_scout else SCOUT_AREAS

    logger.info("=" * 60)
    logger.info("51job Fetch Crawler")
    logger.info("  Keywords: %d", len(keywords))
    logger.info("  Areas: %d provinces + %d scout", len(areas), len(scout_areas))
    logger.info("  Delay: %.1f-%.1fs between requests", DELAY_MIN, DELAY_MAX)
    logger.info("  Scout pruning: %s", "OFF" if args.no_scout else "ON")
    logger.info("=" * 60)

    asyncio.run(crawl(keywords, areas, scout_areas, args.no_scout))


if __name__ == "__main__":
    raise SystemExit(main())
