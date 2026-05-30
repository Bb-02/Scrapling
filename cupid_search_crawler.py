"""
51job crawler using cupid.51job.com noauth search API.
No browser, no login. Pure HTTP requests with 1.5s delay = no WAF trigger.

Usage:
    python cupid_search_crawler.py                     # all 7 categories, 117 keywords
    python cupid_search_crawler.py --category 工程/机械  # single category
    python cupid_search_crawler.py --delay 2.0          # custom delay
    python cupid_search_crawler.py --no-scout           # skip scout, full 34 provinces

Output: ../output/cupid_output.xlsx + cupid_progress.json
Resume: just re-run the same command — auto-resumes from progress.
"""

import argparse
import json
import logging
import re
import sys
import time
import random
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

# ============================================================
# Constants
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = SCRIPT_DIR.parent / "数据模板.xlsx"
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
PROGRESS_FILE = OUTPUT_DIR / "cupid_progress.json"

API_URL = "https://cupid.51job.com/pc/open/noauth/search-h5"
PAGE_SIZE = 200
DELAY_SEC = 2.0  # safe rate for continuous running (never triggers WAF)
MAX_EMPTY_RETRIES = 3  # retry empty responses before moving on
MAX_PAGES = 50  # safety cap
COOLDOWN_SECONDS = 2100  # 35 min wait when WAF blocks (WAF Max-Age=1800s, add margin)
MAX_CONSECUTIVE_COOLDOWNS = 3  # exit if blocked this many times in a row
CYCLE_CRAWL_SECONDS = 2400  # 40 min: crawl before taking a break
CYCLE_REST_SECONDS = 600    # 10 min: rest to reset WAF timer


class WafBlocked(Exception):
    """Raised when cupid API returns 405 (WAF block)."""

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
    "项目经理/管理": [
        "机械项目管理", "汽车项目管理", "通信项目管理", "项目助理",
        "项目工程师", "项目总监", "ERP 实施顾问", "建筑工程管理 / 项目经理",
        "项目经理", "生产项目经理", "项目主管",
    ],
    "工程/机械": [
        "机械工程师", "机械研发工程师", "机械结构工程师", "CNC / 数控操机",
        "机电工程师", "机械制图", "机械研发经理 / 主管", "机械装配工程师",
        "CNC / 数控编程", "实验室负责人 / 工程师", "焊接工程师", "机械产品工程师",
        "飞行器设计与制造", "船舶工程师", "工艺 / 制程工程师", "机械设备工程师",
        "机械项目管理", "机械设计", "工业工程师", "模具工程师", "材料工程师",
        "注塑工程师", "机械设备经理 / 主管", "模具设计", "夹具工程师",
        "铸造 / 锻造工程师 / 技师", "机械维修 / 保养", "冲压工程师",
        "光源与照明工程", "轨道交通工程师", "CAD 绘图", "热能工程师",
        "飞机维修机械师", "仿真应用工程师",
    ],
    "普工": [
        "普工 / 操作工", "包装工", "搬运工", "组装工", "学徒工", "装卸工",
    ],
    "技工": [
        "电工", "空调工", "电梯工", "水工", "仪表工", "机修工", "锅炉工",
        "木工", "技工", "喷塑工",
    ],
    "运输设备操作": [
        "叉车司机", "铲车司机", "吊车司机", "挖掘机司机",
    ],
    "机械加工": [
        "焊工", "CNC / 数控操机", "车工", "切割技工", "铣工", "抛光工",
        "冲压工", "氩弧焊工", "钳工", "模具工", "磨工", "注塑工",
        "折弯工", "钣金工", "钻工", "电镀工 / 镀膜操作", "镗工",
        "炼胶工", "刨工", "铆工", "模切工", "吹膜工", "硫化工", "技工",
    ],
    "质量管理": [
        "质检员 QC", "质量 / 品质主管", "EHS 安全工程师", "体系工程师",
        "生产安全员", "体系认证审核员", "采购材料、设备质量", "质量工程师",
        "质量 / 品质经理", "EHS 安全经理 / 主管", "计量工程师",
        "供应商质量工程师", "认证工程师", "服装纺织质检员 (QA/QC)",
        "汽车质量工程师", "客户质量工程师", "前期质量工程师", "过程质量工程师",
        "药品生产 / 质量管理", "医疗器械生产 / 质量相关岗位",
        "化学分析测试员", "可靠度工程师", "故障分析工程师",
    ],
}

# ============================================================
# Data parsing
# ============================================================

def extract_job_id_from_url(url: str) -> str:
    """Extract numeric job ID from URL for cross-API dedup."""
    if not url:
        return ""
    m = re.search(r"/(\d+)\.html", url)
    return m.group(1) if m else url


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

    # welfare — cupid uses jobWelfareCodeDataList
    welfare = item.get("jobWelfareCodeDataList", []) or []
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
    tags = item.get("jobTags", []) or []
    if isinstance(tags, list):
        tags_str = " / ".join(
            t.get("jobTagName", "") if isinstance(t, dict) else str(t) for t in tags
        )
    else:
        tags_str = str(tags) if tags else ""

    # industry
    func_types = []
    for k in ("industryType1Str", "industryType2Str"):
        v = item.get(k, "")
        if v and v not in func_types:
            func_types.append(v)

    # description — cupid has jobDescribe directly
    work_content, requirements = parse_job_description(
        item.get("jobDescribe", "")
    )

    # jobHref — cupid returns msearch.51job.com format
    job_href = item.get("jobHref", "")
    if not job_href:
        jid = item.get("jobId", "")
        if jid:
            job_href = f"https://jobs.51job.com/{jid}.html"

    return {
        "序号": idx,
        "招聘平台": "前程无忧",
        "岗位类型\n一级": category,
        "岗位类型\n二级": " / ".join(func_types) if func_types else "/",
        "岗位名称": item.get("jobName", ""),
        "岗位类型\n企业/公务员/事业单位/军队文职": "企业",
        "公司名称": item.get("fullCompanyName", "") or item.get("companyName", ""),
        "公司规模": item.get("companySizeString", "/"),
        "所在省份": province,
        "城市": city,
        "详细地址": item.get("jobAreaString", ""),
        "学历要求": item.get("degreeString", "/"),
        "经验要求": item.get("workYearString", "/"),
        "薪资范围": item.get("provideSalaryString", "/"),
        "福利标签": welfare_str or "/",
        "工作内容": work_content or "/",
        "任职要求": requirements or "/",
        "岗位链接": job_href,
        "发布时间": item.get("issueDateString", ""),
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
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        logging.getLogger("crawl").warning(
            "Progress file corrupt, trying recovery..."
        )
        for suffix in (".tmp", ".bak"):
            recovery = Path(str(PROGRESS_FILE) + suffix)
            if recovery.exists():
                try:
                    with open(recovery, "r", encoding="utf-8") as f:
                        return json.load(f)
                except (json.JSONDecodeError, IOError):
                    pass
        return {
            "cat_idx": 0, "kw_idx": 0, "area_idx": 0,
            "phase": "scout", "total": 0, "seen_ids": [],
        }


def save_progress(progress: dict) -> None:
    """Save only metadata + seen_ids (not all_rows — those go to Excel)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    slim = {
        "cat_idx": progress.get("cat_idx", 0),
        "kw_idx": progress.get("kw_idx", 0),
        "area_idx": progress.get("area_idx", 0),
        "phase": progress.get("phase", "scout"),
        "total": progress.get("total", 0),
        "seen_ids": progress.get("seen_ids", []),
    }
    tmp = Path(str(PROGRESS_FILE) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=False, indent=2)
    # Atomic rename — crash during write() leaves main intact
    tmp.replace(PROGRESS_FILE)


# ============================================================
# API client
# ============================================================

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://m.51job.com/",
}


def api_search(keyword: str, job_area: str, page_num: int,
               page_size: int = PAGE_SIZE) -> dict:
    """Call cupid search-h5 API. Returns {"items": [...], "totalCount": N} or {"waf": True}."""
    ts = str(int(time.time() * 1000))
    params = {
        "api_key": "51job",
        "timestamp": ts,
        "keyword": keyword,
        "jobArea": job_area,
        "pageNum": str(page_num),
        "pageSize": str(page_size),
        "searchType": "2",
    }
    try:
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
        if resp.status_code == 405:
            return {"items": [], "totalCount": 0, "waf": True}
        if "application/json" not in (resp.headers.get("content-type") or ""):
            return {"items": [], "totalCount": 0, "waf": True, "waf_detail": f"HTTP {resp.status_code} non-JSON"}
        resp.raise_for_status()
        data = resp.json()
        job = data.get("resultbody", {}).get("job", {})
        return {
            "items": job.get("items") or [],
            "totalCount": job.get("totalCount") or 0,
        }
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 405:
            return {"items": [], "totalCount": 0, "waf": True}
        return {"items": [], "totalCount": 0, "error": str(e)}
    except Exception as e:
        return {"items": [], "totalCount": 0, "error": str(e)}


def api_search_with_retry(keyword: str, job_area: str, page_num: int,
                           logger, max_retries: int = MAX_EMPTY_RETRIES) -> dict:
    """Call API with retry on empty responses. WAF blocks are returned immediately."""
    for attempt in range(max_retries + 1):
        result = api_search(keyword, job_area, page_num)
        if result.get("waf"):
            return result  # no retry — retrying resets WAF timer
        if result.get("error"):
            logger.warning("API error: %s (attempt %d/%d)",
                          result["error"], attempt + 1, max_retries + 1)
            if attempt < max_retries:
                time.sleep(2.0)
                continue
            return result
        if result["items"] or result["totalCount"] > 0:
            if attempt > 0:
                logger.info("  Retry #%d succeeded", attempt)
            return result
        if attempt < max_retries:
            time.sleep(1.5)
    return result


# ============================================================
# Main crawler
# ============================================================

def crawl(
    keywords: list[tuple[str, str]],
    areas: list[tuple[str, str]],
    scout_areas: list[tuple[str, str]],
    no_scout: bool = False,
):
    logger = logging.getLogger("crawl")
    OUTPUT_DIR.mkdir(exist_ok=True)

    progress = load_progress()
    cat_idx = progress.get("cat_idx", 0)
    kw_idx = progress.get("kw_idx", 0)
    area_idx = progress.get("area_idx", 0)
    phase = progress.get("phase", "scout")
    all_rows: list[dict] = progress.get("all_rows", [])
    seen_ids: set[str] = set(progress.get("seen_ids", []))

    # Resume from saved output — try main first, fall back to largest backup
    resume_path = OUTPUT_DIR / "cupid_output.xlsx"
    if resume_path.exists():
        try:
            df = pd.read_excel(resume_path)
            if len(df) < 1000:  # too small → corrupted, try backup
                resume_path = None
        except Exception:
            resume_path = None
    if resume_path is None:
        # Main file missing/corrupt — find largest backup by file size
        backups = sorted(OUTPUT_DIR.glob("cupid_output_20*.xlsx"),
                         key=lambda x: x.stat().st_size, reverse=True)
        for bk in backups[:2]:
            try:
                df = pd.read_excel(bk)
                if len(df) > 1000:
                    resume_path = bk
                    break
            except Exception:
                continue
    if resume_path is None:
        logger.warning("No valid Excel found — starting fresh")
    if resume_path and not all_rows:
        df = pd.read_excel(resume_path)
        for _, row in df.iterrows():
            d = row.to_dict()
            jid = extract_job_id_from_url(str(d.get("岗位链接", "")))
            if jid and jid not in seen_ids:
                seen_ids.add(jid)
                all_rows.append(d)
        for i, row in enumerate(all_rows):
            row["序号"] = i + 1
        logger.info("Resumed %d rows from %s", len(all_rows), resume_path.name)
    elif not all_rows:
        logger.warning("No valid Excel found to resume from — starting fresh")

    # Convert seen_ids to jobId-based if they contain old URLs
    old_seen = list(seen_ids)
    new_seen = set()
    for sid in old_seen:
        if "51job.com" in sid or ".html" in sid:
            jid = extract_job_id_from_url(sid)
            if jid:
                new_seen.add(jid)
        else:
            new_seen.add(sid)
    seen_ids = new_seen

    scout_codes = {c for _, c in scout_areas}
    full_areas = [(n, c) for n, c in areas if c not in scout_codes] if not no_scout else areas

    total_kw = len(keywords)
    logger.info("Starting cupid crawl: %d keywords, %d scout areas, %d full areas",
                total_kw, len(scout_areas), len(full_areas))
    logger.info("API: %s, pageSize=%d, delay=%.1fs", API_URL, PAGE_SIZE, DELAY_SEC)

    cooldown_streak = 0
    cycle_start = time.time()  # for 40-min crawl / 10-min rest cycle

    try:
        while kw_idx < total_kw:
            category, keyword = keywords[kw_idx]
            logger.info("[%d/%d] category=%s keyword=%s phase=%s",
                       kw_idx + 1, total_kw, category, keyword, phase)

            try:
                if phase == "scout":
                    before = len(all_rows)
                    crawl_areas(
                        category, keyword, scout_areas,
                        all_rows, seen_ids, logger,
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
                    remaining = full_areas[area_idx:]
                    last_ai = crawl_areas(
                        category, keyword, remaining,
                        all_rows, seen_ids, logger,
                        area_offset=area_idx,
                    )
                    area_idx = area_idx + last_ai
                    if area_idx >= len(full_areas):
                        kw_idx += 1
                        area_idx = 0
                        phase = "scout"
                    progress["total"] = len(all_rows)
                    progress["seen_ids"] = list(seen_ids)
                    progress.update(cat_idx=cat_idx, kw_idx=kw_idx,
                                   area_idx=area_idx, phase=phase)

                # Auto-save every keyword
                cooldown_streak = 0  # reset on success
                save_progress(progress)
                _save_output(all_rows)
                logger.info("Saved: %d total rows", len(all_rows))

                # Cycle rest: 40 min crawl → 10 min break to avoid WAF
                if time.time() - cycle_start > CYCLE_CRAWL_SECONDS:
                    logger.info(
                        "Cycle rest: crawling for %.0f min, pausing %.0f min...",
                        (time.time() - cycle_start) / 60, CYCLE_REST_SECONDS / 60,
                    )
                    remaining_rest = CYCLE_REST_SECONDS
                    while remaining_rest > 0:
                        chunk = min(60, remaining_rest)
                        time.sleep(chunk)
                        remaining_rest -= chunk
                    cycle_start = time.time()
                    logger.info("Cycle rest done, resuming...")

            except WafBlocked:
                progress["total"] = len(all_rows)
                progress["seen_ids"] = list(seen_ids)
                progress.update(cat_idx=cat_idx, kw_idx=kw_idx,
                               area_idx=area_idx, phase=phase)
                save_progress(progress)
                _save_output(all_rows)
                logger.warning("Progress saved before cooldown: %d rows", len(all_rows))

                cooldown_streak += 1
                if cooldown_streak > MAX_CONSECUTIVE_COOLDOWNS:
                    logger.error(
                        "Blocked %d times in a row — giving up. "
                        "Re-run later when IP is clean.",
                        cooldown_streak - 1,
                    )
                    sys.exit(1)

                logger.warning(
                    "WAF cooldown #%d: waiting %.0f min (%d seconds)...",
                    cooldown_streak, COOLDOWN_SECONDS / 60, COOLDOWN_SECONDS,
                )
                # Sleep but check every 60s in case user hits Ctrl+C
                remaining_sleep = COOLDOWN_SECONDS
                while remaining_sleep > 0:
                    chunk = min(60, remaining_sleep)
                    time.sleep(chunk)
                    remaining_sleep -= chunk
                logger.info("Cooldown done, resuming...")
                cycle_start = time.time()  # reset cycle timer after cooldown

    except KeyboardInterrupt:
        logger.warning("Interrupted. Saving progress...")
        progress["total"] = len(all_rows)
        progress["seen_ids"] = list(seen_ids)
        progress.update(cat_idx=cat_idx, kw_idx=kw_idx,
                       area_idx=area_idx, phase=phase)
        save_progress(progress)
        _save_output(all_rows)
        logger.info("Progress saved. Resume with: python cupid_search_crawler.py")

    _save_output(all_rows)
    logger.info("Done! Total: %d rows", len(all_rows))


def crawl_areas(
    category: str, keyword: str, areas: list[tuple[str, str]],
    all_rows: list[dict], seen_ids: set[str], logger,
    area_offset: int = 0,
) -> int:
    """Crawl areas for one keyword. Returns number of areas processed."""
    request_count = 0
    last_req = 0.0  # timestamp of last request, for rate limiting

    for ai, (area_name, area_code) in enumerate(areas):
        logger.info("  [%d/%d] %s @ %s(%s)",
                   area_offset + ai + 1, area_offset + len(areas),
                   keyword, area_name, area_code)

        for page_num in range(1, MAX_PAGES + 1):
            # Rate limit: ensure at least DELAY_SEC between requests
            gap = time.time() - last_req
            if gap < DELAY_SEC:
                time.sleep(DELAY_SEC - gap)

            result = api_search_with_retry(keyword, area_code, page_num, logger)
            last_req = time.time()
            request_count += 1

            if result.get("waf"):
                logger.warning("WAF block detected at %s page %d!", area_name, page_num)
                raise WafBlocked()

            items = result.get("items", [])
            total_count = result.get("totalCount", 0)

            if not items:
                break

            new_count = 0
            for item in items:
                jid = str(item.get("jobId", ""))
                if jid and jid not in seen_ids:
                    seen_ids.add(jid)
                    all_rows.append(build_row(len(all_rows) + 1, item, category))
                    new_count += 1

            if len(items) < PAGE_SIZE:
                break
            if total_count and page_num * PAGE_SIZE >= total_count:
                break

        # Progress report every 10 areas
        if request_count > 0 and request_count % 10 == 0:
            logger.info("  ... %d requests, %d total rows", request_count, len(all_rows))

    return len(areas)


def _save_output(rows: list[dict]) -> None:
    if not rows:
        return
    OUTPUT_DIR.mkdir(exist_ok=True)
    columns = read_template_columns()
    df = pd.DataFrame(rows, columns=columns)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 1) timestamped snapshot (safe — never overwritten except by next snapshot)
    path = OUTPUT_DIR / f"cupid_output_{stamp}.xlsx"
    df.to_excel(path, index=False, sheet_name="Sheet1")

    # 2) atomic main file: write to .tmp then rename
    latest = OUTPUT_DIR / "cupid_output.xlsx"
    tmp = OUTPUT_DIR / "cupid_output.xlsx.tmp"
    df.to_excel(tmp, index=False, sheet_name="Sheet1")
    tmp.replace(latest)


# ============================================================
# Entry point
# ============================================================

def all_keywords() -> list[tuple[str, str]]:
    result = []
    for cat, kws in CATEGORIES.items():
        for kw in kws:
            result.append((cat, kw))
    return result


def main():
    global DELAY_SEC
    parser = argparse.ArgumentParser(description="51job cupid API crawler")
    parser.add_argument("--category", type=str, default=None)
    parser.add_argument("--keywords", type=str, nargs="*", default=None)
    parser.add_argument("--no-scout", action="store_true", default=False)
    parser.add_argument("--delay", type=float, default=DELAY_SEC)
    args = parser.parse_args()
    DELAY_SEC = args.delay

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
    logger = logging.getLogger("crawl")

    if args.keywords:
        keywords = [(args.category or "未分类", kw) for kw in args.keywords]
    elif args.category and args.category in CATEGORIES:
        keywords = [(args.category, kw) for kw in CATEGORIES[args.category]]
    elif args.category:
        logger.error("Unknown category: %s. Available: %s",
                    args.category, list(CATEGORIES.keys()))
        sys.exit(1)
    else:
        keywords = all_keywords()

    areas = list(PROVINCE_CODES.items())
    scout_areas = [] if args.no_scout else SCOUT_AREAS

    logger.info("=" * 60)
    logger.info("51job Cupid Crawler (noauth)")
    logger.info("  Keywords: %d", len(keywords))
    logger.info("  Areas: %d provinces + %d scout", len(areas), len(scout_areas))
    logger.info("  API: %s", API_URL)
    logger.info("  Delay: %.1fs, PageSize: %d", DELAY_SEC, PAGE_SIZE)
    logger.info("  Scout pruning: %s", "OFF" if args.no_scout else "ON")
    logger.info("=" * 60)

    crawl(keywords, areas, scout_areas, args.no_scout)


if __name__ == "__main__":
    main()
