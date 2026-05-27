"""诊断: 测试导航后 pageNum 是否真的改变"""
import json, logging, sys, io
from urllib.parse import urlencode
from scrapling.fetchers import StealthySession

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
logging.getLogger("scrapling").setLevel(logging.WARNING)

params = {'keyword': '机械工程师', 'searchType': '2', 'pageNum': '1', 'jobArea': '010000'}

def page_action(page):
    """手动导航并检查"""
    # 第1页数据
    url1 = page.url
    data1 = page.evaluate("""() => {
        const items = document.querySelectorAll('.jname');
        return {url: window.location.href, pageNum: new URLSearchParams(window.location.search).get('pageNum'), firstJobs: Array.from(items).slice(0,3).map(e => e.innerText)};
    }""")
    print(f"\n[当前URL] {url1}")
    print(f"[页面状态] {json.dumps(data1, ensure_ascii=False)}")

    # 手动导航到第5页
    print("\n>>> 导航到 pageNum=5 ...")
    page.goto(f"https://we.51job.com/pc/search?{urlencode({'keyword': '机械工程师', 'searchType': '2', 'pageNum': '5', 'jobArea': '010000'})}")
    page.wait_for_timeout(3000)

    data5 = page.evaluate("""() => {
        const items = document.querySelectorAll('.jname');
        return {url: window.location.href, pageNum: new URLSearchParams(window.location.search).get('pageNum'), firstJobs: Array.from(items).slice(0,3).map(e => e.innerText)};
    }""")
    print(f"[页面状态] {json.dumps(data5, ensure_ascii=False)}")

    # 检查 API 请求
    api_calls = page.evaluate("""() => {
        return performance.getEntriesByType('resource')
            .filter(e => e.name.includes('search-pc'))
            .map(e => ({url: e.name, pageNum: new URLSearchParams(e.name.split('?')[1] || '').get('pageNum')}));
    }""")
    print(f"\n[API 请求] {json.dumps(api_calls, ensure_ascii=False)}")

    # 尝试点击翻页按钮
    print("\n>>> 尝试点击第2页...")
    clicked = page.evaluate("""() => {
        // 找分页中的第2页
        const pageLinks = document.querySelectorAll('.pagination li, [class*="pager"] li, [class*="page"] li');
        for (const li of pageLinks) {
            if (li.innerText.trim() === '2') {
                li.click();
                return 'clicked page 2';
            }
        }
        // 尝试其他选择器
        const all = document.querySelectorAll('[class*="page"]');
        return 'found ' + all.length + ' elements, but no page 2 button';
    }""")
    print(f"[点击结果] {clicked}")

    page.wait_for_timeout(3000)

    data_click = page.evaluate("""() => {
        const items = document.querySelectorAll('.jname');
        return {url: window.location.href, firstJobs: Array.from(items).slice(0,3).map(e => e.innerText), totalItems: items.length};
    }""")
    print(f"[点击后状态] {json.dumps(data_click, ensure_ascii=False)}")

url = f"https://we.51job.com/pc/search?{urlencode(params)}"
with StealthySession(
    headless=True, solve_cloudflare=True, real_chrome=True,
    network_idle=True, wait=5000,
    google_search=False, hide_canvas=True, block_webrtc=True,
) as s:
    s.fetch(url, page_action=page_action)
