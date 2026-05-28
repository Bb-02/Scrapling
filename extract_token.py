"""
提取 51job 登录后的认证信息，并自动发现 cupid.51job.com API 端点。

用法:
    python extract_token.py           # 打开浏览器，手动登录，自动抓取所有 API 请求
    python extract_token.py --test    # 用已保存的 token 测试 cupid API
"""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

TOKEN_FILE = Path(__file__).parent / "cupid_token.json"
API_LOG_FILE = Path(__file__).parent / "captured_apis.json"


async def extract_token():
    """打开浏览器让用户手动登录，监听网络请求发现 API 端点."""

    # 存储捕获到的 API 请求
    captured_requests: list[dict] = []

    def _on_request(request):
        """拦截所有请求，记录 API 调用."""
        url = request.url
        # 只关注 51job 的 API 请求
        if "51job.com/api" in url or "cupid.51job.com" in url or "51job.com/open" in url:
            req_info = {
                "url": url,
                "method": request.method,
                "headers": dict(request.headers),
                "post_data": request.post_data if request.method == "POST" else None,
            }
            captured_requests.append(req_info)

    def _on_response(response):
        """记录 API 响应."""
        url = response.request.url
        # 找到对应的请求记录，补充响应信息
        for req in captured_requests:
            if req["url"] == url and "status" not in req:
                try:
                    req["status"] = response.status
                    req["content_type"] = response.headers.get("content-type", "")
                    # 只记录前 2KB 的响应体，避免文件太大
                    body = response.body()[:2048]
                    req["body_preview"] = body.decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
                break

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            channel="chrome",
        )
        context = await browser.new_context()
        page = await context.new_page()

        # 监听网络请求
        page.on("request", _on_request)
        page.on("response", _on_response)

        # 直接打开 51job 搜索页（会触发登录跳转）
        print("正在打开 51job 搜索页...")
        await page.goto("https://we.51job.com/pc/search?keyword=机械工程师", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # 检查是否需要登录（页面上是否有登录按钮）
        need_login = await page.evaluate("""() => {
            const btns = document.querySelectorAll('button, a, span, div');
            for (const el of btns) {
                if (el.textContent.includes('登录') && el.offsetParent !== null) return true;
            }
            // 也检查 URL 是否跳转到了登录页
            if (window.location.href.includes('login')) return true;
            return false;
        }""")

        if need_login:
            print("\n⚠ 需要登录！请在浏览器中点击登录按钮完成登录...")
            print("（支持扫码或账号密码登录）")
        else:
            print("\n可能已处于登录状态，继续抓取数据...")

        print("等待登录完成（最多 5 分钟）...")

        logged_in = False
        for i in range(300):
            await asyncio.sleep(1)
            cookies = await context.cookies()
            cookie_names = {c["name"] for c in cookies}

            # 检测常见登录态 cookie
            has_login_cookie = any(
                name in cookie_names
                for name in ["user-token", "guid", "acw_tc", "51job_login", "loginname"]
            )

            url = page.url
            on_main_site = "we.51job.com" in url or "my.51job.com" in url

            if has_login_cookie and on_main_site:
                logged_in = True
                print(f"✓ 检测到登录成功！当前页面: {url}")
                break

            if i % 15 == 0 and i > 0:
                print(f"  已等待 {i} 秒，请在浏览器中完成登录...")

        if not logged_in:
            # 即使没检测到登录，也可能已经登录了（cookie 名称不同）
            print("未检测到标准登录 cookie，但继续执行...")

        # 登录成功后，主动做一次搜索，触发 API 请求
        print("\n正在触发搜索以捕获 API 请求...")
        try:
            # 等页面稳定
            await asyncio.sleep(3)
            # 如果还在搜索页，尝试点搜索按钮
            await page.goto("https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
                          wait_until="domcontentloaded")
            await asyncio.sleep(5)  # 等 API 请求发出
        except Exception as e:
            print(f"触发搜索时出错: {e}")

        # 提取所有 cookies
        all_cookies = await context.cookies()
        print(f"\n=== 提取到 {len(all_cookies)} 个 cookies ===")

        token_data = {
            "cookies": {},
            "localStorage": {},
            "sessionStorage": {},
            "captured_apis": captured_requests,
        }

        for c in all_cookies:
            domain = c.get("domain", "")
            name = c.get("name", "")
            value = c.get("value", "")
            if "51job.com" in domain:
                token_data["cookies"][name] = value
                val_preview = value[:60] + "..." if len(value) > 60 else value
                print(f"  [{domain}] {name} = {val_preview}")

        # 提取 localStorage
        try:
            ls = await page.evaluate("() => JSON.stringify(localStorage)")
            token_data["localStorage"] = json.loads(ls) if ls else {}
        except Exception:
            pass

        # 提取 sessionStorage
        try:
            ss = await page.evaluate("() => JSON.stringify(sessionStorage)")
            token_data["sessionStorage"] = json.loads(ss) if ss else {}
        except Exception:
            pass

        # 保存
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(token_data, f, ensure_ascii=False, indent=2)
        print(f"\n✓ token 已保存到: {TOKEN_FILE}")

        # 打印捕获到的 API 请求
        print(f"\n=== 捕获到 {len(captured_requests)} 个 API 请求 ===")
        for i, req in enumerate(captured_requests):
            status = req.get("status", "?")
            ct = req.get("content_type", "?")
            print(f"\n  [{i+1}] {req['method']} [HTTP {status}] ({ct})")
            print(f"      URL: {req['url'][:200]}")
            if req.get("body_preview"):
                preview = req["body_preview"][:200]
                print(f"      响应预览: {preview}")

        # 特别标注 cupid 请求
        cupid_requests = [r for r in captured_requests if "cupid" in r["url"]]
        if cupid_requests:
            print(f"\n🎯 发现 {len(cupid_requests)} 个 cupid API 请求！")
            for r in cupid_requests:
                print(f"   {r['method']} {r['url']}")

        # 检查关键 cookie
        print("\n=== 关键认证信息 ===")
        for name, value in token_data["cookies"].items():
            vl = str(value)[:100]
            print(f"  {name} = {vl}")

        for key, value in token_data.get("localStorage", {}).items():
            if any(kw in key.lower() for kw in ["token", "user", "login", "auth"]):
                print(f"  localStorage[{key}] = {str(value)[:100]}")

        await browser.close()
        print("\n浏览器已关闭。")
        print("下一步: python extract_token.py --test")


async def test_cupid_api():
    """用已保存的 token 测试 cupid API."""
    if not TOKEN_FILE.exists():
        print(f"token 文件不存在: {TOKEN_FILE}")
        print("请先运行: python extract_token.py")
        return

    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        token_data = json.load(f)

    print("已加载 token 数据")
    print(f"Cookies: {list(token_data['cookies'].keys())}")
    print(f"捕获到 {len(token_data.get('captured_apis', []))} 个 API 请求")

    # 构建 cookie 字符串
    cookie_str = "; ".join(f"{k}={v}" for k, v in token_data["cookies"].items())

    import requests

    # 优先用捕获到的 cupid API 格式
    cupid_apis = [r for r in token_data.get("captured_apis", []) if "cupid" in r.get("url", "")]

    if cupid_apis:
        print(f"\n使用捕获到的 cupid API 端点和参数进行测试...")

    # 测试端点列表
    tests = [
        # 尝试 we.51job.com 的 API（需要 HMAC 签名和 cookie）
        {
            "url": "https://we.51job.com/api/job/search-pc",
            "params": {
                "api_key": "51job",
                "keyword": "机械工程师",
                "searchType": "2",
                "sortType": "0",
                "jobArea": "000000",
                "pageNum": "1",
                "pageSize": "20",
                "timestamp": "1716336000",
            },
            "headers": {
                "Cookie": cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        },
        # 尝试 cupid 端点
        {
            "url": "https://cupid.51job.com/open/noauth/search-pc",
            "params": {
                "api_key": "51job",
                "keyword": "机械工程师",
                "searchType": "2",
                "sortType": "0",
                "jobArea": "000000",
                "pageNum": "1",
                "pageSize": "20",
            },
            "headers": {
                "Cookie": cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        },
    ]

    for i, test in enumerate(tests):
        print(f"\n--- 测试 {i+1}: {test['url']} ---")
        try:
            resp = requests.get(test["url"], params=test["params"], headers=test["headers"], timeout=15)
            print(f"HTTP {resp.status_code}, Content-Type: {resp.headers.get('Content-Type', '?')}")
            body_preview = resp.text[:800]
            print(f"响应: {body_preview}")

            if "aliyun_waf" in resp.text.lower() or "aliyun_waf_aa" in resp.text:
                print("  ✗ 被阿里云 WAF 拦截！")
            elif resp.status_code == 200 and "application/json" in resp.headers.get("Content-Type", ""):
                try:
                    data = resp.json()
                    items = data.get("resultbody", {}).get("job", {}).get("items", [])
                    if items:
                        print(f"  ✓ 成功！获取到 {len(items)} 条职位")
                        print(f"  第一条: {items[0].get('jobName', '?')}")
                    else:
                        print(f"  ✓ JSON 但无数据: {json.dumps(data, ensure_ascii=False)[:300]}")
                except Exception:
                    print(f"  JSON 解析失败")
            else:
                print(f"  非 JSON 或异常状态码")
        except Exception as e:
            print(f"  请求失败: {e}")


if __name__ == "__main__":
    if "--test" in sys.argv:
        asyncio.run(test_cupid_api())
    else:
        asyncio.run(extract_token())
