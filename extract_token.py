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

        # 打开 51job 登录页
        print("正在打开 51job 登录页...")
        await page.goto("https://login.51job.com/", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        print("\n" + "=" * 60)
        print("  请在浏览器中完成登录（扫码或账号密码均可）")
        print("  登录成功后，在此终端按 Enter 继续...")
        print("=" * 60)
        input()

        # 登录后会跳转到 my.51job.com 或 we.51job.com/pc/my/myjob，先等稳定
        await asyncio.sleep(3)
        print(f"当前页面: {page.url}")

        # 导航到搜索页，触发 API 请求
        print("正在打开搜索页以触发 API 请求...")
        try:
            await page.goto(
                "https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
                wait_until="domcontentloaded",
                timeout=15000,
            )
        except Exception:
            # 如果又被跳转，说明已经在搜索页或者有其他重定向，忽略
            print(f"（导航被打断，当前页: {page.url}）")
        await asyncio.sleep(6)  # 等 Vue 渲染完成，API 请求发出

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


def _build_hmac_signature(path_and_query: str) -> str:
    """计算 51job API 的 HMAC-SHA256 签名."""
    import hashlib
    import hmac
    import time

    HMAC_KEY = b"abfc8f9dcf8c3f3d8aa294ac5f2cf2cc7767e5592590f39c3f503271dd68562b"
    message = path_and_query.encode("utf-8")
    sig = hmac.new(HMAC_KEY, message, hashlib.sha256).hexdigest()
    return sig


async def test_cupid_api():
    """用已保存的 token 测试搜索 API（带 HMAC 签名 + 登录态 cookie）."""
    if not TOKEN_FILE.exists():
        print(f"token 文件不存在: {TOKEN_FILE}")
        print("请先运行: python extract_token.py")
        return

    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        token_data = json.load(f)

    print("已加载 token 数据")
    print(f"Cookies: {list(token_data['cookies'].keys())}")

    import time
    import requests

    cookie_str = "; ".join(f"{k}={v}" for k, v in token_data["cookies"].items())

    # 从捕获的请求中提取真实搜索 API 的完整参数（第20/25号请求）
    print("\n=== 从捕获数据中提取搜索 API 请求格式 ===")
    search_reqs = [r for r in token_data.get("captured_apis", [])
                   if "search-pc" in r.get("url", "") and "we.51job.com" in r.get("url", "")]
    if search_reqs:
        print(f"找到 {len(search_reqs)} 个搜索 API 请求")
        print(f"URL样例: {search_reqs[0]['url'][:300]}")
        print(f"Headers: {dict(search_reqs[0].get('headers', {}))}")

    # 测试1: 不带签名，只带 cookie
    print("\n" + "=" * 60)
    print("测试1: 带 cookie 但不带 HMAC 签名")
    print("=" * 60)
    params1 = {
        "keyword": "机械工程师",
        "searchType": "2",
        "sortType": "0",
        "jobArea": "000000",
        "pageNum": "1",
        "pageSize": "20",
    }
    try:
        resp = requests.get(
            "https://we.51job.com/api/job/search-pc",
            params=params1,
            headers={
                "Cookie": cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=15,
        )
        print(f"HTTP {resp.status_code}, Content-Type: {resp.headers.get('Content-Type', '?')}")
        if "aliyun_waf" in resp.text.lower():
            print("✗ 被阿里云 WAF 拦截（不带签名不行）")
        elif "application/json" in resp.headers.get("Content-Type", ""):
            print(f"✓ JSON: {resp.text[:300]}")
        else:
            print(f"响应: {resp.text[:300]}")
    except Exception as e:
        print(f"请求失败: {e}")

    # 测试2: 带 HMAC 签名 + cookie
    print("\n" + "=" * 60)
    print("测试2: 带 HMAC 签名 + 登录态 cookie")
    print("=" * 60)
    import urllib.parse
    from urllib.parse import urlencode

    timestamp = str(int(time.time() * 1000))
    params2 = {
        "api_key": "51job",
        "keyword": "机械工程师",
        "searchType": "2",
        "sortType": "0",
        "jobArea": "000000",
        "pageNum": "1",
        "pageSize": "20",
        "timestamp": timestamp,
    }
    # 构建签名: /api/job/search-pc?{urlencoded_params}
    query_string = urlencode(params2)
    path_and_query = f"/api/job/search-pc?{query_string}"
    sig = _build_hmac_signature(path_and_query)
    url_with_sig = f"https://we.51job.com{path_and_query}&signature={sig}"
    print(f"签名: {sig[:40]}...")
    try:
        resp = requests.get(
            url_with_sig,
            headers={
                "Cookie": cookie_str,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            timeout=15,
        )
        print(f"HTTP {resp.status_code}, Content-Type: {resp.headers.get('Content-Type', '?')}")
        if "aliyun_waf" in resp.text.lower():
            print("✗ 被阿里云 WAF 拦截（HMAC 签名也没绕过）")
        elif resp.status_code == 200 and "application/json" in resp.headers.get("Content-Type", ""):
            data = resp.json()
            items = data.get("resultbody", {}).get("job", {}).get("items", [])
            if items:
                print(f"✓ 成功！获取到 {len(items)} 条职位")
                print(f"  第一条: {items[0].get('jobName', '?')} — {items[0].get('fullCompanyName', '?')}")
            else:
                print(f"JSON: {json.dumps(data, ensure_ascii=False)[:400]}")
        else:
            print(f"响应: {resp.text[:400]}")
    except Exception as e:
        print(f"请求失败: {e}")

    # 测试3: 完全模拟浏览器的请求 — 10位秒级时间戳 + user-token header + Referer
    print("\n" + "=" * 60)
    print("测试3: 完全模拟浏览器请求头")
    print("=" * 60)

    import urllib.parse
    from urllib.parse import urlencode

    ts = str(int(time.time()))  # 10位秒级时间戳，和浏览器一致
    user_token = token_data.get("localStorage", {}).get("token", "")
    print(f"user-token: {user_token}")
    print(f"timestamp: {ts}")

    # 通用请求头（模拟浏览器）
    common_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "user-token": user_token,
        "Referer": "https://we.51job.com/pc/search?keyword=机械工程师&jobArea=000000",
        "Cookie": cookie_str,
    }

    # 完全模拟浏览器的 we.51job.com 搜索请求
    print("\n--- 测试3A: we.51job.com 搜索API（完全模拟浏览器） ---")
    params3a = {
        "api_key": "51job",
        "timestamp": ts,
        "keyword": "机械工程师",
        "searchType": "2",
        "function": "",
        "industry": "",
        "jobArea": "000000",
        "jobArea2": "",
        "landmark": "",
        "metro": "",
        "salary": "",
        "workYear": "",
        "degree": "",
        "companyType": "",
        "companySize": "",
        "jobType": "",
        "issueDate": "",
        "sortType": "0",
        "pageNum": "1",
        "pageSize": "20",
    }
    # HMAC 签名
    qs = urlencode(params3a)
    sig = _build_hmac_signature(f"/api/job/search-pc?{qs}")
    full_url = f"https://we.51job.com/api/job/search-pc?{qs}&signature={sig}"
    try:
        resp = requests.get(full_url, headers=common_headers, timeout=15)
        ct = resp.headers.get("Content-Type", "")
        print(f"HTTP {resp.status_code} ({ct})")
        if "aliyun_waf" in resp.text.lower():
            print("✗ WAF 拦截（TLS 指纹检测，纯 HTTP 请求无法绕过）")
        elif "application/json" in ct:
            data = resp.json()
            items = data.get("resultbody", {}).get("job", {}).get("items", [])
            print(f"✓ 成功！{len(items)} 条职位")
            if items:
                print(f"  第一条: {items[0].get('jobName', '?')} — {items[0].get('fullCompanyName', '?')}")
        else:
            print(f"响应: {resp.text[:400]}")
    except Exception as e:
        print(f"失败: {e}")

    # 测试3B: cupid 搜索端点 — 完全模拟浏览器，但不加 HMAC
    print("\n--- 测试3B: cupid.51job.com 搜索端点（模拟浏览器，无签名） ---")
    cupid_urls = [
        "https://cupid.51job.com/open/noauth/job/search-pc",
        "https://cupid.51job.com/api/job/search-pc",
    ]
    for url in cupid_urls:
        print(f"\n尝试: {url}")
        params = {"api_key": "51job", "timestamp": ts, "keyword": "机械工程师",
                   "jobArea": "000000", "pageNum": "1", "pageSize": "20"}
        try:
            qs = urlencode(params)
            resp = requests.get(f"{url}?{qs}", headers=common_headers, timeout=15)
            ct = resp.headers.get("Content-Type", "")
            print(f"HTTP {resp.status_code} ({ct}): {resp.text[:400]}")
        except Exception as e:
            print(f"失败: {e}")


if __name__ == "__main__":
    if "--test" in sys.argv:
        asyncio.run(test_cupid_api())
    else:
        asyncio.run(extract_token())
