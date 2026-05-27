"""测试：点击"下一页"翻页，自动检测末尾"""
import json, logging, sys, io, time
from urllib.parse import urlencode
from scrapling.fetchers import StealthySession

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S", stream=sys.stdout)
logging.getLogger("scrapling").setLevel(logging.WARNING)

params = {'keyword': '机械工程师', 'searchType': '2', 'pageNum': '1', 'jobArea': '010000'}

def extract_from_dom(page):
    return page.evaluate("""
        () => {
            const result = {items: [], totalCount: 0};
            document.querySelectorAll('.joblist-item').forEach(el => {
                const wrapper = el.querySelector('[sensorsdata]');
                if (!wrapper) return;
                let sd = {};
                try { sd = JSON.parse(wrapper.getAttribute('sensorsdata')); } catch(e) {}
                const jobNameEl = el.querySelector('.jname');
                const salEl = el.querySelector('.sal');
                result.items.push({
                    jobId: sd.jobId || '',
                    jobTitle: jobNameEl ? jobNameEl.innerText.trim() : (sd.jobTitle || ''),
                    jobSalary: salEl ? salEl.innerText.trim() : (sd.jobSalary || ''),
                });
            });
            return result;
        }
    """)

def click_next(page):
    """点击下一页，返回是否找到并点击了按钮"""
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
            const active = document.querySelector(
                '.pagination .active, [class*="pager"] .active, [class*="page"] .active'
            );
            if (active && active.nextElementSibling) {
                const next = active.nextElementSibling;
                if (!isNaN(parseInt(next.innerText))) { next.click(); return 'page'; }
            }
            return null;
        }
    """)

state = {"page_data": [], "all_ids": set()}

def page_action(page):
    print("=" * 60)

    for p in range(1, 81):  # 最多80页
        data = extract_from_dom(page)
        items = data.get("items", [])
        ids = {it["jobId"] for it in items}
        new_count = len(ids - state["all_ids"])
        state["all_ids"].update(ids)

        print(f"第{p}页: {len(items)}条, 新增={new_count}条, 累计唯一={len(state['all_ids'])}")
        if items:
            print(f"  首条: {items[0]['jobTitle']} | {items[0]['jobSalary']}")

        if p >= 80:
            break

        # 点击下一页
        prev_ids = ids.copy()
        clicked = click_next(page)
        if not clicked:
            print(f"第{p}页后无更多页，停止!")
            break
        print(f"  点击结果: {clicked}")
        page.wait_for_timeout(2000)

        # 验证内容变化
        new_data = extract_from_dom(page)
        new_ids = {it["jobId"] for it in new_data.get("items", [])}
        if new_ids == prev_ids:
            print(f"  内容未变，等2秒再试...")
            page.wait_for_timeout(2000)
            new_data = extract_from_dom(page)
            new_ids = {it["jobId"] for it in new_data.get("items", [])}
            if new_ids == prev_ids:
                print(f"  仍未变化，已到末尾!")
                break

url = f"https://we.51job.com/pc/search?{urlencode(params)}"
print(f"加载: {url}")
with StealthySession(
    headless=True, solve_cloudflare=True, real_chrome=True,
    network_idle=True, wait=5000,
    google_search=False, hide_canvas=True, block_webrtc=True,
) as s:
    s.fetch(url, page_action=page_action)

print(f"\n最终: {len(state['all_ids'])} 个唯一 jobId")
