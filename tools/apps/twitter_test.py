"""基于 CloakBrowser 的 Twitter/X 账号登录。

通过住宅代理登录 Twitter，使用持久化 profile 保持跨次运行的登录态，
供搜索提取等后续脚本复用。

用法:
    # 有头模式（默认）:
    python tools/twitter_test.py \
        --proxy "http://user:pass@proxy_ip:port" \
        --profile ./twitter-profiles/korea_account

    # 无头模式:
    python tools/twitter_test.py \
        --proxy "http://user:pass@proxy_ip:port" \
        --profile ./twitter-profiles/korea_account \
        --headless
"""

import os
import sys
import time
from pathlib import Path

from cloakbrowser import launch_persistent_context

# 启动时加载 tools/.env
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _env import load_env  # noqa: E402
load_env()

# ---- 命令行参数解析 ----

HEADLESS = "--headless" in sys.argv  # 默认有头，传 --headless 切换到无头
PROXY = None
PROFILE = "./twitter-profile"
TWITTER_EMAIL = None
TWITTER_USERNAME = None
TWITTER_PASSWORD = None

for i, arg in enumerate(sys.argv):
    if arg == "--proxy" and i + 1 < len(sys.argv):
        PROXY = sys.argv[i + 1]
    elif arg == "--profile" and i + 1 < len(sys.argv):
        PROFILE = sys.argv[i + 1]
    elif arg == "--email" and i + 1 < len(sys.argv):
        TWITTER_EMAIL = sys.argv[i + 1]
    elif arg == "--username" and i + 1 < len(sys.argv):
        TWITTER_USERNAME = sys.argv[i + 1]
    elif arg == "--password" and i + 1 < len(sys.argv):
        TWITTER_PASSWORD = sys.argv[i + 1]


# ---- 辅助工具 ----

def rand(low: float, high: float) -> float:
    import random
    return random.uniform(low, high)


# ---- 浏览器指纹信息 ----

def print_browser_info(page):
    """输出浏览器指纹和出口 IP 信息，用于验证隐身效果。"""
    import re

    info = page.evaluate("""async () => {
        const ua = navigator.userAgent;
        let fullVersion = null;
        try {
            const data = await navigator.userAgentData.getHighEntropyValues(
                ['fullVersionList', 'platform', 'platformVersion']
            );
            const chrome = data.fullVersionList.find(
                b => b.brand === 'Chromium' || b.brand === 'Google Chrome'
            );
            fullVersion = chrome ? chrome.version : null;
        } catch {}
        const gl = document.createElement('canvas').getContext('webgl');
        const dbg = gl ? gl.getExtension('WEBGL_debug_renderer_info') : null;
        return {
            ua,
            fullVersion,
            platform: navigator.platform,
            cores: navigator.hardwareConcurrency,
            gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'N/A',
            gpuVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : 'N/A',
            screen: screen.width + 'x' + screen.height,
            languages: navigator.languages.join(', '),
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            webdriver: navigator.webdriver,
        };
    }""")

    print(f"  模式: {'无头' if HEADLESS else '有头'}")
    ua_short = re.sub(r'^Mozilla/5\.0 \(', '', info["ua"])
    print(f"  UA: {ua_short}")
    print(f"  平台: {info['platform']} | 核心数: {info['cores']}")
    print(f"  GPU: {info['gpuVendor']} — {info['gpu']}")
    print(f"  屏幕: {info['screen']} | 时区: {info['timezone']}")
    print(f"  语言: {info['languages']}")
    print(f"  webdriver: {info['webdriver']}")

    # 验证出口 IP
    try:
        page.goto("https://httpbin.org/ip", timeout=15000)
        ip = page.evaluate("JSON.parse(document.body.innerText).origin")
        print(f"  出口IP: {ip}")
    except Exception:
        print("  出口IP: 无法检测")


# ---- 登录逻辑 ----

def test_twitter_login(page):
    """尝试登录 Twitter/X。

    支持三种模式:
    1. 已登录（从 profile 恢复了 session）→ 直接返回成功
    2. 提供了凭据 → 逐字符模拟真人打字自动登录
    3. 无凭据 → 打开浏览器窗口让用户手动登录

    输入方式采用 ``page.keyboard.type()``，CloakBrowser 的 humanize 层
    会拦截并逐字符输入（带随机间隔、偶尔打错修正），而非瞬间注入。
    """
    print("\n--- Twitter/X 登录 ---")

    page.goto("https://x.com", wait_until="domcontentloaded", timeout=30000)
    time.sleep(3)

    # 检测当前是否已登录
    logged_in = page.evaluate("""() => {
        const hasTimeline = !!document.querySelector('[data-testid="primaryColumn"]');
        const hasLoginButton = document.body.innerText.includes('Sign in') ||
                               document.body.innerText.includes('Log in');
        const hasSignUp = document.body.innerText.includes('Create account');
        return hasTimeline && !hasLoginButton && !hasSignUp;
    }""")

    if logged_in:
        print("  已登录 — 从 profile 恢复了会话。")
        return True

    print("  未登录。")

    if not TWITTER_EMAIL and not TWITTER_USERNAME:
        print("  未提供凭据。请用 --email/--username/--password 传参。")
        return False

    if not TWITTER_PASSWORD:
        print("  未提供密码。请用 --password 传参。")
        return False

    # ---- 自动登录流程 ----
    login_handle = TWITTER_USERNAME or TWITTER_EMAIL
    print(f"  自动登录，用户名: {login_handle}")

    try:
        # 直接跳转到登录页面
        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        # 步骤1: 逐字符输入用户名（humanize 层接管，模拟真人打字节奏）
        page.locator('input[name="username_or_email"]').first.click()
        time.sleep(rand(200, 600) / 1000)
        page.keyboard.type(login_handle)
        print("  ⌨️  已逐字符输入用户名")
        time.sleep(rand(300, 800) / 1000)

        # 步骤2: 按 Enter 提交
        page.keyboard.press("Enter")
        time.sleep(5)

        # 步骤3: 检查是否跳转到密码页，或者触发了额外验证
        body_text = page.evaluate("() => (document.body?.innerText || '')")

        # Twitter 有时在邮箱后还会要求输入用户名
        pwd_visible = page.locator('input[name="password"]').first.is_visible()
        if not pwd_visible:
            # 可能还在用户名阶段
            try:
                username_still_there = page.locator(
                    'input[name="username_or_email"]').first.is_visible(timeout=5000)
                if username_still_there and TWITTER_USERNAME:
                    page.locator('input[name="username_or_email"]').first.click()
                    time.sleep(rand(200, 500) / 1000)
                    page.keyboard.type(TWITTER_USERNAME)
                    print("  ⌨️  已逐字符输入二次用户名")
                    time.sleep(rand(300, 800) / 1000)
                    page.keyboard.press("Enter")
                    time.sleep(5)
            except Exception:
                pass

        # 步骤4: 逐字符输入密码
        page.locator('input[name="password"]').first.click()
        time.sleep(rand(300, 800) / 1000)
        page.keyboard.type(TWITTER_PASSWORD)
        print("  ⌨️  已逐字符输入密码")
        time.sleep(rand(300, 800) / 1000)
        page.keyboard.press("Enter")
        time.sleep(5)

        # ---- 验证登录结果 ----
        body_text = page.evaluate("() => (document.body?.innerText || '')")

        if "两步验证" in body_text or "Verification code" in body_text or "验证码" in body_text:
            print("  ⚠️ 需要两步验证！请在浏览器窗口中手动输入验证码。")
            print("  等待中，完成后自动检测...")
            for _ in range(40):
                time.sleep(3)
                logged = page.evaluate(
                    "!!document.querySelector('[data-testid=\"primaryColumn\"]')")
                if logged:
                    print("  ✅ 登录成功！")
                    return True
            print("  ⏰ 2FA 等待超时，请手动检查浏览器。")
            return False

        if "密码不正确" in body_text or "Wrong password" in body_text:
            print("  ❌ 密码错误。")
            return False

        if "被锁定" in body_text or "locked" in body_text:
            print("  ❌ 账号被锁定。")
            return False

        # 最后检查时间线
        logged_in = page.evaluate(
            "!!document.querySelector('[data-testid=\"primaryColumn\"]')")

        if logged_in:
            print("  ✅ 登录成功！")
            return True
        else:
            print("  ⚠️ 登录状态未知，请检查浏览器窗口。")
            return False

    except Exception as e:
        print(f"  自动登录异常: {e}")
        return False


# ---- 主流程 ----

def main():
    profile_path = Path(PROFILE).resolve()

    print("=" * 60)
    print("CloakBrowser — Twitter/X 账号登录")
    print("=" * 60)
    print(f"Profile: {profile_path}")
    print(f"代理: {'已配置' if PROXY else '无'}")
    print()

    # 1. 启动隐身浏览器
    # WebRTC IP 从环境变量读取(应与代理出口 IP 一致),未设置则不注入
    webrtc_ip = os.environ.get("CB_WEBRTC_IP")
    extra_args = [f"--fingerprint-webrtc-ip={webrtc_ip}"] if (PROXY and webrtc_ip) else None

    print("正在启动隐身浏览器...")
    ctx = launch_persistent_context(
        str(profile_path),
        headless=HEADLESS,
        proxy=PROXY,
        humanize=True,
        human_preset="careful",
        timezone="Asia/Seoul",
        locale="ko-KR",
        color_scheme="light",
        viewport=None,  # 使用系统窗口尺寸，指纹更自然
        args=extra_args,
    )

    page = ctx.new_page()

    try:
        # 2. 输出指纹信息
        print_browser_info(page)

        # 3. 登录 Twitter
        login_success = test_twitter_login(page)

        if not login_success and HEADLESS:
            print("\n  [提示] 无头模式下无法手动登录。")
            print("  请先去掉 --headless 参数，用有头模式跑一次完成登录。")
            return 1

        if not login_success and not HEADLESS:
            print("\n" + "=" * 60)
            print("  自动登录失败，浏览器窗口已打开。")
            print("  请手动完成登录，登录态将持久化到 profile。")
            print("  完成后关闭浏览器窗口即可。")
            print("=" * 60)

        # 4. 截图留存
        page.screenshot(path="twitter_test_final.png")
        print("\n  截图已保存: twitter_test_final.png")

    except Exception as e:
        print(f"\n  错误: {e}")
        try:
            page.screenshot(path="twitter_test_error.png")
            print("  错误截图已保存: twitter_test_error.png")
        except Exception:
            pass
        return 1

    finally:
        ctx.close()

    print("\n" + "=" * 60)
    print("  测试完成。Profile 已保存到:", profile_path)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
