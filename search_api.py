"""
51job 搜索 API 客户端。

通过 StealthySession 浏览器加载搜索页，使用 page_action 从 Vue SPA
渲染后的 DOM 中直接提取职位数据，绕过 Aliyun WAF 对 API 端点的拦截。

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


def _parse_job_text(text: str, sensor_data: dict) -> dict:
    """从 joblist-item 的 innerText 和 sensorsdata 中提取完整职位数据."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return {}

    # sensorsdata 提供核心结构字段
    item = {
        "jobId": sensor_data.get("jobId", ""),
        "jobName": lines[0] if lines else sensor_data.get("jobTitle", ""),
        "jobType": sensor_data.get("jobType", "0"),
        "companyId": sensor_data.get("companyId", ""),
        "isPromotion": sensor_data.get("isPromote", "否") == "是",
    }

    # 解析行内容
    # 行模式: [职位名, 薪资, 地区, 活跃状态, ...技能标签..., ...福利标签..., 公司名, 行业/类型/规模, 操作按钮]
    idx = 0
    # 第0行 = 职位名 (已处理)
    idx = 1

    # 第1行 = 薪资
    if idx < len(lines) and _looks_like_salary(lines[idx]):
        item["provideSalaryString"] = lines[idx]
        idx += 1
    else:
        item["provideSalaryString"] = sensor_data.get("jobSalary", "")

    # 第2行 = 工作地点
    if idx < len(lines) and _looks_like_location(lines[idx]):
        item["jobAreaString"] = lines[idx]
        item["workArea"] = lines[idx]
        idx += 1
    else:
        item["jobAreaString"] = sensor_data.get("jobArea", "")
        item["workArea"] = sensor_data.get("jobArea", "")

    # 跳过活跃状态行 (如 "今日活跃", "回复率高", "简历处理快")
    if idx < len(lines) and _is_activity_status(lines[idx]):
        idx += 1

    # 收集技能/描述标签 (在福利标签前)
    skill_tags = []
    while idx < len(lines):
        line = lines[idx]
        if _is_welfare_tag(line):
            break
        if _looks_like_company(line):
            break
        if _looks_like_action_button(line):
            idx += 1
            continue
        if line and not _looks_like_salary(line) and not _looks_like_location(line):
            skill_tags.append(line)
        idx += 1

    # 收集福利标签
    welfare_tags = []
    while idx < len(lines):
        line = lines[idx]
        if _looks_like_company(line):
            break
        if _looks_like_action_button(line):
            idx += 1
            continue
        if _is_welfare_tag(line) or (line and not _looks_like_salary(line)):
            welfare_tags.append(line)
        idx += 1

    # 公司名
    if idx < len(lines):
        item["companyName"] = lines[idx]
        item["fullCompanyName"] = lines[idx]
        idx += 1

    # 行业/公司类型/公司规模 (如 "机械/设备/重工民营50-150人")
    if idx < len(lines) and not _looks_like_action_button(lines[idx]):
        item["industryCompanyInfo"] = lines[idx]
        idx += 1

    # 跳过操作按钮
    while idx < len(lines) and _looks_like_action_button(lines[idx]):
        idx += 1

    item["jobTags"] = skill_tags
    item["jobTagsList"] = [{"jobTagName": t} for t in skill_tags]
    item["jobWelfareCodeDataList"] = [{"chineseTitle": w, "typeTitle": w} for w in welfare_tags]
    item["welfareList"] = welfare_tags

    # 从传感器数据补充
    item["degreeString"] = sensor_data.get("jobDegree", "")
    item["workYearString"] = sensor_data.get("jobYear", "")
    item["issueDateString"] = sensor_data.get("jobTime", "")
    item["salary"] = item.get("provideSalaryString", "")
    item["degree"] = item.get("degreeString", "")
    item["workYear"] = item.get("workYearString", "")
    item["issueDate"] = item.get("issueDateString", "")
    item["updateDate"] = item.get("issueDateString", "")
    item["jobHref"] = ""
    item["companyHref"] = ""
    item["companySize"] = ""
    item["industryType1Str"] = ""
    item["industryType2Str"] = ""
    item["companyTypeString"] = ""
    item["companyIndustryType1Str"] = ""
    item["companyIndustryType2Str"] = ""
    item["jobDescribe"] = ""
    item["hrName"] = ""
    item["lon"] = ""
    item["lat"] = ""
    item["isIntern"] = False

    return item


def _looks_like_salary(text: str) -> bool:
    return bool(re.match(r"^[\d.]+[千百万元万千]/", text) or re.match(r"^\d", text) and ("万" in text or "千" in text or "元" in text or "薪" in text or "-" in text[:6]))


def _looks_like_location(text: str) -> bool:
    """检查是否像地点 (如 '西安', '北京·通州区', '广州·天河区')."""
    return bool(re.match(r"^[一-鿿]{2,}(·[一-鿿]+)?$", text) and len(text) <= 15)


def _is_activity_status(text: str) -> bool:
    return text in ("今日活跃", "回复率高", "简历处理快", "简历处理慢", "近期活跃", "本周活跃")


def _is_welfare_tag(text: str) -> bool:
    common_welfare = {"五险一金", "五险", "年终奖金", "绩效奖金", "定期体检", "员工旅游",
                      "餐饮补贴", "节日福利", "交通补贴", "带薪年假", "住房补贴",
                      "免费班车", "通讯补贴", "加班补助", "弹性工作", "周末双休",
                      "全勤奖", "专业培训", "包吃", "包住", "免费工作餐",
                      "项目奖金", "补充公积金", "补充医疗保险", "高温补贴",
                      "出差补贴", "股票期权", "做五休二", "节日礼品",
                      "生日福利", "团建活动", "年度旅游", "下午茶"}
    return text in common_welfare or bool(re.match(r"^[一-鿿]+(金|险|贴|奖|假|餐|住|车|游|检|训|休|薪)$", text))


def _looks_like_company(text: str) -> bool:
    """检查是否像公司名 (含有限公司/科技/集团等)."""
    return bool(re.search(r"(公司|集团|有限|科技|实业|股份|企业|中心|工厂)", text)) and len(text) >= 6


def _looks_like_action_button(text: str) -> bool:
    return text in ("去聊聊", "投递", "收藏", "申请", "立即沟通")


# ---- 兼容旧 API 的函数 ----

def parse_api_response(json_data: dict) -> list[dict]:
    """从搜索 API JSON 响应中提取职位列表（保留用于兼容，实际不再使用）。"""
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
    """从 API 响应中提取总职位数（保留用于兼容）。"""
    return (
        json_data.get("resultbody", {})
        .get("job", {})
        .get("totalCount", 0)
    )


# ============================================================
# 搜索客户端 (DOM 提取版)
# ============================================================

class SearchAPIClient:
    """通过浏览器 + DOM 提取获取 51job 搜索数据。"""

    def __init__(self, headless: bool = True, timeout: int = 60000):
        self.headless = headless
        self.timeout = timeout

    def _extract_from_dom(self, page) -> dict:
        """从页面 DOM 中提取职位数据和总条数。返回 {"items": [...], "totalCount": N}。"""
        return page.evaluate("""
            () => {
                const result = {items: [], totalCount: 0};

                document.querySelectorAll('.joblist-item').forEach(el => {
                    const wrapper = el.querySelector('[sensorsdata]');
                    if (!wrapper) return;

                    let sd = {};
                    try { sd = JSON.parse(wrapper.getAttribute('sensorsdata')); } catch(e) {}

                    // DOM 选择器提取
                    const jobNameEl = el.querySelector('.jname');
                    const salEl = el.querySelector('.sal');
                    const areaEl = el.querySelector('.area div');
                    const tagEls = el.querySelectorAll('.tag');
                    const compLink = el.querySelector('a.comp');

                    // 公司信息和链接
                    const compText = compLink ? compLink.innerText : '';
                    const compLines = compText.split('\\n').map(l => l.trim()).filter(Boolean);
                    const companyName = compLines[0] || '';
                    const industryInfo = compLines[1] || '';  // e.g. "汽车零部件外资（欧美）150-500人"
                    const companyHref = compLink ? compLink.href : '';

                    // 解析行业信息: "行业/类型/性质/规模"
                    let companySize = '';
                    let companyType = '';
                    let industryType1 = '';
                    const sizeMatch = industryInfo.match(/(\\d+人|\\d+-\\d+人|少于\\d+人|\\d+人以上)/);
                    if (sizeMatch) companySize = sizeMatch[1];

                    // 构造 job URL
                    const jobId = sd.jobId || '';
                    const jobHref = jobId ? 'https://jobs.51job.com/all/' + jobId + '.html' : '';

                    const job = {
                        jobId: jobId,
                        jobTitle: jobNameEl ? jobNameEl.innerText.trim() : (sd.jobTitle || ''),
                        jobSalary: salEl ? salEl.innerText.trim() : (sd.jobSalary || ''),
                        jobArea: areaEl ? areaEl.innerText.trim() : (sd.jobArea || ''),
                        jobDegree: sd.jobDegree || '',
                        jobYear: sd.jobYear || '',
                        companyId: sd.companyId || '',
                        jobTime: sd.jobTime || '',
                        jobType: sd.jobType || '0',
                        isPromote: sd.isPromote === '是',
                        jobHref: jobHref,
                        companyHref: companyHref,
                        // 文本数据
                        companyName: companyName,
                        industryInfo: industryInfo,
                        companySize: companySize,
                        companyType: companyType,
                        tags: Array.from(tagEls).map(t => t.innerText.trim()).filter(Boolean),
                        // 保留 allLines 用于兼容旧解析器
                        allLines: el.innerText.split('\\n').map(l => l.trim()).filter(Boolean),
                    };

                    result.items.push(job);
                });

                // 获取总条数
                const bodyText = document.body.innerText;
                const countMatch = bodyText.match(/共\\s*(\\d+)\\s*个?条?职位/);
                if (countMatch) result.totalCount = parseInt(countMatch[1]);

                // 从分页获取页数 (兜底)
                if (!result.totalCount) {
                    let maxPage = 1;
                    document.querySelectorAll('.pagination li, [class*="pager"] li, [class*="page"] [class*="item"]').forEach(li => {
                        const n = parseInt(li.innerText);
                        if (!isNaN(n) && n > maxPage) maxPage = n;
                    });
                    if (maxPage > 1) result.totalCount = maxPage * 20;
                }

                return result;
            }
        """)

    def _fetch_page(self, session: StealthySession, keyword: str,
                    job_area: str, page_num: int,
                    fast: bool = False) -> dict | None:
        """获取单页搜索结果。返回 {"items": [...], "totalCount": N} 或 None。

        fast=True 时减少等待时间（用于第 2 页及以后，WAF 已在首页通过）。
        """
        params = {
            "keyword": keyword,
            "searchType": "2",
            "pageNum": str(page_num),
            "jobArea": job_area,
        }
        url = f"{BASE_SEARCH_URL}?{urlencode(params)}"

        # 首页需要完整等待 (WAF + Vue 初始化)，后续页可加速
        fetch_kwargs = {} if not fast else {"wait": 2000}

        result = {}

        def page_action(page):
            data = self._extract_from_dom(page)
            result["data"] = data

        for attempt in range(2 if fast else 3):
            try:
                session.fetch(url, page_action=page_action, **fetch_kwargs)
                data = result.get("data")
                if data and data.get("items"):
                    return data
                time.sleep(1 * (attempt + 1))
            except Exception:
                time.sleep(1 * (attempt + 1))
        return None

    def _click_next_page(self, page) -> str | None:
        """点击"下一页"按钮。返回 'next'/'page' 或 None（已无更多页）。"""
        return page.evaluate("""
            () => {
                const nextSels = [
                    '.pagination .next:not(.disabled)',
                    '[class*="pager"] .next:not(.disabled)',
                    '.btn-next:not(.disabled)',
                    '.ant-pagination-next:not(.ant-pagination-disabled)',
                ];
                for (const sel of nextSels) {
                    const btn = document.querySelector(sel);
                    if (btn) { btn.click(); return 'next'; }
                }
                // 回退：查找当前激活页的下一个兄弟
                const active = document.querySelector(
                    '.pagination .active, [class*="pager"] .active, [class*="page"] .active'
                );
                if (active && active.nextElementSibling) {
                    const next = active.nextElementSibling;
                    const n = parseInt(next.innerText);
                    if (!isNaN(n)) { next.click(); return 'page'; }
                }
                return null;
            }
        """)

    def _wait_for_page_change(self, page, prev_job_ids: set,
                              timeout_ms: int = 6000) -> tuple[bool, set]:
        """等待页面职位数据更新。返回 (是否变化, 新job_id集合)。"""
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            page.wait_for_timeout(600)
            data = self._extract_from_dom(page)
            new_ids = {item.get("jobId") for item in data.get("items", [])}
            if new_ids and new_ids != prev_job_ids:
                return True, new_ids
        # 超时，最后尝试一次
        data = self._extract_from_dom(page)
        new_ids = {item.get("jobId") for item in data.get("items", [])}
        return (bool(new_ids and new_ids != prev_job_ids), new_ids)

    def search_all_pages(
        self,
        session: StealthySession,
        keyword: str,
        job_area: str,
        max_pages: int = 80,
        on_page: callable = None,
    ) -> list[dict]:
        """搜索全部页面，返回所有职位列表。

        使用单次 session.fetch() + DOM 内点击"下一页"翻页，解决 URL
        导航不触发 Vue SPA 重新渲染的问题。不依赖 totalCount 文本解析。
        """
        params = {
            "keyword": keyword,
            "searchType": "2",
            "pageNum": "1",
            "jobArea": job_area,
        }
        url = f"{BASE_SEARCH_URL}?{urlencode(params)}"

        state = {"all_items": [], "current_page": 0, "stopped_at": 0}

        def page_action(page):
            data = self._extract_from_dom(page)
            items = data.get("items", [])
            if not items:
                return

            state["current_page"] = 1
            parsed = _parse_dom_jobs(items)
            state["all_items"].extend(parsed)
            if on_page:
                on_page(1, max_pages, parsed)

            logger.info(
                "[%s][%s] 第1页: %d条 (上限%d页)",
                keyword, job_area, len(items), max_pages,
            )

            prev_ids = {item.get("jobId") for item in items}

            for p in range(2, max_pages + 1):
                clicked = self._click_next_page(page)
                if not clicked:
                    logger.info(
                        "[%s][%s] 第%d页后无更多页，停止",
                        keyword, job_area, p - 1,
                    )
                    state["stopped_at"] = p - 1
                    break

                changed, new_ids = self._wait_for_page_change(page, prev_ids)
                time.sleep(1.5)  # 避免触发 API 频率限制
                if not changed:
                    logger.info(
                        "[%s][%s] 第%d页内容无变化，已到末尾",
                        keyword, job_area, p,
                    )
                    state["stopped_at"] = p - 1
                    break

                data = self._extract_from_dom(page)
                page_items = data.get("items", [])
                if not page_items:
                    state["stopped_at"] = p - 1
                    break

                prev_ids = new_ids
                parsed = _parse_dom_jobs(page_items)
                state["all_items"].extend(parsed)
                state["current_page"] = p
                if on_page:
                    on_page(p, max_pages, parsed)

                if p % 10 == 0:
                    logger.info(
                        "[%s][%s] 翻页: %d/%d, 累计 %d 条",
                        keyword, job_area, p, max_pages, len(state["all_items"]),
                    )

            state["stopped_at"] = state["stopped_at"] or state["current_page"]

        session.fetch(url, page_action=page_action, network_idle=True, wait=5000)

        total_pages = state["stopped_at"]
        logger.info(
            "[%s][%s] 完成: %d页, %d条",
            keyword, job_area, total_pages, len(state["all_items"]),
        )
        return state["all_items"]

    def crawl_with_splitting(
        self,
        keywords: list[str],
        job_areas: list[tuple[str, str]],
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
                    time.sleep(3)  # 避免触发 API 频率限制

        return all_items


def _parse_dom_jobs(dom_items: list[dict]) -> list[dict]:
    """将从 DOM 提取的原始数据转换为与旧 parse_api_response 兼容的格式。"""
    result = []
    for raw in dom_items:
        tags = raw.get("tags", [])
        welfare_from_tags = [t for t in tags if _is_welfare_tag(t)]
        skill_tags = [t for t in tags if t not in welfare_from_tags]

        # 从 allLines 补充福利标签（有些福利标签不在 .tag 元素中）
        lines = raw.get("allLines", [])
        welfare_lines = [l for l in lines if _is_welfare_tag(l)]
        all_welfare = list(dict.fromkeys(welfare_from_tags + welfare_lines))  # 去重保序

        item = {
            "jobId": raw.get("jobId", ""),
            "jobName": raw.get("jobTitle", ""),
            "jobType": raw.get("jobType", "0"),
            "companyId": raw.get("companyId", ""),
            "isPromotion": raw.get("isPromote", False),
            "companyName": raw.get("companyName", ""),
            "fullCompanyName": raw.get("companyName", ""),
            # 从 DOM 提取的结构化字段
            "provideSalaryString": raw.get("jobSalary", ""),
            "jobAreaString": raw.get("jobArea", ""),
            "workArea": raw.get("jobArea", ""),
            "degreeString": raw.get("jobDegree", ""),
            "workYearString": raw.get("jobYear", ""),
            "issueDateString": raw.get("jobTime", ""),
            "jobHref": raw.get("jobHref", ""),
            "companyHref": raw.get("companyHref", ""),
            "companySize": raw.get("companySize", ""),
            "industryType1Str": raw.get("industryInfo", ""),
            "industryType2Str": "",
            "companyTypeString": "",
            "companyIndustryType1Str": "",
            "companyIndustryType2Str": "",
            # 兼容旧格式
            "salary": raw.get("jobSalary", ""),
            "degree": raw.get("jobDegree", ""),
            "workYear": raw.get("jobYear", ""),
            "issueDate": raw.get("jobTime", ""),
            "updateDate": raw.get("jobTime", ""),
            "jobTags": skill_tags,
            "jobTagsList": [{"jobTagName": t} for t in skill_tags],
            "welfareList": [{"chineseTitle": w, "typeTitle": w} for w in all_welfare],
            "jobDescribe": "",
            "hrName": "",
            "lon": "",
            "lat": "",
            "isIntern": False,
        }
        result.append(item)
    return result


# 保留旧方法名以兼容现有代码
SearchAPIClient.search_one_page = SearchAPIClient._fetch_page
