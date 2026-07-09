# Social Platform Intent Extraction System

多平台社交数据意图提取系统 — 基于 CloakBrowser 隐身浏览器构建，支持 **YouTube** / **Twitter/X** / **Reddit** 三大平台的用户内容批量采集和 LLM 智能过滤。

---

## 架构

```
┌──────────────────────────────────────────────┐
│                  console.py                  │
│         三平台 GUI (Tkinter)                  │
│     实时进度 / 指纹面板 / LLM 过滤 / JSON 导出  │
└──────┬───────────────┬───────────────┬───────┘
       │               │               │
       ▼               ▼               ▼
  TwitterWorker   RedditWorker   YoutubeWorker
       │               │               │
       │               │               ├─ YouTube API (搜索发现)
       │               │               └─ yt-dlp (内容提取)
       │               │
       │               └─ CloakBrowser (隐身浏览器)
       └─ CloakBrowser (隐身浏览器 + 登录态)

提取层 (纯函数, 可独立使用):
  extractors/
  ├── twitter_extractor.py   — Twitter/X DOM 提取
  ├── reddit_extractor.py    — old.reddit.com HTML 解析
  ├── youtube_api.py         — 官方 API v3 + 配额追踪
  ├── youtube_ytdlp.py       — yt-dlp 提取 (零配额)
  └── youtube_dedup.py       — SQLite 跨进程去重
```

## 功能

| 平台 | 模式 | 获取数据 | 依赖 |
|---|---|---|---|
| **YouTube** | API + yt-dlp | 标题、摘要、播放量、点赞、评论(含评论人) | API Key + yt-dlp |
| **Twitter/X** | CloakBrowser | 推文、评论、作者主页 | CloakBrowser + 代理 + 登录态 |
| **Reddit** | CloakBrowser | 帖子、评论、用户 karma | CloakBrowser + 代理 |

**LLM 智能过滤**：基于 DeepSeek，对采集到的帖子/视频/评论自动分类（目标 / 广告 / 无关），支持跨平台使用。

## 快速开始

```bash
# 1. 安装依赖
pip install cloakbrowser yt-dlp google-api-python-client

# 2. 配置环境变量
cp tools/.env.example tools/.env
# 编辑 tools/.env, 填入:
#   YOUTUBE_API_KEY=xxx       # YouTube 搜索需要
#   DEEPSEEK_API_KEY=xxx      # LLM 过滤需要
#   CB_TWITTER_PROXY=xxx      # Twitter/Reddit 需要

# 3. 启动控制台
python tools/console.py
```

### CLI 模式

```bash
# YouTube: 搜索 100 个视频 + 每条 10 个评论
python tools/apps/youtube_run.py "World Cup" -n 100 -c 10

# YouTube: 直接提取已知视频
python tools/apps/youtube_run.py --ids dQw4w9WgXcQ -c 20

# Twitter: 搜索推文
python tools/apps/twitter_search_test.py "关键词"

# LLM 过滤: 对采集结果分类
python tools/apps/llm_filter.py results.json --goal "筛选想购买世界杯门票的用户"
```

## 目录结构

```
tools/
├── console.py                ← 三平台 GUI 主应用
├── _env.py                   ← .env 加载器
├── _llm.py                   ← LLM prompt + API 共享模块
├── config.json               ← GUI 配置 (方案/账号/代理/指纹)
│
├── extractors/               ← 纯数据提取层 (无 GUI 依赖)
│   ├── twitter_extractor.py
│   ├── reddit_extractor.py
│   ├── youtube_api.py        ← YouTube Data API v3 + QuotaTracker
│   ├── youtube_ytdlp.py      ← yt-dlp 提取
│   └── youtube_dedup.py      ← SQLite 去重
│
├── apps/                     ← CLI 命令行工具
│   ├── youtube_run.py
│   ├── twitter_search_test.py
│   ├── twitter_test.py       ← 登录工具
│   ├── llm_filter.py
│   └── socks5_forwarder.py   ← SOCKS5 代理转发
│
├── tests/                    ← 测试套件
│   ├── stealth_test.py
│   ├── fingerprint_scan_test.py
│   └── test_llm_filter.py
│
├── tutorials/                ← 入门示例
│   ├── basic.py
│   └── persistent_context.py
│
└── integrations/             ← 框架集成示例
    ├── aws_lambda/
    ├── browser_use_example.py
    ├── crawl4ai_example.py
    ├── crawlee_example.py
    ├── langchain_loader.py
    ├── selenium_example.py
    └── undetected_chromedriver.py
```

## YouTube 两层架构

```
发现层 (API):  youtube_api.py
  search.list → video_id 列表
  配额: 100 units/页 (每天 10,000)
  带跨进程持久化配额追踪 (QuotaTracker + fcntl.flock)

提取层 (yt-dlp): youtube_ytdlp.py
  video_id → 标题/摘要/评论/评论人信息
  零配额, 零代理, 完整评论树
```

## 底层引擎: CloakBrowser

本项目构建在 [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) 之上，一个经过 58 个 C++ 源码级补丁的隐身 Chromium 浏览器：

- **0.9 reCAPTCHA v3 分数** — 人类级别
- **Cloudflare Turnstile / FingerprintJS / BrowserScan 均通过**
- **humanize=True** — 贝塞尔鼠标轨迹、真实键盘节奏、自然滚动模式
- **自动指纹随机化** — 每次启动生成新的指纹组合
- **MIT 许可** (包装代码) + 二进制免费内部使用

```python
from cloakbrowser import launch
browser = launch(headless=False, proxy="socks5://...", humanize=True)
page = browser.new_page()
# 对反爬系统而言，这就是一个真实的用户浏览器
```

## Twitter/Reddit 使用说明

1. 先运行登录脚本建立会话：
   ```bash
   python tools/apps/twitter_test.py
   ```
2. 启动 SOCKS5 代理转发器：
   ```bash
   python tools/apps/socks5_forwarder.py
   ```
3. 然后在 console.py 中选择平台和方案，开始搜索提取。

## License

本项目代码 (tools/) 为 MIT License。底层 CloakBrowser 引擎的许可证详见 [BINARY-LICENSE.md](BINARY-LICENSE.md)。
