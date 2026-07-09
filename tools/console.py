"""CloakBrowser 多平台数据采集控制台。

基于 tkinter 的本地 GUI，提供:
  - 账号/代理/指纹 下拉切换
  - 实时指纹信息面板
  - 运行日志流
  - 提取结果表格
  - 一键导出 JSON / LLM 过滤

用法:
    python tools/console.py
"""

from __future__ import annotations

import json
import os
import queue
import random
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk
from tkinter import messagebox, filedialog, simpledialog
import tkinter as tk
from urllib.parse import quote
from typing import Optional

# 确保可以导入 tools/ 根目录和 extractors/ 子目录的模块
_EXAMPLES_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _EXAMPLES_DIR)
sys.path.insert(0, os.path.join(_EXAMPLES_DIR, "extractors"))

# Twitter 提取模块(与 console.py 解耦)
from twitter_extractor import extract_tweets

# 启动时加载 tools/.env(集中配置凭据,无需每次 export)
from _env import load_env; load_env()

# YouTube 模块(按需导入,避免未安装时启动报错)
_youtube_available = False
try:
    from youtube_ytdlp import YtDlpExtractor
    from youtube_api import YouTubeAPI, get_quota_remaining
    from youtube_dedup import DedupStore
    _youtube_available = True
except ImportError:
    pass

# Reddit 提取模块(与 console.py 解耦,可独立复用)
from reddit_extractor import (
    build_search_url, extract_posts, extract_comments,
    extract_profile, find_next_page_url,
)

# LLM 过滤共享模块
from _llm import (
    build_classify_prompt, build_comment_batch_prompt,
    build_unified_batch_prompt,
    normalize_label, call_deepseek, parse_comment_labels,
)
from _normalize import normalize_batch  # 跨平台统一 schema

# ---- 路径 ----

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# ---- 配置管理 ----

def load_config() -> dict:
    """加载配置文件。"""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {"accounts": [], "proxies": [], "fingerprints": [], "search": {"default_term": "", "max_tweets": 10}}


def save_config(cfg: dict) -> None:
    """保存配置文件。"""
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


# ---- 工作线程 ----

class TwitterWorker(threading.Thread):
    """在后台线程中运行浏览器自动化的核心逻辑。

    通过 queue.Queue 向 UI 线程发送消息:
      {"type": "log", "text": str, "level": "info"|"warn"|"error"}
      {"type": "fingerprint", "data": dict}
      {"type": "login_status", "logged_in": bool}
      {"type": "result", "tweets": list}
      {"type": "done", "success": bool}
    """

    def __init__(self, account: dict, proxy_info: dict, fp: dict,
                 search_term: str, max_tweets: int, headless: bool,
                 extraction_options: dict,
                 msg_queue: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.account = account
        self.proxy_info = proxy_info
        self.fp = fp
        self.search_term = search_term
        self.max_tweets = max_tweets
        self.headless = headless
        self.extraction_options = extraction_options
        self.q = msg_queue
        self.stop = stop_event

    def log(self, text: str, level: str = "info", progress: int | None = None):
        self.q.put({"type": "log", "text": text, "level": level, "progress": progress})

    @staticmethod
    def _rand(low: float, high: float) -> float:
        return random.uniform(low, high)

    def run(self) -> None:
        try:
            self._run()
        except Exception as e:
            self.log(f"未预期的错误: {e}\n{traceback.format_exc()}", "error")
            self.q.put({"type": "done", "success": False})

    def _run(self) -> None:
        from cloakbrowser import launch_persistent_context

        # ---- 准备参数 ----
        profile = self.account.get("profile_path", f"./twitter-profiles/{self.account['name']}")
        proxy_server = self.proxy_info.get("server") if self.proxy_info else None

        self.log("正在启动隐身浏览器...")
        self.log(f"  账号: {self.account['name']}")
        self.log(f"  代理: {self.proxy_info.get('name', '直连') if self.proxy_info else '直连'}")
        self.log(f"  指纹: {self.fp.get('name', '默认')}")
        self.log(f"  Profile: {profile}")
        self.log(f"  模式: {'无头' if self.headless else '有头'}")

        # 解析 viewport
        vp_w = self.fp.get("viewport_width")
        vp_h = self.fp.get("viewport_height")
        try:
            vp_w = int(vp_w) if vp_w else 0
            vp_h = int(vp_h) if vp_h else 0
        except (ValueError, TypeError):
            vp_w, vp_h = 0, 0
        viewport = {"width": vp_w, "height": vp_h} if (vp_w > 0 and vp_h > 0) else None
        self.log(f"  窗口: {vp_w}x{vp_h}" if viewport else "  窗口: 系统默认")

        # 解析 WebRTC IP
        extra_args = []
        webrtc_ip = self.fp.get("webrtc_ip")
        if webrtc_ip and str(webrtc_ip).strip() and str(webrtc_ip).lower() not in ("none", "null", "false"):
            extra_args.append(f"--fingerprint-webrtc-ip={webrtc_ip}")
            self.log(f"  WebRTC IP: {webrtc_ip}")

        if self.stop.is_set():
            return

        # ---- 启动浏览器 ----
        try:
            ctx = launch_persistent_context(
                str(profile),
                headless=self.headless,
                proxy=proxy_server,
                humanize=True,
                human_preset="careful",
                timezone=self.fp.get("timezone", "Asia/Seoul"),
                locale=self.fp.get("locale", "ko-KR"),
                color_scheme=self.fp.get("color_scheme", "dark"),
                viewport=viewport,
                args=extra_args if extra_args else None,
            )
        except Exception as e:
            self.log(f"浏览器启动失败: {e}", "error")
            self.q.put({"type": "done", "success": False})
            return

        page = ctx.new_page()
        page.set_default_navigation_timeout(300000)
        page.set_default_timeout(120000)

        try:
            # ---- 采集指纹信息 ----
            self.log("正在采集指纹信息...")
            try:
                fp_data = self._extract_fingerprint(page)
                self.q.put({"type": "fingerprint", "data": fp_data})
                for k, v in fp_data.items():
                    self.log(f"  {k}: {v}")
            except Exception as e:
                self.log(f"指纹采集失败: {e}", "warn")

            if self.stop.is_set():
                return

            # ---- 导航到 Twitter ----
            self.log("正在加载 Twitter 首页...")
            try:
                page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=300000)
            except Exception as e:
                self.log(f"导航警告: {e}", "warn")

            # 等待 React SPA 挂载
            for i in range(12):
                if self.stop.is_set():
                    return
                time.sleep(5)
                has_tl = page.evaluate("!!document.querySelector('[data-testid=\"primaryColumn\"]')")
                title = page.title()
                if has_tl and title:
                    break
                if i % 3 == 0:
                    self.log(f"  等待页面渲染... ({i * 5}s)")

            # ---- 检查登录状态 ----
            logged_in = page.evaluate("""() => {
                const hasTimeline = !!document.querySelector('[data-testid="primaryColumn"]');
                const noSignIn = !document.body?.innerText?.includes('Sign in');
                return hasTimeline && noSignIn;
            }""")
            self.q.put({"type": "login_status", "logged_in": logged_in})

            if logged_in:
                self.log("✅ 已登录 — 从 profile 恢复了会话")
            else:
                self.log("⚠️ 未登录 — 需要先运行登录脚本建立 profile", "warn")

            if self.stop.is_set() or not logged_in:
                return

            # ---- 轻度滚动 ----
            for i in range(2):
                if self.stop.is_set():
                    return
                # 用 mouse.wheel 代替 window.scrollBy，humanize 层接管为平滑滚动
                for _ in range(3):
                    page.mouse.wheel(0, random.randint(80, 150))
                    time.sleep(random.uniform(0.5, 1.2))
                time.sleep(random.uniform(1, 2))

            # ---- 搜索（模拟真人操作搜索框）----
            self.log(f"🔍 正在搜索: {self.search_term}")

            # 方式1: 尝试点击侧边栏的搜索输入框
            search_clicked = False
            for selector in [
                '[data-testid="SearchBox_Search_Input"]',
                'input[placeholder="Search"], input[placeholder="搜索"]',
                'input[aria-label="Search query"], input[aria-label="搜索查询"]',
                '[role="search"] input[type="text"]',
            ]:
                try:
                    el = page.wait_for_selector(selector, timeout=5000)
                    if el:
                        el.click()
                        time.sleep(self._rand(300, 800) / 1000)
                        self.log("  ⌨️  逐字符输入搜索关键词...")
                        # humanize 层接管 page.keyboard.type()，逐字符带随机间隔
                        page.keyboard.type(self.search_term)
                        time.sleep(self._rand(400, 1000) / 1000)
                        page.keyboard.press("Enter")
                        search_clicked = True
                        self.log("  🔍 已按 Enter 提交搜索")
                        break
                except Exception:
                    continue

            # 后备方式: 如果选择器都匹配不上，回退到 URL 直接跳转
            if not search_clicked:
                self.log("  ⚠️ 未找到搜索框，使用 URL 跳转")
                search_url = f"https://x.com/search?q={quote(self.search_term)}&src=typed_query&f=live"
                page.goto(search_url, wait_until="domcontentloaded")

            # 等待首次搜索结果渲染
            for i in range(12):
                if self.stop.is_set():
                    return
                time.sleep(3)
                has_tweets = page.evaluate("!!document.querySelector('article[data-testid=\"tweet\"]')")
                if has_tweets:
                    self.log(f"  搜索结果已加载 (耗时 {i * 3}s)")
                    break
                if i > 0 and i % 4 == 0:
                    self.log(f"  仍在等待... ({i * 3}s)")

            if self.stop.is_set():
                return

            # ---- 边滚边提，每次累加去重（对抗 Twitter 虚拟滚动） ----
            self.log(f"🔄 开始边滚边提 (目标: {self.max_tweets} 条)...")

            target_count = self.max_tweets
            seen_ids = set()
            all_tweets = []
            stagnant_count = 0
            max_scrolls = 30

            # 将 extraction_options 序列化为 JSON，传给每轮提取 JS
            extraction_flags = self.extraction_options  # dict → Playwright 自动序列化为 JS 对象

            for scroll_round in range(max_scrolls):
                if self.stop.is_set():
                    break

                # 每轮提取当前 DOM 里所有推文,用 tweet_id 去重累加到 all_tweets
                prev_size = len(all_tweets)
                new_tweets = extract_tweets(page, extraction_flags)

                # 累加去重
                for t in new_tweets:
                    tid = t.get("tweet_id")
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        all_tweets.append(t)
                # 截断到目标数
                if len(all_tweets) > target_count:
                    all_tweets = all_tweets[:target_count]
                    break

                added = len(all_tweets) - prev_size
                pct = min(100, int(len(all_tweets) / target_count * 100))
                self.log(f"  滚动第{scroll_round+1}次: DOM本轮{len(new_tweets)}条, 累计{len(all_tweets)}条 (+{added})",
                         progress=pct)

                if added == 0:
                    stagnant_count += 1
                else:
                    stagnant_count = 0

                if len(all_tweets) >= target_count:
                    self.log(f"  ✅ 已收集足够推文 ({len(all_tweets)} ≥ {target_count})")
                    break

                if stagnant_count >= 4:
                    self.log(f"  ⚠️ 连续{stagnant_count}次无新推文，停止滚动")
                    break

                # 滚动加载更多
                page.mouse.wheel(0, random.randint(600, 900))
                time.sleep(random.uniform(1.5, 3))

            tweets = all_tweets

            self.log(f"✅ 提取完成: {len(tweets)} 条推文")
            tweets = normalize_batch(tweets, "twitter")
            self.q.put({"type": "result", "tweets": tweets})

            # ---- 深度提取：评论区 + 作者主页 ----
            if tweets and not self.stop.is_set():
                extract_comments = self.extraction_options.get("comments", False)
                extract_profiles = self.extraction_options.get("profile", False)
                max_comments = self.extraction_options.get("max_comments_per_tweet", 10)

                if extract_comments:
                    self.log("📝 开始提取评论...")
                    for i, t in enumerate(tweets):
                        if self.stop.is_set():
                            break
                        tid = t.get("tweet_id")
                        if not tid:
                            continue
                        pct = int((i + 1) / len(tweets) * 100)
                        self.log(f"  [{i+1}/{len(tweets)}] 提取 @{t.get('author_handle','?')} 的评论...",
                                 progress=pct)
                        try:
                            t["comments"] = self._extract_comments(
                                page, t["author_handle"], tid, max_comments)
                            if t["comments"]:
                                self.log(f"     → {len(t['comments'])} 条评论")
                        except Exception as e:
                            self.log(f"     → 评论提取失败: {e}", "warn")

                    self.log("", progress=0)
                    # 归一化并更新结果
                    tweets = normalize_batch(tweets, "twitter")
                    self.q.put({"type": "result", "tweets": tweets})

                if extract_profiles:
                    self.log("👤 开始提取作者主页...")
                    # 去重
                    unique_handles = list(dict.fromkeys(
                        t.get("author_handle") for t in tweets if t.get("author_handle")))
                    profile_cache = {}
                    for i, handle in enumerate(unique_handles):
                        if self.stop.is_set():
                            break
                        pct = int((i + 1) / len(unique_handles) * 100)
                        self.log(f"  [{i+1}/{len(unique_handles)}] @{handle}...", progress=pct)
                        try:
                            profile_cache[handle] = self._extract_profile(page, handle)
                            if profile_cache[handle]:
                                bio = profile_cache[handle].get("bio", "")[:40]
                                followers = profile_cache[handle].get("followers", 0)
                                self.log(f"     → 粉丝:{followers} bio:{bio}")
                        except Exception as e:
                            self.log(f"     → 失败: {e}", "warn")

                    self.log("", progress=0)
                    for t in tweets:
                        if t.get("author_handle") in profile_cache:
                            t["profile"] = profile_cache[t["author_handle"]]

                    # 归一化并更新结果
                    tweets = normalize_batch(tweets, "twitter")
                    self.q.put({"type": "result", "tweets": tweets})

        finally:
            ctx.close()

        self.q.put({"type": "done", "success": True})

    @staticmethod
    def _extract_fingerprint(page) -> dict:
        """从浏览器中提取指纹信息。"""
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

            // WebGL 硬件指纹
            const gl = document.createElement('canvas').getContext('webgl');
            const dbg = gl ? gl.getExtension('WEBGL_debug_renderer_info') : null;

            // Canvas 哈希（L1 检测核心）
            let canvas_hash = 'N/A';
            try {
                const c = document.createElement('canvas');
                c.width = 200; c.height = 50;
                const ctx = c.getContext('2d');
                ctx.textBaseline = 'top';
                ctx.font = '14px Arial';
                ctx.fillStyle = '#f60';
                ctx.fillRect(0, 0, 200, 50);
                ctx.fillStyle = '#069';
                ctx.fillText('CloakBrowser 0123456789', 2, 15);
                // 简单哈希（截取 DataURL 特征段）
                const data = c.toDataURL();
                canvas_hash = data.substring(data.length - 40, data.length - 10);
            } catch {}

            return {
                ua,
                fullVersion,
                platform: navigator.platform,
                cores: navigator.hardwareConcurrency,
                memory: navigator.deviceMemory || 'N/A',
                gpu: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'N/A',
                gpuVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : 'N/A',
                screen: screen.width + 'x' + screen.height,
                touch_points: navigator.maxTouchPoints,
                plugins: navigator.plugins ? navigator.plugins.length : 0,
                mime_types: navigator.mimeTypes ? navigator.mimeTypes.length : 0,
                chrome_runtime: !!(window.chrome && window.chrome.runtime),
                languages: navigator.languages.join(', '),
                timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                webdriver: navigator.webdriver,
                canvas_hash: canvas_hash,
            };
        }""")

        # 获取出口 IP
        ip = "检测中..."
        try:
            page.goto("https://api.ipify.org/?format=json", timeout=10000)
            ip_raw = page.evaluate("document.body.innerText")
            ip = json.loads(ip_raw).get("ip", ip_raw)
        except Exception:
            ip = "无法检测"

        info["ip"] = ip
        return info

    def _extract_comments(self, page, author_handle: str, tweet_id: str,
                          max_comments: int) -> list[dict]:
        """进入推文详情页，滚动加载评论区并提取评论内容。

        Returns:
            [{commenter_handle, commenter_name, text, timestamp, likes}, ...]
        """
        detail_url = f"https://x.com/{author_handle}/status/{tweet_id}"
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        # 用 humanized mouse.wheel 滚动加载更多评论
        for _ in range(5):
            if self.stop.is_set():
                break
            page.mouse.wheel(0, random.randint(600, 900))
            time.sleep(random.uniform(1.5, 3))

        comments = page.evaluate(f"""(maxC) => {{
            // 推文详情页上所有 article 都是 tweet 结构
            // 第一个是原推文，后面的都是评论
            const articles = document.querySelectorAll('article[data-testid="tweet"]');
            const results = [];
            for (let i = 1; i < Math.min(articles.length, maxC + 1); i++) {{
                const el = articles[i];

                const nameEl = el.querySelector('[data-testid="User-Name"] a span');
                const commenter_name = nameEl ? nameEl.innerText.trim() : '';

                const handleEls = el.querySelectorAll('[data-testid="User-Name"] a');
                let commenter_handle = '';
                for (const a of handleEls) {{
                    const href = a.getAttribute('href') || '';
                    if (href.startsWith('/') && !href.includes('/status/')) {{
                        commenter_handle = href.replace('/', '').trim();
                        break;
                    }}
                }}

                const bodyEl = el.querySelector('[data-testid="tweetText"]');
                const text = bodyEl ? bodyEl.innerText.trim() : '';

                const timeEl = el.querySelector('time');
                const timestamp = timeEl ? timeEl.getAttribute('datetime') || '' : '';

                const getCount = (tid) => {{
                    const e = el.querySelector('[data-testid="' + tid + '"]');
                    if (!e) return 0;
                    const aria = e.getAttribute('aria-label') || '';
                    const match = aria.match(/([0-9,]+)/);
                    if (match) return parseInt(match[1].replace(/,/g, '')) || 0;
                    return 0;
                }};

                results.push({{
                    commenter_name, commenter_handle, text, timestamp,
                    likes: getCount('like')
                }});
            }}
            return results;
        }}""", max_comments)

        return comments

    def _extract_profile(self, page, handle: str) -> dict | None:
        """进入作者主页，提取个人资料信息。

        Returns:
            {bio, followers, following, join_date, verified, website, location, tweet_count}
        """
        profile_url = f"https://x.com/{handle}"
        page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        profile = page.evaluate("""() => {
            const getText = (testId) => {
                const el = document.querySelector(`[data-testid="${testId}"]`);
                return el ? el.innerText.trim() : '';
            };

            const getNumeric = (testId) => {
                const text = getText(testId);
                if (!text) return 0;
                // 处理 "1,234 Followers" 或 "1,234 人关注" 格式
                const match = text.match(/([0-9,]+)/);
                return match ? parseInt(match[1].replace(/,/g, '')) || 0 : 0;
            };

            return {
                bio: getText('UserDescription'),
                followers: getNumeric('followers'),
                following: getNumeric('following'),
                join_date: (() => {
                    const el = document.querySelector('[data-testid="UserJoinDate"]');
                    return el ? el.innerText.trim() : '';
                })(),
                verified: !!(document.querySelector('[data-testid="icon-verified"]') ||
                             document.querySelector('[aria-label="已认证的账号"]')),
                website: (() => {
                    const el = document.querySelector('[data-testid="UserUrl"]');
                    return el ? el.innerText.trim() : '';
                })(),
                location: (() => {
                    const el = document.querySelector('[data-testid="UserLocation"]');
                    return el ? el.innerText.trim() : '';
                })(),
                tweet_count: getNumeric('tweets'),
            };
        }""")

        return profile


# ---- YouTube 工作线程（API 发现 + yt-dlp 提取）----

class YoutubeWorker(threading.Thread):
    """YouTube 数据提取工作线程: API 搜索 -> yt-dlp 提取内容。

    不需要浏览器、不需要代理、不需要登录。
    通过 queue.Queue 向 UI 线程发送消息:
      {"type": "log", "text": str, "level": "info"|"warn"|"error"}
      {"type": "result", "tweets": list}    # 统一用 tweets 字段名
      {"type": "done", "success": bool}
    """

    def __init__(self, search_term: str, max_videos: int,
                 comments_per_video: int, msg_queue: queue.Queue,
                 stop_event: threading.Event):
        super().__init__(daemon=True)
        self.search_term = search_term
        self.max_videos = max_videos
        self.comments_per_video = comments_per_video
        self.q = msg_queue
        self.stop = stop_event

    def log(self, text: str, level: str = "info", progress: int | None = None):
        self.q.put({"type": "log", "text": text, "level": level, "progress": progress})

    def _to_tweet_format(self, video: dict) -> dict:
        """将 YouTube 视频数据映射为 console 通用的推文格式。"""
        comments = []
        for c in video.get("comments", []):
            comments.append({
                "commenter_name": c.get("author", ""),
                "commenter_handle": c.get("author", ""),
                "text": c.get("text", ""),
                "timestamp": c.get("time_text", ""),
                "likes": c.get("like_count", 0),
            })

        return {
            "tweet_id": video.get("video_id", ""),
            "author_name": video.get("uploader", ""),
            "author_handle": video.get("uploader_id", ""),
            "tweet_text": (video.get("title") or "")[:200],
            "timestamp": video.get("upload_date", ""),
            "likes": video.get("like_count", 0),
            "retweets": 0,
            "replies": video.get("comment_count", 0),
            "images": [video.get("thumbnail", "")] if video.get("thumbnail") else [],
            "description": video.get("description", ""),
            "view_count": video.get("view_count", 0),
            "duration": video.get("duration", 0),
            "channel_id": video.get("channel_id", ""),
            "uploader_url": video.get("uploader_url", ""),
            "webpage_url": video.get("webpage_url", ""),
            "tags": video.get("tags", []),
            "categories": video.get("categories", []),
            "comments": comments,
        }

    def run(self):
        try:
            self._run()
        except Exception as e:
            self.log(f"未预期的错误: {e}\n{traceback.format_exc()}", "error")
            self.q.put({"type": "done", "success": False})

    def _run(self):
        # ---- 阶段 1: API 搜索发现 ----
        self.log("=" * 50)
        self.log("阶段 1/2: API 搜索发现 video_id...")
        self.log(f"  关键词: {self.search_term}")

        try:
            api = YouTubeAPI()
        except ValueError as e:
            self.log(f"API Key 未配置,使用 yt-dlp 内置搜索", "warn")
            self.q.put({"type": "quota", "used": 0})  # 无配额
            video_ids = self._discover_via_ytdlp()
        else:
            try:
                video_ids = api.discover(
                    query=self.search_term,
                    count=self.max_videos,
                )
                self.log(f"  发现 {len(video_ids)} 个 video_id")
                self.log(f"  本次消耗: {api.quota_used} | 今日累计: {api.quota_used_today} / 10,000 units")
                self.q.put({"type": "quota", "used": api.quota_used})
            except Exception as e:
                self.log(f"  API 搜索失败: {e}", "warn")
                video_ids = self._discover_via_ytdlp()

        if self.stop.is_set():
            return

        if not video_ids:
            self.log("未发现任何视频", "error")
            self.q.put({"type": "done", "success": False})
            return

        # ---- 去重过滤 ----
        dedup_db = os.environ.get("YOUTUBE_DEDUP_DB", "youtube_dedup.db")
        store = DedupStore(dedup_db)
        try:
            original_count = len(video_ids)
            video_ids = store.filter_new(video_ids)
            skipped = original_count - len(video_ids)

            if not video_ids:
                self.log("所有视频已处理过(去重)", "warn")
                self.q.put({"type": "done", "success": True})
                return

            if skipped:
                self.log(f"  去重过滤: 跳过 {skipped} 个已处理, 剩余 {len(video_ids)} 个新视频")

            # ---- 阶段 2: yt-dlp 提取 ----
            self.log("=" * 50)
            self.log(f"阶段 2/2: yt-dlp 提取 {len(video_ids)} 个视频")
            if self.comments_per_video > 0:
                self.log(f"  (每个视频 {self.comments_per_video} 条评论)")

            extractor = YtDlpExtractor()
            results_raw = extractor.extract(
                video_ids,
                max_comments_per_video=self.comments_per_video,
                on_progress=lambda cur, tot, vid: self._on_progress(cur, tot, vid),
            )

            if self.stop.is_set():
                return

            # 转换为通用格式
            tweets = [self._to_tweet_format(v) for v in results_raw]

            self.log(f"提取完成: {len(tweets)} 个视频")

            # 存入去重 (使用旧格式 tweet_id)
            store.batch_mark_seen([t["tweet_id"] for t in tweets])

            tweets = normalize_batch(tweets, "youtube")
            self.q.put({"type": "result", "tweets": tweets})
            self.q.put({"type": "done", "success": True})
        finally:
            store.close()

    def _on_progress(self, current: int, total: int, video_id: str):
        pct = int(current / max(total, 1) * 100)
        self.log(f"  [{current}/{total}] {video_id}", progress=pct)

    def _discover_via_ytdlp(self) -> list[str]:
        """yt-dlp 内置搜索回退 (不需要 API Key)。"""
        self.log("  使用 yt-dlp 内置搜索...")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "yt_dlp",
                 "--flat-playlist", "--get-id",
                 f"ytsearch{min(self.max_videos, 20)}:{self.search_term}"],
                capture_output=True, text=True, timeout=30,
            )
            ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            self.log(f"  发现 {len(ids)} 个 video_id")
            return ids[:self.max_videos]
        except Exception as e:
            self.log(f"  yt-dlp 搜索失败: {e}", "error")
            return []


# ---- Reddit 工作线程（CloakBrowser + old.reddit.com）----

class RedditWorker(threading.Thread):
    """通过 CloakBrowser 访问 old.reddit.com 提取数据。

    old.reddit.com 是服务端渲染 HTML，无需 JS 解析，比新版 Reddit 简单。
    通过 queue.Queue 向 UI 线程发送消息:
      {"type": "log", "text": str, "level": "info"|"warn"|"error"}
      {"type": "result", "tweets": list}
      {"type": "done", "success": bool}
    """

    BASE = "https://old.reddit.com"

    def __init__(self, search_term: str, subreddit: str, sort: str,
                 time_filter: str, max_posts: int, request_delay: float,
                 extraction_options: dict,
                 msg_queue: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.search_term = search_term
        self.subreddit = subreddit
        self.sort = sort
        self.time_filter = time_filter
        self.max_posts = max_posts
        self.request_delay = request_delay
        self.extraction_options = extraction_options
        self.q = msg_queue
        self.stop = stop_event

    def log(self, text: str, level: str = "info", progress: int | None = None):
        self.q.put({"type": "log", "text": text, "level": level, "progress": progress})

    def run(self):
        try:
            self._run()
        except Exception as e:
            self.log(f"未预期的错误: {e}\n{traceback.format_exc()}", "error")
            self.q.put({"type": "done", "success": False})

    def _build_search_url(self) -> str:
        return build_search_url(
            self.search_term, self.subreddit, self.sort,
            self.time_filter, self.max_posts,
        )

    def _extract_posts_from_html(self, page) -> list[dict]:
        return extract_posts(page, self.extraction_options)

    def _extract_comments(self, page, permalink: str, max_comments: int) -> list[dict]:
        """进入帖子详情页，提取一级评论。"""
        detail_url = f"{self.BASE}{permalink}"
        page.goto(detail_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        return extract_comments(page, permalink, max_comments)

    def _extract_profile(self, page, username: str) -> dict | None:
        """访问用户主页提取信息。"""
        if username == "[deleted]":
            return None
        page.goto(f"{self.BASE}/user/{username}", wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
        return extract_profile(page, username)

    def _run(self):
        from cloakbrowser import launch_persistent_context

        search_url = self._build_search_url()
        self.log(f"🔍 搜索: {self.search_term}")
        self.log(f"   版块: r/{self.subreddit} | 排序: {self.sort} | 时间: {self.time_filter}")

        # 启动隐身浏览器（走代理，Reddit 不要求登录但有代理更安全）
        # 优先用韩国代理（复用 Twitter 的转发器），代理不通时回退直连
        ctx = launch_persistent_context(
            "./twitter-profiles/reddit_temp",
            headless=False,
            proxy="socks5://127.0.0.1:11080",
            humanize=True,
            human_preset="careful",
            timezone="America/New_York",
            locale="en-US",
        )
        page = ctx.new_page()
        page.set_default_navigation_timeout(60000)
        page.set_default_timeout(30000)
        try:
            # 首次访问首页做 cookie 交换
            self.log("  正在访问 old.reddit.com...")
            page.goto(self.BASE, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            # 搜索
            self.log(f"  跳转搜索: {search_url}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

            # ---- 分页提取（old.reddit 有"下一页"链接） ----
            target = self.max_posts
            seen_ids = set()
            all_posts = []
            max_pages = 10  # 最多翻 10 页

            for page_num in range(max_pages):
                if self.stop.is_set():
                    break

                # 提取当前页帖子
                new_posts = self._extract_posts_from_html(page)
                added = 0
                for p in new_posts:
                    tid = p.get("tweet_id")
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        all_posts.append(p)
                        added += 1

                pct = min(100, int(len(all_posts) / target * 100))
                self.log(f"  第{page_num+1}页: 提取{len(new_posts)}条, 累计{len(all_posts)}条 (+{added})",
                         progress=pct)

                # 够了就停
                if len(all_posts) >= target:
                    self.log(f"  ✅ 已收集足够帖子 ({len(all_posts)} ≥ {target})")
                    break

                # 找"下一页"链接
                next_link = find_next_page_url(page)
                if not next_link:
                    self.log(f"  ⚠️ 无下一页，停止翻页（已到最后一页）")
                    break

                # 跳转下一页
                self.log(f"  翻到第{page_num+2}页...")
                page.goto(next_link, wait_until="domcontentloaded", timeout=30000)
                time.sleep(2)

            # 截断到目标数
            posts = all_posts[:target]
            self.log(f"✅ 提取完成: {len(posts)} 个帖子", progress=100)
            posts = normalize_batch(posts, "reddit")
            self.q.put({"type": "result", "tweets": posts})

            # ---- 深度提取 ----
            flags = self.extraction_options
            if posts and not self.stop.is_set():
                if flags.get("comments", False):
                    max_c = flags.get("max_comments_per_tweet", 10)
                    self.log("📝 开始提取评论...")
                    for i, p in enumerate(posts):
                        if self.stop.is_set():
                            break
                        perm = p.get("permalink")
                        if not perm:
                            continue
                        pct = int((i + 1) / len(posts) * 100)
                        self.log(f"  [{i+1}/{len(posts)}] r/{p.get('subreddit','?')}: {p.get('tweet_text','')[:40]}...",
                                 progress=pct)
                        time.sleep(self.request_delay)
                        try:
                            p["comments"] = self._extract_comments(page, perm, max_c)
                            if p["comments"]:
                                self.log(f"     → {len(p['comments'])} 条评论")
                        except Exception as e:
                            self.log(f"     → 失败: {e}", "warn")
                    self.log("", progress=0)
                    posts = normalize_batch(posts, "reddit")
                    self.q.put({"type": "result", "tweets": posts})

                if flags.get("profile", False):
                    self.log("👤 开始提取作者主页...")
                    authors = list(dict.fromkeys(
                        p.get("author_handle") for p in posts if p.get("author_handle") and p["author_handle"] != "[deleted]"))
                    profile_cache = {}
                    for i, author in enumerate(authors):
                        if self.stop.is_set():
                            break
                        pct = int((i + 1) / len(authors) * 100)
                        self.log(f"  [{i+1}/{len(authors)}] u/{author}...", progress=pct)
                        time.sleep(self.request_delay)
                        try:
                            profile_cache[author] = self._extract_profile(page, author)
                            if profile_cache[author]:
                                self.log(f"     → ok")
                        except Exception as e:
                            self.log(f"     → 失败: {e}", "warn")
                    self.log("", progress=0)
                    for p in posts:
                        if p.get("author_handle") in profile_cache:
                            p["profile"] = profile_cache[p["author_handle"]]
                    posts = normalize_batch(posts, "reddit")
                    self.q.put({"type": "result", "tweets": posts})

        finally:
            ctx.close()

        self.q.put({"type": "done", "success": True})


# ---- UI 对话框 ----

class EditDialog(tk.Toplevel):
    """通用的 JSON 编辑对话框。"""

    def __init__(self, parent, title: str, items: list, fields: list[dict], key: str,
                 bool_fields: list[str] | None = None):
        """
        items: 当前条目列表
        fields: [{"key": "name", "label": "名称", "width": 20}, ...]
        key: 条目在 config 中的键名（仅用于窗口标题）
        bool_fields: 字段 key 列表，这些字段使用勾选框（True/False）而非文本输入
        """
        super().__init__(parent)
        self.title(title)
        self.items = list(items)
        self.fields = fields
        self.bool_fields = set(bool_fields or [])
        self.result = None
        self._build_ui()
        # 自动选中第一条，填充表单值
        if self.items:
            self.listbox.selection_set(0)
            self._on_select(None)
        self.transient(parent)
        self.grab_set()
        self.wait_window()

    def _build_ui(self):
        # 列表区域
        list_frame = ttk.LabelFrame(self, text="当前条目", padding=5)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.listbox = tk.Listbox(list_frame, height=8, width=50)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=scrollbar.set)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self._refresh_list()

        # 编辑区域
        edit_frame = ttk.LabelFrame(self, text="编辑", padding=5)
        edit_frame.pack(fill=tk.X, padx=10, pady=5)

        self.entries = {}
        self.bool_vars = {}
        for i, f in enumerate(self.fields):
            key = f["key"]
            label_text = f["label"]

            if key.startswith("_sep"):
                # 分隔符：跨两列显示加粗标签，无输入控件
                sep_label = ttk.Label(edit_frame, text=label_text, font=("", 10, "bold"))
                sep_label.grid(row=i, column=0, columnspan=2, sticky=tk.W, padx=5, pady=(8, 2))
                self.entries[key] = sep_label
                continue

            ttk.Label(edit_frame, text=label_text).grid(
                row=i, column=0, sticky=tk.W, padx=5, pady=2)

            if key in self.bool_fields:
                var = tk.BooleanVar(value=False)
                # 使用 tk.Checkbutton 而非 ttk.Checkbutton:
                # macOS 上 ttk 勾选后显示 ✗（令人误解），tk 原生控件显示 ✓
                cb = tk.Checkbutton(edit_frame, variable=var,
                                    onvalue=True, offvalue=False,
                                    highlightthickness=0)
                cb.grid(row=i, column=1, sticky=tk.W, padx=5, pady=2)
                self.entries[key] = cb
                self.bool_vars[key] = var
            else:
                entry = ttk.Entry(edit_frame, width=f.get("width", 40),
                                  show=f.get("show", ""))
                entry.grid(row=i, column=1, sticky=tk.EW, padx=5, pady=2)
                self.entries[key] = entry

        # 按钮
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(btn_frame, text="新增", command=self._add).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="更新选中", command=self._update).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="删除选中", command=self._delete).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="保存并关闭", command=self._save).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_frame, text="取消", command=self.destroy).pack(side=tk.RIGHT, padx=2)

    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for item in self.items:
            self.listbox.insert(tk.END, item.get("name", str(item)))

    def _is_sep(self, key: str) -> bool:
        return key.startswith("_sep")

    def _on_select(self, evt):
        sel = self.listbox.curselection()
        if not sel:
            return
        item = self.items[sel[0]]
        for f in self.fields:
            key = f["key"]
            if self._is_sep(key):
                continue
            if key in self.bool_fields:
                raw = item.get(key, False)
                if isinstance(raw, bool):
                    self.bool_vars[key].set(raw)
                elif isinstance(raw, str):
                    self.bool_vars[key].set(raw.lower() == "true")
                else:
                    self.bool_vars[key].set(False)
            else:
                self.entries[key].delete(0, tk.END)
                self.entries[key].insert(0, str(item.get(key, "")))

    def _read_form(self) -> dict:
        result = {}
        for f in self.fields:
            key = f["key"]
            if self._is_sep(key):
                continue
            if key in self.bool_fields:
                result[key] = self.bool_vars[key].get()
            else:
                result[key] = self.entries[key].get().strip()
        return result

    def _add(self):
        new_item = self._read_form()
        if not new_item.get("name"):
            messagebox.showwarning("提示", "名称不能为空")
            return
        self.items.append(new_item)
        self._refresh_list()

    def _update(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一条记录")
            return
        self.items[sel[0]] = self._read_form()
        self._refresh_list()

    def _delete(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        if messagebox.askyesno("确认", f"确定要删除 '{self.items[sel[0]].get('name', '')}' 吗？"):
            del self.items[sel[0]]
            self._refresh_list()

    def _save(self):
        self.result = self.items
        self.destroy()


# ---- 账号辅助 ----

def resolve_account_password(account: dict) -> str:
    """优先从环境变量 CB_TWITTER_PASS_<NAME> 取密码,其次 config.json。

    避免密码明文落在 config.json。account['name'] 如 'KR_1' → CB_TWITTER_PASS_KR_1。
    """
    name = account.get("name", "")
    env_key = f"CB_TWITTER_PASS_{name.upper()}"
    env_val = os.environ.get(env_key)
    if env_val:
        return env_val
    return account.get("password", "")


# ---- 主控制台 ----

class ConsoleApp:
    """多平台数据采集控制台（Twitter / Reddit / YouTube）。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CloakBrowser 多平台数据采集")
        self.root.geometry("980x780")
        self.root.minsize(900, 650)

        self.cfg = load_config()
        self.msg_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[TwitterWorker] = None
        self.forwarder_proc: Optional[subprocess.Popen] = None
        self.fp_labels: dict[str, tk.StringVar] = {}
        self._fp_visible = False   # 指纹面板默认隐藏
        self._fp_frame: ttk.LabelFrame | None = None
        self._fp_paned: ttk.PanedWindow | None = None  # 中部分割窗口引用

        self._build_ui()
        self._poll_queue()

        # 键盘快捷键 (P1-10)
        self.root.bind("<Command-Return>", lambda _: self._start())
        self.root.bind("<Control-Return>", lambda _: self._start())
        self.root.bind("<Escape>", lambda _: self._stop())
        self.root.bind("<Command-s>", lambda _: self._export_json())
        self.root.bind("<Control-s>", lambda _: self._export_json())

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- UI 构建 ----

    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")

        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # ── 配置区 ──
        self._build_config_section(main)

        # ── 指纹 + 日志区 (左右分栏) ──
        self._fp_paned = ttk.PanedWindow(main, orient=tk.HORIZONTAL)
        self._fp_paned.pack(fill=tk.BOTH, expand=True, pady=5)

        self._build_fingerprint_panel(self._fp_paned)
        self._build_log_panel(self._fp_paned)

        # ── 结果区 ──
        self._build_result_section(main)

        # ── 底部按钮 ──
        self._build_bottom_buttons(main)

    def _build_config_section(self, parent):
        frame = ttk.LabelFrame(parent, text="配置", padding=10)
        frame.pack(fill=tk.X, pady=(0, 5))

        # 第一行: 平台 + 方案 + 管理按钮
        row1 = ttk.Frame(frame)
        row1.pack(fill=tk.X, pady=2)

        # 平台选择
        ttk.Label(row1, text="平台:").pack(side=tk.LEFT)
        self.platform_var = tk.StringVar(value="Twitter")
        platform_values = ["Twitter", "Reddit"]
        if _youtube_available:
            platform_values.append("YouTube")
        self.platform_combo = ttk.Combobox(row1, textvariable=self.platform_var,
                                            values=platform_values, width=10, state="readonly")
        self.platform_combo.pack(side=tk.LEFT, padx=(2, 2))
        self.platform_combo.current(0)
        self.platform_combo.bind("<<ComboboxSelected>>", self._on_platform_changed)

        # 方案（绑定账号+代理+指纹+提取）
        ttk.Label(row1, text="  方案:").pack(side=tk.LEFT)
        self.bundle_var = tk.StringVar()
        bundle_names = [b["name"] for b in self.cfg.get("bundles", [])]
        self.bundle_combo = ttk.Combobox(row1, textvariable=self.bundle_var,
                                          values=bundle_names, width=18, state="readonly")
        self.bundle_combo.pack(side=tk.LEFT, padx=(2, 2))
        if bundle_names:
            self.bundle_combo.current(0)
        ttk.Button(row1, text="✎", width=3, command=self._edit_bundles).pack(side=tk.LEFT)
        ttk.Button(row1, text="⚙", width=3, command=self._manage_components).pack(side=tk.LEFT)

        # 指纹面板切换按钮
        self.fp_toggle_btn = ttk.Button(row1, text="🔍 指纹", width=7, command=self._toggle_fingerprint)
        self.fp_toggle_btn.pack(side=tk.RIGHT, padx=2)

        # 选中方案时显示摘要
        self.bundle_summary_var = tk.StringVar(value="")
        summary_label = ttk.Label(row1, textvariable=self.bundle_summary_var, foreground="gray", font=("", 9))
        summary_label.pack(side=tk.LEFT, padx=(10, 0))
        self.bundle_combo.bind("<<ComboboxSelected>>", self._on_bundle_changed)
        # 初始显示
        self._on_bundle_changed()

        # 第二行: 搜索词 + 平台特有参数 + 数量 + 模式 + 按钮
        row2 = ttk.Frame(frame)
        row2.pack(fill=tk.X, pady=(5, 0))

        # Reddit 子版块输入（仅 Reddit 模式显示）
        self.reddit_config_frame = ttk.Frame(row2)
        ttk.Label(self.reddit_config_frame, text="版块:").pack(side=tk.LEFT)
        self.subreddit_var = tk.StringVar(value=self.cfg.get("reddit", {}).get("subreddit", "all"))
        self.subreddit_entry = ttk.Entry(self.reddit_config_frame, textvariable=self.subreddit_var, width=15)
        self.subreddit_entry.pack(side=tk.LEFT, padx=2)
        ttk.Label(self.reddit_config_frame, text="  排序:").pack(side=tk.LEFT)
        self.reddit_sort_var = tk.StringVar(value=self.cfg.get("reddit", {}).get("sort", "relevance"))
        ttk.Combobox(self.reddit_config_frame, textvariable=self.reddit_sort_var,
                     values=["relevance", "hot", "new", "top", "comments"],
                     width=10, state="readonly").pack(side=tk.LEFT, padx=2)
        ttk.Label(self.reddit_config_frame, text="  时间:").pack(side=tk.LEFT)
        self.reddit_time_var = tk.StringVar(value=self.cfg.get("reddit", {}).get("time", "all"))
        ttk.Combobox(self.reddit_config_frame, textvariable=self.reddit_time_var,
                     values=["all", "year", "month", "week", "day", "hour"],
                     width=6, state="readonly").pack(side=tk.LEFT, padx=2)

        # YouTube 参数（仅 YouTube 模式显示）
        self.youtube_config_frame = ttk.Frame(row2)
        ttk.Label(self.youtube_config_frame, text="评论数/视频:").pack(side=tk.LEFT)
        self.yt_comments_var = tk.IntVar(value=self.cfg.get("youtube", {}).get("comments_per_video", 10))
        ttk.Spinbox(self.youtube_config_frame, from_=0, to=100,
                    textvariable=self.yt_comments_var, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(self.youtube_config_frame,
                  text="  (0=只要元数据, 无需代理/API Key=直接提取)",
                  foreground="gray", font=("", 9)).pack(side=tk.LEFT)

        # API 配额显示（仅 YouTube 模式需要）
        self.quota_var = tk.StringVar(value="")
        self.quota_label = ttk.Label(self.youtube_config_frame,
                                     textvariable=self.quota_var,
                                     foreground="#5a9e6f", font=("", 9, "bold"))
        self.quota_label.pack(side=tk.LEFT, padx=(10, 0))

        ttk.Label(row2, text="搜索:").pack(side=tk.LEFT)
        # 搜索框：可下拉的历史记录
        search_history = self.cfg.get("search", {}).get("history", [])
        self.search_var = tk.StringVar(value=self.cfg.get("search", {}).get("default_term", ""))
        self.search_combo = ttk.Combobox(row2, textvariable=self.search_var,
                                          values=search_history, width=25)
        self.search_combo.pack(side=tk.LEFT, padx=2)
        # 仅在回车提交时保存到历史,避免每次按键都写盘(否则会产生大量半截残词)
        self.search_combo.bind("<Return>", lambda _: self._commit_search_history())

        ttk.Label(row2, text="最大提取:").pack(side=tk.LEFT, padx=(10, 0))
        self.max_var = tk.IntVar(value=self.cfg.get("search", {}).get("max_tweets", 10))
        self.max_spinbox = ttk.Spinbox(row2, from_=1, to=100, textvariable=self.max_var, width=5)
        self.max_spinbox.pack(side=tk.LEFT, padx=2)

        self.headless_var = tk.BooleanVar(value=False)
        self.headless_checkbox = ttk.Checkbutton(row2, text="无头模式", variable=self.headless_var)
        self.headless_checkbox.pack(side=tk.LEFT, padx=(10, 0))

        # 进度条 + 预估剩余时间
        self.progress_var = tk.IntVar(value=0)
        self.progress_bar = ttk.Progressbar(row2, variable=self.progress_var, maximum=100, length=120)
        self.progress_bar.pack(side=tk.LEFT, padx=(10, 0))
        self.eta_var = tk.StringVar(value="")
        self.eta_label = ttk.Label(row2, textvariable=self.eta_var, foreground="gray", font=("", 9), width=18)
        self.eta_label.pack(side=tk.LEFT, padx=(5, 0))
        self._task_start_time: float = 0  # 当前任务开始时间(秒)

        # 操作按钮
        self.start_btn = ttk.Button(row2, text="▶ 开始搜索", command=self._start)
        self.start_btn.pack(side=tk.RIGHT, padx=2)

        self.stop_btn = ttk.Button(row2, text="■ 停止", command=self._stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, padx=2)

    def _build_fingerprint_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="指纹信息", padding=8)
        self._fp_frame = frame
        parent.add(frame, weight=1)
        # 默认隐藏
        parent.forget(frame)

        # 双列布局，16 项分两半
        keys = [
            ("ua_label",       "UA"),             ("plugins_label",  "Plugins"),
            ("platform_label", "平台"),            ("mime_label",     "MIME Types"),
            ("gpu_label",      "GPU"),             ("chrome_runtime_label", "chrome.runtime"),
            ("memory_label",   "内存"),            ("canvas_label",   "Canvas 哈希"),
            ("screen_label",   "屏幕"),            ("touch_label",    "触控点数"),
            ("languages_label","语言"),            ("ip_label",       "出口IP"),
            ("timezone_label", "时区"),            ("cores_label",    "CPU 核心"),
            ("webdriver_label","WebDriver"),       ("full_version_label", "Chrome 版本"),
        ]

        half = len(keys) // 2
        for i, (var_name, label) in enumerate(keys):
            if i < half:
                row, key_col, val_col = i, 0, 1
            else:
                row, key_col, val_col = i - half, 2, 3

            ttk.Label(frame, text=f"{label}:", font=("", 9, "bold")).grid(
                row=row, column=key_col, sticky=tk.W, padx=(2, 1), pady=1)
            sv = tk.StringVar(value="—")
            self.fp_labels[var_name] = sv
            lbl = ttk.Label(frame, textvariable=sv, wraplength=200, font=("", 9))
            lbl.grid(row=row, column=val_col, sticky=tk.W, padx=(1, 5), pady=1)

        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

    def _build_log_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="运行日志", padding=5)
        parent.add(frame, weight=2)

        self.log_text = tk.Text(frame, wrap=tk.WORD, state=tk.DISABLED,
                                 font=("Menlo", 10), bg="#1e1e1e", fg="#d4d4d4",
                                 insertbackground="white")
        self.log_text.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(self.log_text, orient=tk.VERTICAL, command=self.log_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

        # 配置 tag 颜色
        self.log_text.tag_config("info", foreground="#d4d4d4")
        self.log_text.tag_config("warn", foreground="#e5c07b")
        self.log_text.tag_config("error", foreground="#e06c75")
        self.log_text.tag_config("success", foreground="#98c379")

    def _build_result_section(self, parent):
        """构建结果区：上方表格 + 下方详情预览（分割面板）。"""
        result_pane = ttk.PanedWindow(parent, orient=tk.VERTICAL)
        result_pane.pack(fill=tk.BOTH, expand=True, pady=(5, 5))

        # 上半部分: 表格
        table_frame = ttk.Frame(result_pane)
        result_pane.add(table_frame, weight=1)

        # 列 ID 固定, 平台切换时只改 heading 文字
        columns = ("#", "作者", "内容摘要", "图", "评", "赞", "转", "回", "标签")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings",
                                  height=8, selectmode="browse")
        self.tree.heading("#", text="#", anchor=tk.CENTER)
        self.tree.column("#", width=30, anchor=tk.CENTER, stretch=False)
        self.tree.heading("作者", text="作者")
        self.tree.column("作者", width=100, stretch=False)
        self.tree.heading("内容摘要", text="内容摘要")
        self.tree.column("内容摘要", width=300)
        self.tree.heading("图", text="图", anchor=tk.CENTER)
        self.tree.column("图", width=30, anchor=tk.CENTER, stretch=False)
        self.tree.heading("评", text="评", anchor=tk.CENTER)
        self.tree.column("评", width=40, anchor=tk.CENTER, stretch=False)
        self.tree.heading("赞", text="赞", anchor=tk.CENTER)
        self.tree.column("赞", width=45, anchor=tk.CENTER, stretch=False)
        self.tree.heading("转", text="转", anchor=tk.CENTER)
        self.tree.column("转", width=45, anchor=tk.CENTER, stretch=False)
        self.tree.heading("回", text="回", anchor=tk.CENTER)
        self.tree.column("回", width=45, anchor=tk.CENTER, stretch=False)
        self.tree.heading("标签", text="标签", anchor=tk.CENTER)
        self.tree.column("标签", width=65, anchor=tk.CENTER, stretch=False)

        t_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=t_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        t_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 单击选中 → 下方自动预览（替代双击弹窗）
        self.tree.bind("<<TreeviewSelect>>", self._on_tweet_select)
        self.tweets_cache: list[dict] = []
        self.tweets_all: list[dict] = []  # 完整列表（用于筛选）

        # 下半部分: 详情预览
        self.detail_frame = ttk.LabelFrame(result_pane, text="详情 (单击表格行查看)", padding=5)
        result_pane.add(self.detail_frame, weight=1)

        self.detail_text = tk.Text(self.detail_frame, wrap=tk.WORD, state=tk.DISABLED,
                                    font=("", 10), padx=8, pady=8,
                                    bg="#f5f5f5", fg="#333333")
        self.detail_text.pack(fill=tk.BOTH, expand=True)
        d_scroll = ttk.Scrollbar(self.detail_text, orient=tk.VERTICAL, command=self.detail_text.yview)
        d_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_text.config(yscrollcommand=d_scroll.set)

    # ---- 表格列头适配 ----

    _COLUMN_CONFIGS = {
        "Twitter":  {"图": "图",   "评": "评",   "赞": "赞",   "转": "转",   "回": "回",   "图w": 30},
        "Reddit":   {"图": "",     "评": "评",   "赞": "赞同", "转": "版块", "回": "回",   "图w": 0},
        "YouTube":  {"图": "",     "评": "评论", "赞": "播放", "转": "点赞", "回": "时长", "图w": 0},
    }

    def _reconfigure_columns(self, platform: str):
        """根据平台切换表格列头文字和宽度。"""
        cfg = self._COLUMN_CONFIGS.get(platform, self._COLUMN_CONFIGS["Twitter"])
        for col_key in ("图", "评", "赞", "转", "回"):
            self.tree.heading(col_key, text=cfg[col_key])
        self.tree.column("图", width=cfg.get("图w", 30))

    def _build_bottom_buttons(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(frame, textvariable=self.status_var).pack(side=tk.LEFT)

        # 标签筛选下拉框（LLM 过滤后可用）
        ttk.Label(frame, text=" 筛选:").pack(side=tk.LEFT, padx=(20, 0))
        self.filter_var = tk.StringVar(value="全部")
        self.filter_combo = ttk.Combobox(frame, textvariable=self.filter_var,
                                          values=["全部", "TARGET", "AD", "IRRELEVANT"],
                                          width=12, state="readonly")
        self.filter_combo.pack(side=tk.LEFT, padx=(2, 0))
        self.filter_combo.current(0)
        self.filter_combo.bind("<<ComboboxSelected>>",
            lambda _: self._populate_results(self.tweets_all, update_cache=False))

        ttk.Button(frame, text="导出 JSON", command=self._export_json).pack(side=tk.RIGHT, padx=2)
        ttk.Button(frame, text="LLM 过滤", command=self._llm_filter).pack(side=tk.RIGHT, padx=2)

    # ---- 消息轮询 ----

    def _poll_queue(self):
        """定时从队列取消息，更新 UI。"""
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass

        # 检查 worker 是否结束
        if self.worker and not self.worker.is_alive():
            self._on_worker_done()

        self.root.after(100, self._poll_queue)

    def _handle_message(self, msg: dict):
        msg_type = msg.get("type")

        if msg_type == "log":
            self._append_log(msg["text"], msg.get("level", "info"),
                             progress=msg.get("progress"))

        elif msg_type == "fingerprint":
            data = msg["data"]
            # Canvas 哈希截断
            ch = data.get("canvas_hash", "")
            canvas_short = ch[:20] + "..." if len(ch) > 20 else ch

            mappings = {
                "ua_label":             data.get("ua", "")[:80],
                "full_version_label":   data.get("fullVersion", "") or "N/A",
                "platform_label":       str(data.get("platform", "")),
                "cores_label":          str(data.get("cores", "")),
                "memory_label":         str(data.get("memory", "N/A")),
                "gpu_label":            f"{data.get('gpuVendor','')} — {data.get('gpu','')}"[:60],
                "screen_label":         data.get("screen", ""),
                "touch_label":          str(data.get("touch_points", "")),
                "plugins_label":        str(data.get("plugins", "")),
                "mime_label":           str(data.get("mime_types", "")),
                "chrome_runtime_label": str(data.get("chrome_runtime", "")),
                "languages_label":      data.get("languages", ""),
                "timezone_label":       data.get("timezone", ""),
                "webdriver_label":      str(data.get("webdriver", "")),
                "canvas_label":         canvas_short,
                "ip_label":             data.get("ip", ""),
            }
            for key, value in mappings.items():
                if key in self.fp_labels:
                    self.fp_labels[key].set(value)

        elif msg_type == "login_status":
            logged_in = msg["logged_in"]
            if not logged_in:
                self._append_log("⚠️ 未登录！请先运行登录脚本建立 profile", "warn")

        elif msg_type == "result":
            tweets = msg["tweets"]
            self.tweets_cache = tweets
            self._populate_results(tweets)

        elif msg_type == "llm_done":
            self.status_var.set("就绪")
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)

        elif msg_type == "quota":
            used = msg.get("used", 0)
            if used > 0:
                # 从磁盘读取跨进程累计配额(含其他脚本的消耗)
                remaining = get_quota_remaining()
                self.quota_var.set(f"API 配额: {remaining:,}/10,000")
            elif not os.environ.get("YOUTUBE_API_KEY"):
                self.quota_var.set("(无 API Key, 使用 yt-dlp 搜索)")

        elif msg_type == "done":
            success = msg["success"]

    def _append_log(self, text: str, level: str = "info", progress: int | None = None):
        self.log_text.config(state=tk.NORMAL)
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] ", "info")
        self.log_text.insert(tk.END, f"{text}\n", level)
        self.log_text.see(tk.END)  # 自动滚到底部
        self.log_text.config(state=tk.DISABLED)
        if progress is not None:
            self.progress_var.set(progress)
            self._update_eta(progress)

    def _update_eta(self, progress: int):
        """根据进度和已用时间计算预估剩余时间。"""
        if not self._task_start_time or progress <= 0 or progress >= 100:
            if progress >= 100:
                t = time.time() - self._task_start_time
                self.eta_var.set(f"耗时 {self._format_duration(t)}")
            return

        elapsed = time.time() - self._task_start_time
        if elapsed < 1:  # 刚开始,还没足够数据
            return

        eta = (elapsed / progress) * (100 - progress)
        self.eta_var.set(f"剩余 ~{self._format_duration(eta)}")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """格式化时长: 1h23m / 5m12s / 23s"""
        s = max(0, int(seconds))
        if s >= 3600:
            h, m = divmod(s, 3600)
            return f"{h}h{m // 60}m"
        elif s >= 60:
            return f"{s // 60}m{s % 60}s"
        else:
            return f"{s}s"

    # ---- 操作 ----

    def _start_forwarder(self, proxy_info: dict | None = None) -> bool:
        """启动 SOCKS5 本地转发器（如需要）。返回 True 表示已启动。"""
        if not proxy_info or proxy_info.get("server", "") != "socks5://127.0.0.1:11080":
            return False  # 不需要转发器

        self._append_log("正在启动本地 SOCKS5 转发器...")
        forwarder_script = Path(__file__).resolve().parent / "socks5_forwarder.py"
        try:
            self.forwarder_proc = subprocess.Popen(
                [sys.executable, str(forwarder_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(2)  # 等待转发器启动
            self._append_log("✅ 本地转发器已启动 (127.0.0.1:11080)", "success")
            return True
        except Exception as e:
            self._append_log(f"转发器启动失败: {e}", "error")
            return False

    def _stop_forwarder(self):
        """停止 SOCKS5 本地转发器（后台线程，不阻塞 UI）。"""
        if self.forwarder_proc:
            proc = self.forwarder_proc
            self.forwarder_proc = None
            # 后台终止并等待退出，避免僵尸进程
            def _cleanup():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            threading.Thread(target=_cleanup, daemon=True).start()

    def _resolve_bundle(self):
        """从选中的方案解析出账号、代理、指纹、提取四组件。"""
        bundle_name = self.bundle_var.get()
        bundle = next((b for b in self.cfg.get("bundles", []) if b["name"] == bundle_name), None)
        if not bundle:
            messagebox.showwarning("提示", "请先选择一个方案")
            return None
        account = next((a for a in self.cfg.get("accounts", []) if a["name"] == bundle.get("account")), {})
        proxy_info = next((p for p in self.cfg.get("proxies", []) if p["name"] == bundle.get("proxy")), None)
        fp = next((f for f in self.cfg.get("fingerprints", []) if f["name"] == bundle.get("fingerprint")), {})
        extraction_preset = next((e for e in self.cfg.get("extractions", []) if e["name"] == bundle.get("extraction")), {})
        extraction_options = {k: v for k, v in extraction_preset.items() if not k.startswith("_sep")}
        return {"bundle": bundle, "account": account, "proxy": proxy_info,
                "fp": fp, "extraction": extraction_options}

    def _commit_search_history(self):
        """把当前搜索词存入历史(置顶去重,上限 20)。仅在提交时调用。"""
        term = self.search_var.get().strip()
        if not term:
            return
        history = self.cfg.setdefault("search", {}).setdefault("history", [])
        if term in history:
            history.remove(term)
        history.insert(0, term)
        del history[20:]
        save_config(self.cfg)
        self.search_combo["values"] = list(history)

    def _start(self):
        """启动搜索任务（根据平台分发到 Twitter 或 Reddit）。"""
        search_term = self.search_var.get().strip()
        if not search_term:
            messagebox.showwarning("提示", "请输入搜索关键词")
            return

        # 提交时保存搜索历史(不再每次按键写盘)
        self._commit_search_history()

        # 清空上一次结果
        self._clear_results()

        # 重置状态
        self.stop_event.clear()

        # 更新 UI 状态
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_var.set("运行中...")
        self._task_start_time = time.time()
        self.eta_var.set("")

        platform = self.platform_var.get()
        is_reddit = platform == "Reddit"
        is_youtube = platform == "YouTube"

        if is_youtube:
            # ---- YouTube 模式（API + yt-dlp, 不需要浏览器/代理） ----
            if not _youtube_available:
                messagebox.showwarning("提示",
                    "YouTube 模块未安装。请确保:\n"
                    "  pip install yt-dlp google-api-python-client\n"
                    "并且 youtube_api.py / youtube_ytdlp.py / youtube_dedup.py 在同目录下。")
                self.start_btn.config(state=tk.NORMAL)
                self.stop_btn.config(state=tk.DISABLED)
                self.status_var.set("就绪")
                return

            comments_per = self.yt_comments_var.get()

            # 保存 YouTube 配置
            if "youtube" not in self.cfg:
                self.cfg["youtube"] = {}
            self.cfg["youtube"]["comments_per_video"] = comments_per
            save_config(self.cfg)

            mode_desc = "元数据+评论" if comments_per > 0 else "仅元数据"
            self._append_log(f"🔴 YouTube 模式 | {mode_desc} | 无需浏览器/代理")
            self._append_log(f"  评论数/视频: {comments_per}")

            self.worker = YoutubeWorker(
                search_term=search_term,
                max_videos=self.max_var.get(),
                comments_per_video=comments_per,
                msg_queue=self.msg_queue,
                stop_event=self.stop_event,
            )
            self.worker.start()
            return

        if is_reddit:
            # ---- Reddit 模式（需要浏览器 + 代理，无需登录） ----
            # 从当前方案读取提取模板（和 Twitter 模式一致）
            bundle_name = self.bundle_var.get()
            bundle = next((b for b in self.cfg.get("bundles", []) if b["name"] == bundle_name), None)
            extraction_name = bundle.get("extraction", "仅基础") if bundle else "仅基础"
            extraction_preset = next(
                (e for e in self.cfg.get("extractions", []) if e["name"] == extraction_name), {})
            extraction_options = {k: v for k, v in extraction_preset.items()
                                  if not k.startswith("_sep")}

            subreddit = self.subreddit_var.get().strip() or "all"
            sort = self.reddit_sort_var.get()
            time_filter = self.reddit_time_var.get()
            request_delay = self.cfg.get("reddit", {}).get("request_delay", 2.0)

            # 保存 Reddit 配置
            if "reddit" not in self.cfg:
                self.cfg["reddit"] = {}
            self.cfg["reddit"].update({"subreddit": subreddit, "sort": sort,
                                        "time": time_filter, "request_delay": request_delay})
            save_config(self.cfg)

            self._append_log(f"🟠 Reddit 模式 | 版块: r/{subreddit} | 排序: {sort} | 时间: {time_filter}")
            self._append_log(f"  提取模板: {extraction_preset.get('name', '默认')}")

            # 代理可达性检查 (P2-16)
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            proxy_ok = sock.connect_ex(("127.0.0.1", 11080)) == 0
            sock.close()
            if not proxy_ok:
                self._append_log("⚠️ SOCKS5 转发器未启动 (127.0.0.1:11080)", "warn")
                self._append_log("  请先运行: python tools/apps/socks5_forwarder.py")
                if not messagebox.askyesno("代理不可达", "SOCKS5 转发器 127.0.0.1:11080 未启动。\n\n请先在另一个终端运行:\n  python tools/apps/socks5_forwarder.py\n\n是否仍要继续(直连)?", parent=self.root):
                    self.start_btn.config(state=tk.NORMAL)
                    self.stop_btn.config(state=tk.DISABLED)
                    self.status_var.set("就绪")
                    return
                self._append_log("  用户选择继续(直连)", "warn")

            # 启动 SOCKS5 转发器
            self._start_forwarder({"server": "socks5://127.0.0.1:11080"})

            self.worker = RedditWorker(
                search_term=search_term,
                subreddit=subreddit,
                sort=sort,
                time_filter=time_filter,
                max_posts=self.max_var.get(),
                request_delay=request_delay,
                extraction_options=extraction_options,
                msg_queue=self.msg_queue,
                stop_event=self.stop_event,
            )
            self.worker.start()
            return

        # ---- Twitter 模式 ----
        resolved = self._resolve_bundle()
        if not resolved:
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            self.status_var.set("就绪")
            return
        account = resolved["account"]
        proxy_info = resolved["proxy"]
        fp = resolved["fp"]
        extraction_options = resolved["extraction"]

        # 启动 SOCKS5 转发器（如需要）
        self._start_forwarder(proxy_info)

        # 启动工作线程
        self.worker = TwitterWorker(
            account=account,
            proxy_info=proxy_info,
            fp=fp,
            search_term=search_term,
            max_tweets=self.max_var.get(),
            headless=self.headless_var.get(),
            extraction_options=extraction_options,
            msg_queue=self.msg_queue,
            stop_event=self.stop_event,
        )
        self.worker.start()

    def _stop(self):
        """停止搜索任务。"""
        self._append_log("正在停止...", "warn")
        self.stop_event.set()
        self.stop_btn.config(state=tk.DISABLED)
        self.status_var.set("正在停止...")

    def _on_worker_done(self):
        """Worker 线程结束后的清理。"""
        # 更新方案使用统计
        try:
            bundle_name = self.bundle_var.get()
            for b in self.cfg.get("bundles", []):
                if b["name"] == bundle_name:
                    b["usage_count"] = b.get("usage_count", 0) + 1
                    b["usage_last"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    save_config(self.cfg)
                    break
        except Exception:
            pass

        self.worker = None
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_var.set("就绪")
        self._task_start_time = 0
        self._stop_forwarder()

    def _populate_results(self, tweets: list[dict], update_cache: bool = True):
        """将归一化后的数据填入表格。"""
        if update_cache:
            self.tweets_cache = tweets
            self.tweets_all = list(tweets)

        # 根据筛选标签决定显示哪些
        filter_label = self.filter_var.get()
        visible = tweets
        if filter_label != "全部":
            visible = [t for t in tweets if t.get("label") == filter_label]

        self.tree.delete(*self.tree.get_children())
        for i, item in enumerate(visible):
            text_preview = item.get("content", {}).get("title", "")[:70].replace("\n", " ")
            author = f"@{item.get('author', '?')}"
            meta = item.get("meta", {})
            img_count = len(meta.get("images", []) or [])
            comment_count = len(item.get("comments", []) or [])
            label = item.get("label", "")

            if item.get("platform") == "youtube":
                # YouTube: 图=空, 评=评论数, 赞=播放, 转=点赞, 回=时长
                duration = meta.get("duration", 0) or 0
                duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else ""
                self.tree.insert("", tk.END, values=(
                    i + 1, author, text_preview,
                    "",
                    comment_count or "",
                    meta.get("views", 0),
                    meta.get("likes", 0),
                    duration_str,
                    label,
                ))
            else:
                self.tree.insert("", tk.END, values=(
                    i + 1,
                    author,
                    text_preview,
                    f"🖼{img_count}" if img_count else "",
                    comment_count or "",
                    meta.get("likes", 0),
                    meta.get("retweets", 0),
                    meta.get("replies", 0),
                    label,
                ))

    def _clear_results(self):
        """清空结果表格和日志。YouTube 模式下不重置指纹面板。"""
        self.tree.delete(*self.tree.get_children())
        self.tweets_cache = []
        self.tweets_all = []
        self.detail_text.config(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.config(state=tk.DISABLED)
        # 指纹面板仅 Twitter/Reddit 模式下重置 (YouTube 不走浏览器)
        if self.platform_var.get() != "YouTube":
            for sv in self.fp_labels.values():
                sv.set("—")
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.eta_var.set("")
        self._task_start_time = 0

    def _on_tweet_select(self, _evt=None):
        """单击表格行 → 下方自动显示详情（不再弹窗）。"""
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        if not values:
            return
        idx = int(values[0]) - 1
        # 在当前可见列表中找
        filter_label = self.filter_var.get()
        visible = self.tweets_all
        if filter_label != "全部":
            visible = [t for t in self.tweets_all if t.get("label") == filter_label]
        if idx < len(visible):
            t = visible[idx]
            handle = t.get('author', '?')
            name = t.get('author_name', '?')
            platform = t.get('platform', '')
            is_youtube = platform == "youtube"
            meta = t.get("meta", {})
            content = t.get("content", {})

            if is_youtube:
                # YouTube 详情
                views = meta.get("views", 0)
                duration = meta.get("duration", 0)
                mins = duration // 60
                secs = duration % 60
                tags = meta.get("tags", [])
                categories = meta.get("categories", [])

                text_parts = [
                    f"🎬 {content.get('title', '')}",
                    f"频道: {name} ({handle})",
                    f"视频ID: {t.get('id', '?')}",
                    f"播放: {views:,}  点赞: {meta.get('likes', 0):,}  评论: {meta.get('replies', 0):,}  时长: {mins}:{secs:02d}",
                ]
                if meta.get("url"):
                    text_parts.append(f"链接: {meta['url']}")
                if tags:
                    text_parts.append(f"标签: {', '.join(tags[:10])}")
                if categories:
                    text_parts.append(f"分类: {', '.join(categories)}")
                if content.get("body"):
                    text_parts.append(f"{'─' * 50}")
                    text_parts.append(f"摘要:")
                    text_parts.append(content['body'][:500])
            else:
                text_parts = [
                    f"作者: @{handle} ({name})",
                    f"ID: {t.get('id', '?')}",
                    f"时间: {meta.get('timestamp', '?')}",
                    f"点赞: {meta.get('likes', 0)}  转发: {meta.get('retweets', 0)}  回复: {meta.get('replies', 0)}",
                ]

            # 作者主页信息 (仅 Twitter/Reddit)
            if not is_youtube:
                profile = t.get("profile")
                if profile:
                    text_parts.append(f"{'─' * 50}")
                    text_parts.append(f"📋 作者主页:")
                    if profile.get("verified"):
                        text_parts.append(f"  ✅ 已认证")
                    text_parts.append(f"  Bio: {profile.get('bio', '?')[:200]}")
                    text_parts.append(f"  粉丝: {profile.get('followers', 0)}  关注: {profile.get('following', 0)}")
                    if profile.get("join_date"):
                        text_parts.append(f"  加入: {profile.get('join_date', '')}")
                    if profile.get("location"):
                        text_parts.append(f"  位置: {profile.get('location', '')}")
                    if profile.get("website"):
                        text_parts.append(f"  网站: {profile.get('website', '')}")

            # 正文内容 (Twitter/Reddit)
            if not is_youtube:
                text_parts.append(f"{'─' * 50}")
                text_parts.append(f"📝 内容:")
                body = content.get("title", "") or "(无内容)"
                text_parts.append(body)

                # 图片
                images = meta.get("images") or []
                if images:
                    text_parts.append(f"{'─' * 50}")
                    text_parts.append(f"🖼 图片 ({len(images)} 张):")
                    for img_url in images:
                        text_parts.append(f"  {img_url}")

            # 评论
            comments = t.get("comments") or []
            if comments:
                text_parts.append(f"{'─' * 50}")
                text_parts.append(f"💬 评论 ({len(comments)} 条):")
                for ci, c in enumerate(comments):
                    cl = c.get('label', '')
                    cl_icon = {"TARGET": "✅", "AD": "📢", "IRRELEVANT": "❌"}.get(cl, "")
                    text_parts.append(
                        f"  [{ci+1}] {cl_icon} @{c.get('author','?')}:"
                    )
                    text_parts.append(f"      {c.get('content','')[:200]}")
                    if c.get('timestamp'):
                        text_parts.append(f"      {c['timestamp']} | 赞:{c.get('likes',0)}")

            detail_text = "\n".join(text_parts)

            self.detail_text.config(state=tk.NORMAL)
            self.detail_text.delete("1.0", tk.END)
            self.detail_text.insert("1.0", detail_text)
            self.detail_text.config(state=tk.DISABLED)

    def _export_json(self):
        """导出归一化结果为 JSON 文件。"""
        if not self.tweets_cache:
            messagebox.showwarning("提示", "没有可导出的结果")
            return
        path = filedialog.asksaveasfilename(
            title="导出 JSON",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
            initialfile=f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        if not path:
            return
        record = {
            "search_term": self.search_var.get(),
            "platform": self.platform_var.get().lower(),
            "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": len(self.tweets_cache),
            "items": self.tweets_cache,
        }
        Path(path).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        self._append_log(f"已导出: {path}", "success")
        messagebox.showinfo("提示", f"已导出到:\n{path}")

    def _llm_filter(self):
        """批量 LLM 过滤 — 一次 API 调用分类全部，失败回退逐条。"""
        if not self.tweets_cache:
            messagebox.showwarning("提示", "没有可过滤的结果")
            return

        # 获取 goal (循环直到用户填写内容或取消)
        while True:
            goal = simpledialog.askstring(
                "LLM 过滤", "请输入过滤目标:\n(例: 筛选真实租户，过滤中介广告)",
                parent=self.root)
            if goal is None:
                return  # 用户点击取消
            if goal.strip():
                break
            messagebox.showwarning("提示", "过滤目标不能为空，请输入筛选条件。")

        self._append_log("正在调用 DeepSeek 进行批量 LLM 过滤...")
        self.status_var.set("LLM 过滤中...")
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.stop_event.clear()

        def _run_filter():
            try:
                api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
                if not api_key:
                    try:
                        api_key = (Path.home() / ".cloakbrowser_deepseek_key").read_text(encoding="utf-8").strip()
                    except FileNotFoundError:
                        api_key = ""
                if not api_key or api_key.startswith("sk-你的"):
                    self.msg_queue.put({"type": "log", "text": "错误: API Key 未配置。请在 .env 设置 DEEPSEEK_API_KEY,或写入 ~/.cloakbrowser_deepseek_key", "level": "error"})
                    self.msg_queue.put({"type": "llm_done"})
                    return

                items = self.tweets_cache  # 已经是归一化格式
                counts = {"TARGET": 0, "AD": 0, "IRRELEVANT": 0, "ERROR": 0}
                labels = [""] * len(items)

                # 第一阶段: 批量调用
                try:
                    prompt = build_unified_batch_prompt(items, goal)
                    raw = call_deepseek(
                        [
                            {"role": "system", "content": "你是一个信息过滤助手。只回复 JSON 数组。"},
                            {"role": "user", "content": prompt},
                        ],
                        api_key=api_key,
                        max_tokens=len(items) * 15 + 20,
                        timeout=60,
                        retries=1,
                    )
                    from _llm import parse_llm_json
                    parsed = parse_llm_json(raw)
                    if isinstance(parsed, list) and len(parsed) == len(items):
                        labels = [normalize_label(str(x)) for x in parsed]
                        self.msg_queue.put({"type": "log", "text": f"  批量分类成功: {len(labels)} 条", "level": "info"})
                    else:
                        self.msg_queue.put({"type": "log", "text": "  ⚠️ 批量返回长度不匹配, 回退逐条", "level": "warn"})
                except Exception as e:
                    self.msg_queue.put({"type": "log", "text": f"  ⚠️ 批量分类异常: {e}, 回退逐条", "level": "warn"})

                # 第二阶段: 逐条兜底
                for i, item in enumerate(items):
                    if self.stop_event.is_set():
                        break
                    if labels[i]:
                        continue
                    try:
                        # 将归一化 item 转回旧格式供 build_classify_prompt 使用
                        fallback_item = {
                            "author_handle": item.get("author", ""),
                            "author_name": item.get("author_name", ""),
                            "tweet_text": item.get("content", {}).get("title", "")[:500],
                            "likes": item.get("meta", {}).get("likes", 0),
                            "replies": item.get("meta", {}).get("replies", 0),
                            "view_count": item.get("meta", {}).get("views", 0),
                            "profile": item.get("profile"),
                            "comments": [
                                {"commenter_handle": c.get("author", ""),
                                 "text": c.get("content", "")}
                                for c in item.get("comments", [])[:10]
                            ],
                        }
                        raw_item = call_deepseek(
                            [
                                {"role": "system", "content": "你是一个信息过滤助手。严格按格式回复。"},
                                {"role": "user", "content": build_classify_prompt(fallback_item, goal)},
                            ],
                            api_key=api_key, max_tokens=10, timeout=30, retries=1,
                        )
                        labels[i] = normalize_label(raw_item)
                    except Exception as e:
                        self.msg_queue.put({"type": "log", "text": f"  [{i+1}] API 调用失败: {e}", "level": "warn"})
                        labels[i] = "ERROR"

                # 应用标签到 items
                labeled = []
                for i, item in enumerate(items):
                    label = labels[i]
                    counts[label] = counts.get(label, 0) + 1
                    item["label"] = label

                    # 评论区也批量分类
                    comments = item.get("comments") or []
                    if comments:
                        batch_prompt = build_comment_batch_prompt(
                            [{"commenter_handle": c.get("author", ""),
                              "text": c.get("content", "")}
                             for c in comments],
                            {"author_handle": item.get("author", ""),
                             "tweet_text": item.get("content", {}).get("title", "")},
                            goal,
                        )
                        try:
                            c_raw = call_deepseek(
                                [
                                    {"role": "system", "content": "你是一个评论过滤助手。只回复 JSON 数组。"},
                                    {"role": "user", "content": batch_prompt},
                                ],
                                api_key=api_key, max_tokens=len(comments) * 15 + 10, timeout=60, retries=1,
                            )
                            c_labels = parse_comment_labels(c_raw, len(comments))
                        except Exception:
                            c_labels = ["ERROR"] * len(comments)
                        c_counts = {"TARGET": 0, "AD": 0, "IRRELEVANT": 0, "ERROR": 0}
                        for ci, c in enumerate(comments):
                            cl = c_labels[ci] if ci < len(c_labels) else "IRRELEVANT"
                            c["label"] = cl
                            c_counts[cl] = c_counts.get(cl, 0) + 1
                        self.msg_queue.put({"type": "log", "text": f"    评论区: {c_counts['TARGET']} 目标 / {c_counts['AD']} 广告 / {c_counts['IRRELEVANT']} 无关", "level": "info"})

                    labeled.append(item)
                    icon = {"TARGET": "✅", "AD": "📢", "IRRELEVANT": "❌", "ERROR": "⚠️"}.get(label, "?")
                    self.msg_queue.put({"type": "log", "text": f"  [{i+1}] {icon} {label} @{item.get('author', '?')}", "level": "info"})

                if self.stop_event.is_set():
                    self.msg_queue.put({"type": "log", "text": f"已中断: {counts['TARGET']} 目标 / {counts['AD']} 广告 / {counts['IRRELEVANT']} 无关 (仅处理 {len(labeled)}/{len(items)} 条)", "level": "warn"})
                else:
                    self.msg_queue.put({"type": "log", "text": f"过滤完成: {counts['TARGET']} 目标 / {counts['AD']} 广告 / {counts['IRRELEVANT']} 无关", "level": "success"})

                if labeled:
                    self.msg_queue.put({"type": "result", "tweets": labeled})

            except Exception as e:
                self.msg_queue.put({"type": "log", "text": f"LLM 过滤失败: {e}", "level": "error"})
            finally:
                self.msg_queue.put({"type": "llm_done"})

        threading.Thread(target=_run_filter, daemon=True).start()

    # ---- 配置编辑对话框 ----

    def _edit_accounts(self):
        fields = [
            {"key": "name", "label": "名称", "width": 20},
            {"key": "email", "label": "邮箱", "width": 30},
            {"key": "username", "label": "用户名", "width": 20},
            {"key": "password", "label": "密码", "width": 30, "show": "*"},
            {"key": "profile_path", "label": "Profile 路径", "width": 35},
        ]
        dlg = EditDialog(self.root, "编辑账号", self.cfg.get("accounts", []), fields, "accounts")
        if dlg.result is not None:
            self.cfg["accounts"] = dlg.result
            save_config(self.cfg)
            self._reload_combos()

    def _edit_proxies(self):
        fields = [
            {"key": "name", "label": "名称", "width": 20},
            {"key": "server", "label": "代理地址 (null=直连)", "width": 50},
        ]
        dlg = EditDialog(self.root, "编辑代理", self.cfg.get("proxies", []), fields, "proxies")
        if dlg.result is not None:
            self.cfg["proxies"] = dlg.result
            save_config(self.cfg)
            self._reload_combos()

    def _edit_fingerprints(self):
        fields = [
            {"key": "name", "label": "名称", "width": 20},
            {"key": "timezone", "label": "时区", "width": 25},
            {"key": "locale", "label": "语言/区域", "width": 15},
            {"key": "color_scheme", "label": "色系 (light/dark)", "width": 15},
            {"key": "viewport_width", "label": "窗口宽度 (0=系统默认)", "width": 10},
            {"key": "viewport_height", "label": "窗口高度 (0=系统默认)", "width": 10},
            {"key": "webrtc_ip", "label": "WebRTC IP (留空=不设)", "width": 20},
        ]
        dlg = EditDialog(self.root, "编辑指纹模板", self.cfg.get("fingerprints", []), fields, "fingerprints")
        if dlg.result is not None:
            self.cfg["fingerprints"] = dlg.result
            save_config(self.cfg)
            self._reload_combos()

    def _edit_extractions(self):
        fields = [
            {"key": "name", "label": "名称", "width": 20},
            {"key": "_sep1", "label": "——— 基本信息 (≈0s，搜索页直接提取) ———", "width": 20},
            {"key": "author_name", "label": "  作者显示名", "width": 15},
            {"key": "author_handle", "label": "  作者 @handle", "width": 15},
            {"key": "tweet_text", "label": "  推文正文", "width": 15},
            {"key": "timestamp", "label": "  发布时间", "width": 15},
            {"key": "tweet_id", "label": "  推文ID (用于去重/进入详情)", "width": 15},
            {"key": "likes", "label": "  点赞数", "width": 15},
            {"key": "retweets", "label": "  转发数", "width": 15},
            {"key": "replies", "label": "  回复数", "width": 15},
            {"key": "images", "label": "  图片URL (≈0s)", "width": 15},
            {"key": "_sep2", "label": "——— 深度提取 (需额外跳转，耗时) ———", "width": 20},
            {"key": "comments", "label": "  评论区内容 (≈5s/推文)", "width": 20},
            {"key": "profile", "label": "  作者主页 (≈5s/作者)", "width": 20},
            {"key": "max_comments_per_tweet", "label": "  每条最大评论数", "width": 10},
        ]
        bool_fields = ["author_name", "author_handle", "tweet_text", "timestamp",
                       "tweet_id", "likes", "retweets", "replies", "images",
                       "comments", "profile"]
        # 分隔符 (_sep1, _sep2) 不参与 bool_fields，渲染为普通文本标签
        dlg = EditDialog(self.root, "编辑提取模板", self.cfg.get("extractions", []),
                         fields, "extractions", bool_fields=bool_fields)
        if dlg.result is not None:
            self.cfg["extractions"] = dlg.result
            save_config(self.cfg)
            self._reload_combos()

    def _toggle_fingerprint(self):
        """切换指纹面板的显示/隐藏。"""
        self._show_fingerprint_panel(not self._fp_visible)

    def _show_fingerprint_panel(self, show: bool):
        """显示或隐藏指纹信息面板。"""
        if not self._fp_frame or not self._fp_paned:
            return
        self._fp_visible = show
        if show:
            self._fp_paned.add(self._fp_frame, weight=1)
        else:
            self._fp_paned.forget(self._fp_frame)
        self.fp_toggle_btn.config(text="🔍 指纹" if not show else "✕ 隐藏指纹")

    def _on_platform_changed(self, _evt=None):
        """切换平台时显示/隐藏特有配置、适配表格列头。"""
        platform = self.platform_var.get()
        is_reddit = platform == "Reddit"
        is_youtube = platform == "YouTube"

        # Reddit 配置
        if is_reddit:
            self.reddit_config_frame.pack(side=tk.LEFT, padx=(10, 0))
        else:
            self.reddit_config_frame.pack_forget()

        # YouTube 配置
        if is_youtube:
            self.youtube_config_frame.pack(side=tk.LEFT, padx=(10, 0))
            if os.environ.get("YOUTUBE_API_KEY"):
                remaining = get_quota_remaining()
                self.quota_var.set(f"API 配额: {remaining:,}/10,000")
            else:
                self.quota_var.set("(无 API Key, 使用 yt-dlp 搜索)")
        else:
            self.youtube_config_frame.pack_forget()
            self.quota_var.set("")

        # 方案/无头: YouTube 不需要
        if is_youtube:
            self.bundle_combo.config(state=tk.DISABLED)
            self.bundle_summary_var.set("(YouTube 模式: 不需要浏览器/代理/指纹)")
            self.headless_var.set(False)
            # 隐藏方案区域和无头复选框 (P1-8, P1-9)
            self.headless_checkbox.pack_forget()
        else:
            self.bundle_combo.config(state="readonly")
            self._on_bundle_changed()
            if is_reddit:
                self.headless_var.set(False)
            # 恢复无头复选框
            if not self.headless_checkbox.winfo_ismapped():
                self.headless_checkbox.pack(side=tk.LEFT, padx=(10, 0))

        # 表格列头 + 详情面板标签 (P0-2, P0-3)
        self._reconfigure_columns(platform)
        detail_titles = {
            "Twitter": "推文详情 (单击表格行查看)",
            "Reddit":  "帖子详情 (单击表格行查看)",
            "YouTube": "视频详情 (单击表格行查看)",
        }
        self.detail_frame.config(text=detail_titles.get(platform, "详情 (单击表格行查看)"))

        # Spinbox 上限 (P0-5): YouTube 最多 500
        self.max_spinbox.config(to=500 if is_youtube else 100)
        if is_youtube and self.max_var.get() > 500:
            self.max_var.set(100)

        # 指纹面板: YouTube 模式隐藏 (P1-6)
        if is_youtube:
            self._show_fingerprint_panel(False)
            self.fp_toggle_btn.pack_forget()
        else:
            self.fp_toggle_btn.pack(side=tk.RIGHT, padx=2)
            self._show_fingerprint_panel(self._fp_visible)

    def _on_bundle_changed(self, _evt=None):
        """方案切换时更新摘要信息。"""
        bundle_name = self.bundle_var.get()
        bundle = next((b for b in self.cfg.get("bundles", []) if b["name"] == bundle_name), None)
        if not bundle:
            self.bundle_summary_var.set("")
            return

        account = bundle.get("account", "?")
        proxy = bundle.get("proxy", "?")
        fp = bundle.get("fingerprint", "?")
        extr = bundle.get("extraction", "?")
        created = bundle.get("usage_created", "?")
        count = bundle.get("usage_count", 0)
        last = bundle.get("usage_last") or "从未"

        summary = f"账号:{account} | 代理:{proxy} | 指纹:{fp} | 提取:{extr} | 创建:{created} | 使用:{count}次 | 上次:{last}"
        self.bundle_summary_var.set(summary)

    def _edit_bundles(self):
        """编辑方案：绑定/替换账号、代理、指纹、提取。"""
        account_names = [a["name"] for a in self.cfg.get("accounts", [])]
        proxy_names = [p["name"] for p in self.cfg.get("proxies", [])]
        fp_names = [f["name"] for f in self.cfg.get("fingerprints", [])]
        extr_names = [e["name"] for e in self.cfg.get("extractions", [])]

        dlg = tk.Toplevel(self.root)
        dlg.title("编辑方案")
        dlg.geometry("550x450")
        dlg.transient(self.root)
        dlg.grab_set()

        # 列表区
        list_frame = ttk.LabelFrame(dlg, text="当前方案", padding=5)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        lb = tk.Listbox(list_frame, height=6, width=50)
        lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=lb.yview)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        lb.config(yscrollcommand=sc.set)

        def refresh_list():
            lb.delete(0, tk.END)
            for b in self.cfg.get("bundles", []):
                cnt = b.get("usage_count", 0)
                last = (b.get("usage_last") or "从未")[:10]
                lb.insert(tk.END, f"{b['name']}  (使用{cnt}次, 上次{last})")

        refresh_list()

        # 编辑区
        edit_frame = ttk.LabelFrame(dlg, text="编辑", padding=5)
        edit_frame.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(edit_frame, text="名称:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        name_var = tk.StringVar()

        ttk.Entry(edit_frame, textvariable=name_var, width=30).grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)

        ttk.Label(edit_frame, text="账号:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        account_var = tk.StringVar()
        ttk.Combobox(edit_frame, textvariable=account_var, values=account_names, width=27, state="readonly").grid(row=1, column=1, sticky=tk.EW, padx=5, pady=2)

        ttk.Label(edit_frame, text="代理:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        proxy_var = tk.StringVar()
        ttk.Combobox(edit_frame, textvariable=proxy_var, values=proxy_names, width=27, state="readonly").grid(row=2, column=1, sticky=tk.EW, padx=5, pady=2)

        ttk.Label(edit_frame, text="指纹:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=2)
        fp_var = tk.StringVar()
        ttk.Combobox(edit_frame, textvariable=fp_var, values=fp_names, width=27, state="readonly").grid(row=3, column=1, sticky=tk.EW, padx=5, pady=2)

        ttk.Label(edit_frame, text="提取:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=2)
        extr_var = tk.StringVar()
        ttk.Combobox(edit_frame, textvariable=extr_var, values=extr_names, width=27, state="readonly").grid(row=4, column=1, sticky=tk.EW, padx=5, pady=2)

        ttk.Label(edit_frame, text="创建日期:").grid(row=5, column=0, sticky=tk.W, padx=5, pady=2)
        created_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(edit_frame, textvariable=created_var, width=30).grid(row=5, column=1, sticky=tk.EW, padx=5, pady=2)

        def on_select(evt):
            sel = lb.curselection()
            if not sel:
                return
            b = self.cfg["bundles"][sel[0]]
            name_var.set(b.get("name", ""))
            account_var.set(b.get("account", ""))
            proxy_var.set(b.get("proxy", ""))
            fp_var.set(b.get("fingerprint", ""))
            extr_var.set(b.get("extraction", ""))
            created_var.set(b.get("usage_created", datetime.now().strftime("%Y-%m-%d")))
        lb.bind("<<ListboxSelect>>", on_select)

        def do_add():
            name = name_var.get().strip()
            if not name:
                return
            new_b = {
                "name": name,
                "account": account_var.get(),
                "proxy": proxy_var.get(),
                "fingerprint": fp_var.get(),
                "extraction": extr_var.get(),
                "usage_created": created_var.get(),
                "usage_count": 0,
                "usage_last": None,
            }
            self.cfg["bundles"].append(new_b)
            save_config(self.cfg)
            refresh_list()
            self._reload_combos()

        def do_replace():
            sel = lb.curselection()
            if not sel:
                return
            b = self.cfg["bundles"][sel[0]]
            # 只替换组件，保留使用统计
            b["account"] = account_var.get()
            b["proxy"] = proxy_var.get()
            b["fingerprint"] = fp_var.get()
            b["extraction"] = extr_var.get()
            name_var_val = name_var.get().strip()
            if name_var_val:
                b["name"] = name_var_val
            save_config(self.cfg)
            refresh_list()
            self._reload_combos()

        def do_delete():
            sel = lb.curselection()
            if not sel:
                return
            if messagebox.askyesno("确认", f"删除方案 '{self.cfg['bundles'][sel[0]]['name']}' 吗？"):
                del self.cfg["bundles"][sel[0]]
                save_config(self.cfg)
                refresh_list()
                self._reload_combos()

        def do_login():
            """在后台线程中启动隐身浏览器并执行登录。"""
            sel = lb.curselection()
            if not sel:
                messagebox.showwarning("提示", "请先在左侧列表点击选中一个方案（会高亮），再点登录")
                return
            b = self.cfg["bundles"][sel[0]]
            account_name = b.get("account", "")
            proxy_name = b.get("proxy", "")
            fp_name = b.get("fingerprint", "")

            account = next((a for a in self.cfg.get("accounts", []) if a["name"] == account_name), None)
            proxy_info = next((p for p in self.cfg.get("proxies", []) if p["name"] == proxy_name), None)
            fp = next((f for f in self.cfg.get("fingerprints", []) if f["name"] == fp_name), {})

            if not account:
                messagebox.showwarning("提示", "未找到对应账号")
                return
            email = account.get("email", "")
            username = account.get("username", "")
            password = resolve_account_password(account)
            profile = account.get("profile_path", f"./twitter-profiles/{account['name']}")
            proxy_server = proxy_info.get("server") if proxy_info else None

            if not email and not username:
                messagebox.showwarning("提示", "账号未配置邮箱或用户名")
                return
            if not password:
                messagebox.showwarning("提示", "账号未配置密码")
                return

            # 确认
            if not messagebox.askyesno("确认登录",
                f"将打开浏览器窗口登录:\n"
                f"  账号: {account_name}\n"
                f"  代理: {proxy_name}\n"
                f"  指纹: {fp_name}\n"
                f"  Profile: {profile}\n\n"
                f"点击'是'开始登录。"):
                return

            # 在后台线程执行
            def _login_thread():
                self._append_log(f"🔄 正在为 {account_name} 执行登录...")
                try:
                    from cloakbrowser import launch_persistent_context

                    # 解析 viewport
                    vp_w = fp.get("viewport_width")
                    vp_h = fp.get("viewport_height")
                    try:
                        vp_w = int(vp_w) if vp_w else 0
                        vp_h = int(vp_h) if vp_h else 0
                    except (ValueError, TypeError):
                        vp_w, vp_h = 0, 0
                    viewport = {"width": vp_w, "height": vp_h} if (vp_w > 0 and vp_h > 0) else None

                    # WebRTC IP
                    extra_args = []
                    webrtc_ip = fp.get("webrtc_ip")
                    if webrtc_ip and str(webrtc_ip).strip() and str(webrtc_ip).lower() not in ("none", "null", "false"):
                        extra_args.append(f"--fingerprint-webrtc-ip={webrtc_ip}")

                    ctx = launch_persistent_context(
                        str(profile),
                        headless=False,
                        proxy=proxy_server,
                        humanize=True,
                        human_preset="careful",
                        timezone=fp.get("timezone", "Asia/Seoul"),
                        locale=fp.get("locale", "ko-KR"),
                        color_scheme=fp.get("color_scheme", "dark"),
                        viewport=viewport,
                        args=extra_args if extra_args else None,
                    )
                    page = ctx.new_page()

                    try:
                        # 先看是否已登录
                        page.goto("https://x.com", wait_until="domcontentloaded", timeout=30000)
                        time.sleep(3)
                        already_logged = page.evaluate(
                            "!!document.querySelector('[data-testid=\"primaryColumn\"]')")
                        if already_logged:
                            self._append_log(f"✅ {account_name} 已登录，无需重复", "success")
                            pass  # 登录成功
                            return

                        # 跳转到登录页
                        self._append_log(f"  → 跳转登录页...")
                        page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded", timeout=30000)
                        time.sleep(4)

                        login_handle = username or email

                        # 填用户名
                        page.locator('input[name="username_or_email"]').first.click()
                        time.sleep(random.uniform(0.2, 0.6))
                        page.keyboard.type(login_handle)
                        self._append_log(f"  ⌨️  已输入用户名")
                        time.sleep(random.uniform(0.3, 0.8))
                        page.keyboard.press("Enter")
                        time.sleep(5)

                        # 检查是否需要二次用户名
                        pwd_visible = page.locator('input[name="password"]').first.is_visible()
                        if not pwd_visible and username:
                            try:
                                still_there = page.locator('input[name="username_or_email"]').first.is_visible(timeout=5000)
                                if still_there:
                                    page.locator('input[name="username_or_email"]').first.click()
                                    time.sleep(random.uniform(0.2, 0.5))
                                    page.keyboard.type(username)
                                    self._append_log(f"  ⌨️  已输入二次用户名")
                                    time.sleep(random.uniform(0.3, 0.8))
                                    page.keyboard.press("Enter")
                                    time.sleep(5)
                            except Exception:
                                pass

                        # 填密码
                        page.locator('input[name="password"]').first.click()
                        time.sleep(random.uniform(0.3, 0.8))
                        page.keyboard.type(password)
                        self._append_log(f"  ⌨️  已输入密码")
                        time.sleep(random.uniform(0.3, 0.8))
                        page.keyboard.press("Enter")
                        time.sleep(5)

                        # 检测结果
                        body_text = page.evaluate("() => (document.body?.innerText || '')")

                        if "两步验证" in body_text or "Verification code" in body_text or "验证码" in body_text:
                            self._append_log(f"  ⚠️ 需要 2FA，请在浏览器窗口中手动输入验证码", "warn")
                            self._append_log(f"  ⏳ 等待 2FA 完成（最多 2 分钟）...")
                            for _ in range(40):
                                time.sleep(3)
                                logged = page.evaluate(
                                    "!!document.querySelector('[data-testid=\"primaryColumn\"]')")
                                if logged:
                                    self._append_log(f"✅ {account_name} 登录成功！", "success")
                                    pass  # 登录成功
                                    return
                            self._append_log(f"  ⏰ 2FA 超时", "warn")

                        elif "密码不正确" in body_text or "Wrong password" in body_text:
                            self._append_log(f"  ❌ {account_name} 密码错误", "error")

                        else:
                            logged_final = page.evaluate(
                                "!!document.querySelector('[data-testid=\"primaryColumn\"]')")
                            if logged_final:
                                self._append_log(f"✅ {account_name} 登录成功！", "success")
                                pass  # 登录成功
                            else:
                                self._append_log(f"  ⚠️ {account_name} 登录状态未知，请检查浏览器", "warn")

                    finally:
                        ctx.close()

                except Exception as e:
                    self._append_log(f"❌ {account_name} 登录失败: {e}", "error")
                    traceback.print_exc()

            threading.Thread(target=_login_thread, daemon=True).start()

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        ttk.Button(btn_frame, text="新增", command=do_add).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="替换组件(保留统计)", command=do_replace).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="删除", command=do_delete).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="🔑 登录此账号", command=do_login).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Button(btn_frame, text="关闭", command=dlg.destroy).pack(side=tk.RIGHT, padx=2)

        dlg.wait_window()

    def _manage_components(self):
        """管理独立组件：账号、代理、指纹、提取（编辑/新增/删除）。"""
        component_tabs = [
            ("账号", self._edit_accounts),
            ("代理", self._edit_proxies),
            ("指纹", self._edit_fingerprints),
            ("提取", self._edit_extractions),
        ]

        dlg = tk.Toplevel(self.root)
        dlg.title("管理组件")
        dlg.geometry("500x60")
        dlg.transient(self.root)

        frame = ttk.Frame(dlg, padding=10)
        frame.pack(fill=tk.X)

        ttk.Label(frame, text="独立编辑各类组件（不会影响方案的使用统计）:", font=("", 9, "bold")).pack(anchor=tk.W, pady=(0, 5))

        for label, handler in component_tabs:
            ttk.Button(frame, text=f"编辑{label}", command=handler, width=18).pack(
                side=tk.LEFT, padx=2)

        ttk.Button(frame, text="关闭", command=dlg.destroy).pack(side=tk.RIGHT, padx=2)
        dlg.wait_window()

    def _reload_combos(self):
        """重新加载方案下拉框数据。"""
        self.bundle_combo["values"] = [b["name"] for b in self.cfg.get("bundles", [])]

    def _on_close(self):
        """窗口关闭时的清理。"""
        if self.worker and self.worker.is_alive():
            if messagebox.askyesno("确认", "任务正在运行中，确定要退出吗？"):
                self.stop_event.set()
                self.worker.join(timeout=5)
        self._stop_forwarder()
        self.root.destroy()


# ---- 入口 ----

def main():
    root = tk.Tk()
    ConsoleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
