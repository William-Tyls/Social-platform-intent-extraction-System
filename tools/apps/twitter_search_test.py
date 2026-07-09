"""基于 CloakBrowser 的 Twitter 关键词搜索 + 推文提取。

直接运行，无需手动登录（依赖已建立好的 profile 会话）。
搜索结果以结构化 JSON 保存。

用法:
    python tools/twitter_search_test.py "搜索关键词"

    不传关键词时默认使用 "날씨"
"""

from cloakbrowser import launch_persistent_context
import os, time, random, json, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

# 启动时加载 tools/.env
_EXAMPLES = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _EXAMPLES)
sys.path.insert(0, os.path.join(_EXAMPLES, "extractors"))
from _env import load_env  # noqa: E402
load_env()

# ---- 配置 ----

# 代理从环境变量读取(避免硬编码内网地址)。示例:
#   export CB_TWITTER_PROXY=socks5://127.0.0.1:11080
PROXY = os.environ.get("CB_TWITTER_PROXY")
PROFILE = "./twitter-profiles/korea_account"
SEARCH_TERM = sys.argv[1] if len(sys.argv) > 1 else "날씨"


# ---- 主流程 ----

def main():
    # 1. 启动隐身浏览器（有头模式，从 profile 恢复登录态）
    print("正在启动浏览器...")
    browser_ctx = launch_persistent_context(
        str(PROFILE),
        headless=False,
        proxy=PROXY,
        humanize=True,
        human_preset="careful",
        timezone="Asia/Seoul",
        locale="ko-KR",
        color_scheme="dark",
        args=["--lang=ko-KR"],
    )
    page = browser_ctx.new_page()
    page.set_default_navigation_timeout(300000)
    page.set_default_timeout(120000)

    # 2. 加载 Twitter 首页，验证登录态
    print("正在加载 x.com/home...")
    t0 = time.time()
    try:
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=300000)
    except Exception as e:
        print(f"  导航警告: {e}")

    # 等待 React SPA 挂载完成
    for i in range(12):
        time.sleep(5)
        title = page.title()
        has_tl = page.evaluate("!!document.querySelector('[data-testid=\"primaryColumn\"]')")
        print(f"  {i*5}s: 标题='{title[:60]}', 时间线已加载={has_tl}")
        if has_tl and title:
            break

    url = page.url
    title = page.title()
    print(f"当前URL: {url}")
    print(f"页面标题: {title}")

    # 检测登录状态
    logged_in = page.evaluate("""() => {
        const hasTimeline = !!document.querySelector('[data-testid="primaryColumn"]');
        const noSignIn = !document.body?.innerText?.includes('Sign in');
        return hasTimeline && noSignIn;
    }""")
    print(f"登录状态: {logged_in}")

    # 如果未登录，尝试导航到登录页并截图，然后退出
    if not logged_in:
        print("未登录。需要重新认证。")
        page.screenshot(path="twitter_not_logged_in.png")
        print("正在跳转到登录页...")
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")
        time.sleep(5)
        title = page.title()
        url = page.url
        print(f"登录页URL: {url}, 标题: {title}")

        # 输出登录页内容，便于排查
        body = page.evaluate("(document.body?.innerText || '')")
        print(f"登录页内容预览: {body[:300]}")
        page.screenshot(path="twitter_login_page.png")
        browser_ctx.close()
        return

    # 3. 轻度滚动，模拟自然浏览
    for i in range(2):
        page.evaluate(f"window.scrollBy(0, {random.randint(200, 400)})")
        time.sleep(random.uniform(2, 3))

    # 4. 执行关键词搜索
    print(f"\n正在搜索: {SEARCH_TERM}")
    search_url = f"https://x.com/search?q={quote(SEARCH_TERM)}&src=typed_query&f=live"
    page.goto(search_url, wait_until="domcontentloaded")

    # 等待搜索结果渲染
    print("等待搜索结果渲染...")
    for i in range(12):
        time.sleep(3)
        has_tweets = page.evaluate("!!document.querySelector('article[data-testid=\"tweet\"]')")
        if has_tweets:
            print(f"  结果已加载 (耗时 {i*3}s)")
            break
        if i > 0 and i % 4 == 0:
            print(f"  仍在等待... ({i*3}s)")
    time.sleep(5)

    # 5. 提取推文数据(公共模块,与 console.py 共用)
    from twitter_extractor import extract_tweets
    tweets = extract_tweets(page)[:10]  # 与原逻辑一致:最多取 10 条

    # 6. 控制台输出结果摘要
    print(f"\n=== 关键词 '{SEARCH_TERM}' 共 {len(tweets)} 条推文 ===")
    for i, t in enumerate(tweets[:10]):
        print(f"\n[{i+1}] @{t['author_handle']} ({t['author_name']})")
        print(f"    {t['tweet_text'][:250]}")
        print(f"    {t['timestamp']} | 赞:{t['likes']} 转:{t['retweets']} 评:{t['replies']}")

    # 7. 截图留存
    page.screenshot(path="twitter_search_final.png")

    # 8. 保存为结构化 JSON 文件
    record = {
        "search_term": SEARCH_TERM,
        "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(tweets),
        "tweets": tweets,
    }
    path = f"twitter_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    Path(path).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n已保存: {path}")
    browser_ctx.close()
    print("完成!")


if __name__ == "__main__":
    main()
