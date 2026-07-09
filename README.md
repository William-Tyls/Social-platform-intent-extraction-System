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
python tools/apps/youtube_run.py "{搜索关键词}" -n 100 -c 10

# YouTube: 直接提取已知视频
python tools/apps/youtube_run.py --ids "{视频ID}" -c 20

# Twitter: 搜索推文 (需先登录 + 启动代理)
python tools/apps/twitter_test.py                 # 1. 登录建立会话
python tools/apps/socks5_forwarder.py              # 2. 启动代理转发
python tools/apps/twitter_search_test.py "{关键词}"  # 3. 搜索

# Reddit: 同理, 通过 console.py 选择平台后搜索

# LLM 过滤: 对采集结果分类
python tools/apps/llm_filter.py "{结果文件.json}" --goal "{筛选目标}"
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

本项目构建在 [CloakBrowser](https://github.com/CloakHQ/CloakBrowser) 之上，一个经过 58 个 C++ 源码级补丁的隐身 Chromium 浏览器 (v0.3.31, Chrome/145)。我们在其基础上做了性能和安全性增强，以下是优化结果。

### 指标提升

| 检测项 | 基线 | 优化后 | 说明 |
|---|---|---|---|
| **CreepJS likeHeadless** | 31% (5 fails) | **0%** (0 fails) | API polyfill 修正 + 颜色信号消除 |
| **fingerprint-scan hd_fails** | 3 | **0** | 随 CreepJS 修复自动消除 |
| **ShieldSquare** | FAIL | **PASS** | 代理 IP 信誉诊断修复 |

> 基线为 2026-06-26 美国住宅代理实测数据。其他 7 项检测 (bot.sannysoft, BrowserScan, CF Turnstile, reCAPTCHA v3 等) 优化前后均保持通过，详见[完整文档](docs/cloakbrowser-optimization-plan.md)。FingerprintJS BLOCKED 待 binary 层修复。

### 我们做的优化

| 层级 | 优化项 | 改动 |
|---|---|---|
| **指纹安全性** | CSPRNG 随机种子 | `random.randint` (MT19937) → `secrets.randbelow`，消除指纹序列可预测性 |
| | 延迟加载 | `js/config.ts` import 时不再同步读取 `package.json`，减小冷启动指纹面 |
| **CreepJS 31%→0%** | API polyfill 修正 | 旧 polyfill 注入目标错误 (`ServiceWorkerRegistration.prototype.index` 等)。通过逆向 `creep.js` 源码定位到真实检测点 → 修正为 `window.ContentIndex`、`window.ContactsManager`、`NetworkInformation.prototype.downlinkMax` |
| | 颜色信号消除 | `color_scheme="dark"` 消除 `prefersLightColor`；CSS 注入 `background-color: inherit` 消除 `hasKnownBgColor` |
| **行为拟人化** | 对数正态分布 | `rand_lognormal()` 替代均匀分布的鼠标移动/滚动间隔，贴近真实人类行为统计特征 |
| | 阅读行为模拟 | 30% 概率随机停顿 3-8s、15% 概率回滚 100-300px，模拟真实用户阅读节奏 |
| | CDP 隔离世界 | 特殊字符用 `Input.dispatchKeyEvent` 替代 `page.evaluate`，`isTrusted=true` 不触发伪造检测 |
| **ShieldSquare** | 代理 IP 诊断 | 确认 PASS/FAIL 差异来自代理 IP 信誉而非指纹，消除误报 |

### 待解决 (需 binary 层配合)

**FingerprintJS (L3)** 和 **PixelScan inconsistent** 共享同一根因：CloakBrowser binary 未修补 `chrome.runtime`、`navigator.plugins`(仅5)、`navigator.mimeTypes`(仅2)、`navigator.userAgentData`(macOS 缺失)。这些 C++ 层的 V8 native getter 无法用 JavaScript polyfill 覆盖，需 binary 团队在编译层注入 Compat API。

[查看完整优化文档](docs/cloakbrowser-optimization-plan.md)

## License

本项目代码 (tools/) 为 MIT License。底层 CloakBrowser 引擎的许可证详见 [BINARY-LICENSE.md](BINARY-LICENSE.md)。
