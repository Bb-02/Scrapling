"""
51job "工程/机械" 类别全国职位爬虫。

策略: 浏览器 + XHR 拦截获取搜索 API JSON，子标签 × 地域多维度拆分，
最大限度突破 51job 单次搜索 1,600 条限制。
"""

import argparse
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from scrapling.fetchers import StealthySession

from search_api import (
    SearchAPIClient,
    PROVINCE_CODES,
    MAJOR_CITY_CODES,
    parse_api_response,
)

# ============================================================
# 配置
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = SCRIPT_DIR.parent / "数据模板.xlsx"
OUTPUT_DIR = SCRIPT_DIR.parent / "output"
PROGRESS_FILE = SCRIPT_DIR.parent / "crawl_progress.json"

DEFAULT_PLATFORM = "前程无忧"
DEFAULT_CATEGORY_L1 = "工程/机械"
DEFAULT_JOB_AREA = "000000"  # 全国，但会被 IP 覆盖，实际用地域拆分
DEFAULT_MAX_PAGES = 80

# 工程/机械 下的子标签（从 51job 左侧筛选栏获取）
SUB_CATEGORIES = [
    "机械工程师",
    "机电工程师",
    "结构工程师",
    "模具工程师",
    "设备工程师",
    "机械设计",
    "机械绘图员",
    "机械维修",
    "机械工艺",
    "机械制图",
    "机械自动化",
    "电气工程师",
    "自动化工程师",
    "工业工程师",
    "材料工程师",
    "焊接工程师",
    "铸造工程师",
    "锻造工程师",
    "冲压工程师",
    "注塑工程师",
    "CNC工程师",
    "数控编程",
    "质量管理",
    "质量工程师",
    "机械质检",
    "工程监理",
    "工程项目管理",
    "土木工程",
    "建筑工程",
    "暖通工程师",
    "给排水工程师",
    "水利工程",
    "岩土工程",
    "测绘工程师",
    "安全工程师",
    "焊接",
    "钣金",
    "车工",
    "磨工",
    "铣工",
    "钳工",
    "电焊工",
    "装配工",
    "维修工",
    "电工",
    "技工",
    "操作工",
    "生产技术",
    "工艺工程师",
    "PE工程师",
    "IE工程师",
    "NPI工程师",
    "ME工程师",
    "测试工程师",
    "可靠性工程师",
    "实验室技术员",
    "工程经理",
    "项目工程师",
    "研发工程师",
    "产品工程师",
    "制冷工程师",
    "热能工程师",
    "液压工程师",
    "气动工程师",
    "船舶工程师",
    "汽车工程师",
    "医疗器械工程师",
    "仪器仪表工程师",
    "机器人工程师",
    "无人机工程师",
]


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
# 城市/省份 映射
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


def infer_province(city: str) -> str:
    if not city:
        return ""
    if city in CITY_PROVINCE:
        return CITY_PROVINCE[city]
    return ""


def parse_location(job_area_string: str) -> tuple[str, str]:
    """从 jobAreaString 拆分城市和省。格式如 '广州·天河区' 或 '深圳'。"""
    if not job_area_string:
        return "", ""
    parts = re.split(r"[·\-]", job_area_string)
    city = parts[0].strip() if parts else ""
    province = infer_province(city)
    # 直辖市处理
    if city in ("北京", "上海", "天津", "重庆"):
        province = city + "市"
    return province, city


def parse_job_description(job_describe: str) -> tuple[str, str]:
    """拆分职位描述为工作内容和任职要求。"""
    if not job_describe:
        return "", ""

    text = str(job_describe)
    # 常见分隔模式
    requirement_pattern = re.compile(
        r"(任职要求|任职资格|职位要求|岗位要求|应聘要求|资格要求|任职条件)[:：】\]\s]*"
    )
    m = requirement_pattern.search(text)
    if not m:
        return text, ""
    work_content = text[:m.start()].strip()
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


def build_row(idx: int, item: dict) -> dict:
    """将 API 数据转为模板 Excel 行。"""
    province, city = parse_location(item.get("jobAreaString", ""))
    work_content, requirements = parse_job_description(item.get("jobDescribe", ""))

    # 福利标签
    welfare = item.get("welfareList", [])
    if isinstance(welfare, list) and welfare:
        if isinstance(welfare[0], dict):
            welfare_str = " / ".join(
                w.get("chineseTitle", "") or w.get("typeTitle", "")
                for w in welfare
            )
        else:
            welfare_str = " / ".join(str(w) for w in welfare)
    else:
        welfare_str = ""

    # 子标签
    tags = item.get("jobTags", [])
    if isinstance(tags, list):
        tags_str = " / ".join(
            t.get("jobTagName", "") if isinstance(t, dict) else str(t)
            for t in tags
        )
    else:
        tags_str = str(tags) if tags else ""

    # 岗位类型二级
    func_types = []
    for k in ("industryType1", "industryType2"):
        v = item.get(k, "")
        if v and v not in func_types:
            func_types.append(v)
    func_type_str = " / ".join(func_types)

    return {
        "序号": idx,
        "招聘平台": DEFAULT_PLATFORM,
        "岗位类型\n一级": DEFAULT_CATEGORY_L1,
        "岗位类型\n二级": func_type_str or "/",
        "岗位名称": item.get("jobName", ""),
        "岗位类型\n企业/公务员/事业单位/军队文职": "企业",
        "公司名称": item.get("fullCompanyName", "") or item.get("companyName", ""),
        "公司规模": item.get("companySize", "/"),
        "所在省份": province,
        "城市": city,
        "详细地址": item.get("jobAreaString", "/"),
        "学历要求": item.get("degree", "/"),
        "经验要求": item.get("workYear", "/"),
        "薪资范围": item.get("salary", "/"),
        "福利标签": welfare_str or "/",
        "工作内容": work_content or "/",
        "任职要求": requirements or "/",
        "岗位链接": item.get("jobHref", ""),
        "发布时间": item.get("issueDate", "") or item.get("updateDate", ""),
        "投递起始时间": "/",
        "投递截止时间": "/",
        "证书要求": "/",
        "备注（技能要求）": tags_str or "/",
    }


# ============================================================
# 断点续爬
# ============================================================

def load_progress() -> dict:
    if not PROGRESS_FILE.exists():
        return {"keyword_idx": 0, "area_idx": 0, "total": 0}
    try:
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return {"keyword_idx": 0, "area_idx": 0, "total": 0}


def save_progress(keyword_idx: int, area_idx: int, total: int) -> None:
    PROGRESS_FILE.write_text(
        json.dumps(
            {"keyword_idx": keyword_idx, "area_idx": area_idx, "total": total},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="51job 工程/机械 全国爬虫")
    parser.add_argument("--keywords", type=str, nargs="*", default=None,
                        help="指定子标签，默认使用内置列表")
    parser.add_argument("--areas", type=str, default="provinces",
                        choices=["provinces", "cities", "both"],
                        help="地域拆分级别: provinces=省级(默认), cities=城市级, both=两者")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--resume", action="store_true", default=False,
                        help="从断点继续")
    parser.add_argument("--limit-keywords", type=int, default=0,
                        help="限制子标签数量（测试用）")
    parser.add_argument("--limit-areas", type=int, default=0,
                        help="限制地域数量（测试用）")
    args = parser.parse_args()

    # 准备搜索列表
    keywords = args.keywords if args.keywords else SUB_CATEGORIES
    if args.limit_keywords > 0:
        keywords = keywords[:args.limit_keywords]

    if args.areas == "provinces":
        areas = list(PROVINCE_CODES.items())
    elif args.areas == "cities":
        areas = list(MAJOR_CITY_CODES.items())
    else:
        areas = list(PROVINCE_CODES.items()) + list(MAJOR_CITY_CODES.items())

    if args.limit_areas > 0:
        areas = areas[:args.limit_areas]

    # 断点续爬
    progress = load_progress()
    start_ki = progress.get("keyword_idx", 0) if args.resume else 0
    start_ai = progress.get("area_idx", 0) if args.resume else 0
    all_rows: list[dict] = progress.get("rows", []) if args.resume else []

    logger.info(
        "配置: %d 子标签 × %d 地域, 最多 %d 页/搜索",
        len(keywords), len(areas), args.max_pages,
    )
    if args.resume and start_ki > 0:
        logger.info("从断点恢复: keyword_idx=%d, area_idx=%d, 已累计 %d 条",
                     start_ki, start_ai, len(all_rows))

    # 爬取
    client = SearchAPIClient(headless=True, timeout=60000)
    seen_job_ids: set[str] = {row.get("岗位链接", "") for row in all_rows if row.get("岗位链接")}

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ki = start_ki
    ai = start_ai
    try:
        with StealthySession(
            headless=True,
            solve_cloudflare=True,
            real_chrome=True,
            network_idle=True,
            wait=3000,
            timeout=60000,
            capture_xhr=r"https://we\.51job\.com/api/job/search-pc.*",
            google_search=False,
            hide_canvas=True,
            block_webrtc=True,
        ) as session:
            for ki in range(start_ki, len(keywords)):
                keyword = keywords[ki]
                ai_start = start_ai if ki == start_ki else 0

                for ai in range(ai_start, len(areas)):
                    area_name, area_code = areas[ai]
                    logger.info("=== [%d/%d] %s @ %s(%s) ===",
                                ki + 1, len(keywords), keyword, area_name, area_code)

                    items = client.search_all_pages(
                        session, keyword, area_code,
                        max_pages=args.max_pages,
                    )

                    new_count = 0
                    for item in items:
                        jid = item.get("jobHref", "") or item.get("jobId", "")
                        if jid and jid not in seen_job_ids:
                            seen_job_ids.add(jid)
                            all_rows.append(build_row(len(all_rows) + 1, item))
                            new_count += 1

                    logger.info(
                        "-> %s@%s: 新增 %d, 累计 %d 条",
                        keyword, area_name, new_count, len(all_rows),
                    )

                    save_progress(ki, ai + 1, len(all_rows))

                    # 定期保存中间结果
                    if len(all_rows) % 500 == 0 and all_rows:
                        _save_intermediate(all_rows)

    except KeyboardInterrupt:
        logger.info("用户中断，保存当前进度...")
        save_progress(ki, ai, len(all_rows))
        _save_intermediate(all_rows)
        logger.info("进度已保存，共 %d 条", len(all_rows))
        return
    except Exception:
        logger.exception("爬取异常，保存进度...")
        save_progress(ki, ai, len(all_rows))
        _save_intermediate(all_rows)
        raise

    # 最终保存
    if not all_rows:
        logger.warning("未获取到任何数据")
        return

    _save_final(all_rows)
    PROGRESS_FILE.unlink(missing_ok=True)
    logger.info("全部完成!")


def _save_intermediate(rows: list[dict]) -> None:
    """保存中间结果。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"51job_engineering_partial_{stamp}.xlsx"
    columns = read_template_columns()
    df = pd.DataFrame(rows, columns=columns)
    df.to_excel(path, index=False, sheet_name="Sheet1")
    logger.info("中间结果已保存: %s (%d 条)", path, len(rows))


def _save_final(rows: list[dict]) -> None:
    """保存最终结果。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"51job_engineering_{stamp}.xlsx"
    columns = read_template_columns()
    df = pd.DataFrame(rows, columns=columns)
    df.to_excel(path, index=False, sheet_name="Sheet1")
    logger.info("最终结果: %s (%d 条)", path, len(rows))


if __name__ == "__main__":
    raise SystemExit(main())
