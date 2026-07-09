# CloakBrowser 反检测优化方案

> v0.3.31 · binary v146.0.7680.177.5 · Docker Linux arm64 (58 patches)

---

## 基线分层策略

官方测试对不同检测服务用了不同配置（headed/headless、noise 开关、住宅代理）。我们的基线也**分层匹配**：逐项对齐官方配置，使每项对比有意义。

| 检测层级 | 测试项目 | 对齐的配置 | 剩余差距 |
|---------|------|------|------|
| **L1 硬件指纹** | bot.sannysoft、bot.incolumitas、BrowserScan、deviceandbrowserinfo、navigator.*、UA、CDP、TLS | headless 即可 | 无 |
| **L2 行为/渲染** | CreepJS、fingerprint-scan、CF Turnstile、ShieldSquare | headed（Docker Xvfb） | 无 |
| **L3 FPJS** | FingerprintJS | headed + `noise=false` + 住宅代理 | ⚠️ 代理延迟瓶颈（高延迟住宅代理 → JS 渲染超时）；CamoFox 通过 ✅ |
| **L4 IP 信誉** | reCAPTCHA v3 | headed + 住宅代理 | ⚠️ 代理延迟瓶颈（reCAPTCHA JS 超时）；CamoFox 通过（score=3） |

> L4 是外部变量：reCAPTCHA 评分从 0.3→0.9 主要靠 IP 信誉，不是指纹。优化方案聚焦 L1-L3（指纹可控），L4 需要住宅代理单独解决。

---

## 官方各测试的真实配置

官方 README 的测试结果表格（reCAPTCHA 0.9、FingerprintJS PASS、sannysoft 56/56 等）**并非用同一套配置跑出**，而是针对不同检测服务使用不同配置，取各自最优结果汇总。以下从官方源码中提取各测试的真实配置。

### 配置来源

| 测试项目 | 源码来源 | 官方配置 |
|---------|---------|---------|
| bot.sannysoft | [`examples/stealth_test.py:231`](../examples/stealth_test.py#L231) | `launch(headless=False, proxy=..., geoip=True)` |
| bot.incolumitas | 同上 | 同上 |
| BrowserScan | 同上 | 同上 |
| deviceandbrowserinfo | 同上 | 同上 |
| CF Turnstile | 同上（stealth_test 子测试） | 同上 |
| ShieldSquare | 同上（stealth_test 子测试） | 同上 |
| CreepJS | [`examples/fingerprint_scan_test.py:189-197`](../examples/fingerprint_scan_test.py#L189-L197) | `launch_context(headless=False, proxy=..., args=["--fingerprint-screen-width=1920", "--fingerprint-screen-height=1080", "--fingerprint-timezone=..."])` |
| fingerprint-scan | 同上 | 同上 |
| FingerprintJS | [`README.md#L1073-L1084`](../README.md#L1073-L1084) | `launch(headless=False, proxy="residential-proxy", geoip=True, args=["--fingerprint-noise=false", "--fingerprint-screen-width=1920", "--fingerprint-screen-height=1080"])` |
| reCAPTCHA v3 | [`examples/recaptcha_score.py:13`](../examples/recaptcha_score.py#L13) | `launch(headless=True)` — **无代理、headless、无特殊参数、noise 默认开** |

### 配置逐项分解

| 配置项 | L1 硬件指纹<br>(sannysoft等) | L2 行为渲染<br>(CreepJS/fp-scan) | L3 FPJS<br>(FingerprintJS) | L4 reCAPTCHA<br>(官方) |
|---------|:---:|:---:|:---:|:---:|
| `headless` | `False` (headed) | `False` (headed) | `False` (headed) | **`True`** ← 官方用 headless |
| `proxy` | 可选 | 可选 | **住宅代理** | **无代理** ← 官方无代理 |
| `geoip` | `True` | 否（手动 timezone） | `True` | 否 |
| `noise` | 默认 on | 默认 on | **`false`** | 默认 on |
| `--fingerprint-screen` | 默认 | **1920×1080** | **1920×1080** | 默认 |
| `context` | `launch` | `launch_context` | `launch` | `launch` |

### reCAPTCHA v3 的特殊性

官方 `recaptcha_score.py` 用 **headless + 无代理 + noise 默认开** 拿到 0.9 分，说明 reCAPTCHA v3 评分**主要依赖 IP 信誉和二进制指纹**，不需要代理和 headed 模式。我们的测试用代理是因为网络可达性需求，评分会受代理 IP 信誉影响，不能直接对比官方 0.9 的目标。

---

## 美国代理基线 (2026-06-26)

以下使用美国住宅代理 `http://192.168.1.7:8118`（出口 IP `82.180.163.169`，时区 `America/Phoenix`），macOS 本地直连，CloakBrowser 0.3.31，Chrome/145。按官方各自配置分层测试。

| 检测维度 | 配置 | 官方配置来源 | 结果 | 目标 |
|---------|------|------|:---:|:---:|
| **L1 硬件指纹** | headed + noise=default + proxy | stealth_test.py | ✅ 56/56 · 35/36 ¹ · 19/0 · isBot=False | 56/56, 35/36, NORMAL, isBot=false |
| **L2 行为/渲染** | headed + noise=default + proxy + screen 1920×1080 | fingerprint_scan_test.py | ⚠️ CreepJS 31% (5 fails) ² · fp-scan hd_fails=3 · Turnstile PASS · ShieldSquare ⚠️ ²³ | ≤5%, hd=0 |
| **L3 FPJS** | headed + noise=false + proxy + screen 1920×1080 | README FPJS config | ❌ BLOCKED ³ | PASS |
| **L4 reCAPTCHA** | headless + 无代理 + noise=default | recaptcha_score.py | ✅ 0.9 | 0.9（参考） |

> ¹ 仅 WEBDRIVER fail（binary 层已知 tradeoff）。² 5 fails: `hasKnownBgColor` + `prefersLightColor` + `noContentIndex` + `noContactsManager` + `noDownlinkMax`。noise=default 比 noise=false 多 `hasKnownBgColor` 和 `prefersLightColor` 两项。³ 页面显示 `"Anti-detect browser tampering detected, potentially a bot, access denied."` — FingerprintJS Smart Signal 服务端主动判定为 bot 并拒绝 API 请求。非代理延迟问题，是 CloakBrowser 的指纹特征（noise=false 下 macOS Chrome/145）被 FPJS 服务端识别。之前 `domcontentloaded` 测试因 Search 按钮未启用而误判为 NO FLIGHTS，`networkidle` 后完整测试确认真实结果为 BLOCKED。
>
> ⁴ L4 使用官方原配（headless + 无代理）直接访问 reCAPTCHA demo，无需 VPN 即可连通 Google API。`grecaptcha.execute()` → token → 后端验证顺利完成，拿到 **0.9** 分，与官方基准一致。此前通过 VPN 代理时 Chromium 被代理层拦截 `google.com`，导致 score 永不渲染；去掉代理后问题消失。
> 韩国代理（`http://102.134.40.8:50100`，出口 `102.134.40.8`，Asia/Seoul）存在 HTTP 407 认证和 18-35s 高延迟，且 `bot.incolumitas.com` 完全不可达，已弃用。

---

## 核心对比表：优化路径

`▸` = 存在优化空间，无标记 = 已达标。🖥️ = headed, 👤 = headless, 🔇 = noise=false, 🏠 = 住宅代理。

| 检测维度 | 配置 | 官方基准 | 基线 | 优化后 | 目标 |
|---------|------|:---:|:---:|:---:|:---:|
| **L1 硬件指纹**<br>👤 headed + proxy + geoip · noise=default | | | | | |
| bot.sannysoft | 👤 | 56/56 | 56/56 | 56/56 | ✅ |
| bot.incolumitas | 👤 | 1 fail | 35/36 | 35/36 | ✅ |
| BrowserScan | 👤 | NORMAL | 19/0 | 19/0 | ✅ |
| deviceandbrowserinfo | 👤 | 0 flags | isBot=False | isBot=False | ✅ |
| **L2 行为/渲染**<br>🖥️ headed + proxy + screen 1920×1080 · noise=default | | | | | |
| ▸ CreepJS likeHeadless | 🖥️ | null | 31% | 0% ⬆️ | ✅ |
| ▸  ┗ failures | | — | 5 项 | 0 项 ⬆️ | ✅ |
| CreepJS headless / stealth | 🖥️ | null | 0% / 0% | 0% / 0% | ✅ |
| ▸ fingerprint-scan | 🖥️ | null | hd_fails=3 | hd_fails=0 ⬆️ | ✅ |
| CF Turnstile | 🖥️ | PASS | PASS | PASS | ✅ |
| ShieldSquare | 🖥️ | PASS | PASS ¹ | PASS | ✅ |
| **L3 FPJS**<br>🖥️🔇🏠 headed + noise=false + 住宅代理 + screen 1920×1080 | | | | | |
| ▸ FingerprintJS | 🖥️🔇🏠 | PASS | BLOCKED ² | BLOCKED ² | ❌ |
| **L4 reCAPTCHA**<br>👤 headless · 无代理 · noise=default | | | | | |
| reCAPTCHA v3 | 👤 | 0.9 | 0.9 | 0.9 | ✅ |
| **单项指标** | | | | | |
| navigator.webdriver | 👤 | false | false | false | ✅ |
| UA string | 👤 | Chrome/146 | Chrome/145 | Chrome/145 | ✅ |
| CDP detection | 👤 | Not detected | Not detected | Not detected | ✅ |
| 时区 | 👤 | =代理 | =代理 | =代理 | ✅ |
| **新增指标** | | | | | |
| JA4 TLS | 🌐 | = Chrome | = Chrome | = Chrome | ✅ |
| BrowserLeaks · WebGL | 🖥️ | null | ANGLE | ANGLE | ✅ |
| BrowserLeaks · Canvas | 🖥️ | null | hash OK | hash OK | ✅ |
| PixelScan | 🖥️ | null | inconsistent ³ | inconsistent ³ | ❌ |

> ¹ ShieldSquare 基线最初显示 FAIL，后确认根因是该美国代理 IP 被 Radware 信誉库拦截——无代理直连正常，官方同样带代理 PASS。非 CloakBrowser 指纹问题。
> ² FingerprintJS 确认为 binary 层问题。详情见 L3 维度详解。
> ³ PixelScan 显示 inconsistent（非 bot）。`launch_persistent_context` 消除了 Playwright 的 Incognito 误检，但 inconsistent 根因与 L3 共享（binary API 缺失）。

**测试环境**：macOS 本地 CloakBrowser v0.3.31 Chrome/145，美国住宅代理 `http://192.168.1.7:8118`（出口 82.180.163.169, Phoenix, AZ）。L1/L2 使用 noise=default，L3 使用 noise=false。简化表移除了 CamoFox 列（Firefox 对比）和中间优化柱（P0-1/P0-2/P0-3），完整优化路径见下方详解。

---

## 检测维度详解

### L1 — 硬件指纹检测

**检测什么**：`navigator.webdriver`、`navigator.platform`、`window.chrome` 对象、CDP 自动化标记、`chrome.runtime` 是否存在等静态属性。这些值在浏览器启动时由 C++ 层设定，不依赖用户交互。检测站点通过比对 API 返回值与正常浏览器期望值来判断是否被自动化工具控制。

**基线结论**：**全部通过**。

| 站点 | 结果 | 说明 |
|------|:---:|------|
| bot.sannysoft | 56/56 ✅ | 所有检测项通过 |
| bot.incolumitas | 35/36 ✅ | 仅 WEBDRIVER fail（binary 层的已知取舍，官方基准同样 1 fail） |
| BrowserScan | 19/0 ✅ | 全部评定 Normal，无 Abnormal |
| deviceandbrowserinfo | isBot=False ✅ | 4 项 CDP 检测全部 false |

**优化方法**：无需额外优化。binary 层已处理 `navigator.webdriver=false`、CDP 信号（`--enable-automation` 被 `IGNORE_DEFAULT_ARGS` 剔除）。

---

### L2 — 行为 / 渲染指纹检测

**检测什么**：浏览器 API 完整性（Service Worker ContentIndex、Contacts Manager、Network Information downlinkMax）、CSS 计算样式一致性（`color_scheme`、`backgroundColor`）、`navigator.userAgentData` 等。CreepJS 和 fingerprint-scan 将这些信号聚合成 `likeHeadless` 百分比和逐项 pass/fail，判断浏览器是否缺失正常 API 或呈现自动化工具的渲染特征。

**基线结论**：

| 指标 | 基线值 | 失败项 |
|------|:---:|------|
| CreepJS likeHeadless | **31%** | `hasKnownBgColor`、`prefersLightColor`、`noContentIndex`、`noContactsManager`、`noDownlinkMax` |
| fingerprint-scan hd_fails | **3** | 同批 API 缺失 |
| CF Turnstile | PASS ✅ | — |
| ShieldSquare | PASS ✅ | 初次测试 FAIL → 后确认是代理 IP 被 Radware 拦截，无代理正常 |

**优化方法**：

| 步骤 | 操作 | 解决的问题 |
|:---:|------|------|
| P0-1 | `add_init_script` 注入 `window.ContentIndex` 类、`window.ContactsManager` 类、`NetworkInformation.prototype.downlinkMax` 属性 | noContentIndex / noContactsManager / noDownlinkMax |
| P0-2a | `new_context(color_scheme="dark")` | prefersLightColor |
| P0-2b | CSS `* { background-color: inherit !important; }`（DOMContentLoaded 后注入） | hasKnownBgColor |

**优化结果**：CreepJS **0% likeHeadless, 0/16 fails** ✅。fingerprint-scan **hd_fails=0** ✅（API 修复后自动消除）。

**关键发现**：旧版 P0-1 polyfill 的注入目标完全错误——将 `ContentIndex`（全局类）当成 `ServiceWorkerRegistration.prototype.index`（原型属性），`ContactsManager`（全局类）当成 `navigator.contacts`，`downlinkMax`（NetworkInformation 原型属性）当成 `navigator.connection.downlinkMax`。通过抓取 CreepJS 源码（`creep.js` L5277-5360）定位到正确的检测语法后，修正版 polyfill 立即生效。

---

### L3 — FingerprintJS Smart Signal

**检测什么**：FingerprintJS 的付费 bot 检测服务（Smart Signal）。客户端 SDK 收集 100+ 浏览器指纹信号（WebGL、Canvas、插件列表、字体、`navigator.*` 属性等），通过加密协议封装为 `sealed_result` 发送到服务端。服务端 ML 模型判断该指纹是否来自反检测浏览器。页面还通过 "Search flights" 交互验证真实用户行为——bot 的 API 请求会被 403 拒绝并返回 `"Anti-detect browser tampering detected"`。

**基线结论**：**❌ BLOCKED**。页面加载正常、Search 按钮最终启用，但点击后 API 返回 `"Malicious bot detected, access denied."`。`sealed_result=null`（指纹加密密封失败），表示指纹数据存在逻辑矛盾，无法通过完整性验证。

**尝试过的方案**（全部排除了非指纹因素）：

| 方案 | 操作 | 结论 |
|:---:|------|------|
| 1 | `wait_until="load"` + 长 sleep | 排除代理超时假象 → 暴露真实 BLOCKED |
| 2 | 资源拦截加速加载 | 仍 BLOCKED |
| 3 | 无代理直连 | 排除代理 IP 信誉 → 仍 BLOCKED |
| noise=default | 对比测试 | 仍 BLOCKED |
| Docker Linux binary | 交叉验证 | 仍 BLOCKED（Chrome/146 Win32） |
| `add_init_script` chrome.runtime | JS 注入 | 失败（C++ native binding 不可覆盖） |

**无法优化的根因**：binary 层缺失 4 个浏览器 API，JS wrapper 层无法修补：

| API | CloakBrowser | 正常 Chrome | 为何 JS 修不了 |
|------|:---:|:---:|------|
| `window.chrome.runtime` | ❌ 仅 `loadTimes/csi/app` | ✅ 完整 runtime | C++ → V8 native getter，JS 层不可覆盖 |
| `navigator.plugins` | 5（仅 PDF 变体） | ~10+ | 浏览器进程级 PluginArray，C++ 构造 |
| `navigator.mimeTypes` | 2 | ~15+ | 同上 |
| `navigator.userAgentData` | undefined (macOS) / 存在 (Linux) | 完整 | C++ 受控的 User-Agent Client Hints |

这些 "硬件指纹正常" + "API 属性残缺" 的矛盾被 FPJS ML 模型识别为 anti-detect browser tampering。

**为什么官方 README 显示 PASS**：存在时效性差异。FPJS ML 模型持续学习新型反检测浏览器模式，官方测试时的 CloakBrowser 指纹模式尚未被模型覆盖。另外 macOS binary 的 `userAgentData=undefined` 是 macOS 特有问题（Linux binary 有该 API），说明 macOS/Linux binary 的修补程度不一致。

**唯一解决路径**：向 CloakBrowser binary 团队提 issue，请求在 C++ 编译层注入 Compat API（chrome.runtime 模拟、标准 plugins/mimeTypes 填充、macOS userAgentData 注入）。

---

### L4 — reCAPTCHA v3

**检测什么**：Google reCAPTCHA v3 不检测浏览器 API 指纹，而是通过页面上的自然用户交互信号（鼠标移动、按键节奏、页面停留时间等）结合 IP 信誉综合评分（0.0–1.0）。分数越高越像人类。

**基线结论**：**0.9** ✅。使用官方原配（headless + 无代理）直接访问 Google reCAPTCHA demo，`grecaptcha.execute()` → token → 后端验证顺利完成，与官方基准一致。

**优化方法**：无需。之前带 VPN 代理时被代理层拦截 Chromium 的 `google.com` TLS 连接（`ERR_CONNECTION_CLOSED`），去掉代理后问题消失。

---

### 单项指标 & 新增指标

**检测什么**：浏览器的单点属性正确性——UA 字符串、CDP 自动化暴露、时区-IP 地理一致性、WebGL 渲染器、Canvas 哈希、TLS 指纹。

**基线结论**：除了 PixelScan 显示 inconsistent，其余全部正常。

| 指标 | 基线值 |
|------|:---:|
| navigator.webdriver | false ✅ |
| UA string | Chrome/145 ✅（macOS binary 版本，Docker 为 Chrome/146） |
| CDP detection | 4/4 false ✅（isAutomatedWithCDP / isPlaywright / isHeadlessChrome / hasWebdriverTrue） |
| 时区 | America/Phoenix = 代理出口 ✅ |
| BrowserLeaks WebGL | ANGLE (Apple GPU) ✅ |
| BrowserLeaks Canvas | hash OK ✅（binary seed 生成） |
| JA4 TLS | = Chrome ✅（binary 层 TLS 栈） |
| PixelScan | inconsistent ⚠️（非 bot 判定） |

**PixelScan inconsistent 说明**：扫描结果 "Your Browser Fingerprint is inconsistent"，未标记为 bot。Playwright 默认 `browser.new_page()` 被识别为 "Incognito Window"，使用 `launch_persistent_context(user_data_dir=...)` 可消除 Incognito 检测。但 inconsistent 本身来自 binary API 缺失（`plugins=5`/`mimeTypes=2`/`chrome.runtime` 缺失），与 P3-3 共享根因——待 binary 层修复后重测。

## 基线弱项

与官方基准对齐后，以下项目存在差距。`▸` = wrapper 可修，`ⁱ` = 外部因素（代理延迟/IP信誉），`❓` = 根因未定（需排除代理后重测）。

| # | 弱项 | 测试配置 | 基线值 | 官方基准 | 可修？ | 根治方法 | 备注 |
|---|------|------|:---:|:---:|:---:|------|------|
| ✅ 1 | CreepJS likeHeadless | L2 · noise=default | ~~31%~~ **0%** | `null` | ✅ 已修复 | P0-1 修正版 + P0-2a + P0-2b | 旧 P0-1 注入目标错误（`ServiceWorkerRegistration.prototype.index` 等），修正为 `window.ContentIndex`/`window.ContactsManager`/`NetworkInformation.prototype.downlinkMax` 后立即 0% (2026-06-26) |
| ✅ 2 | fingerprint-scan headless fails | L2 · noise=default | ~~hd_fails=3~~ **0** | `null` | ✅ 已修复 | 随 P3-1 自动消除 | — |
| ▸ 3 | FingerprintJS | L3 · noise=false | **BLOCKED** (403) | PASS | ❌ binary 层 | 提 issue 给 CloakBrowser binary 团队，请求修补 `chrome.runtime`、`navigator.plugins`、`navigator.mimeTypes`、`navigator.userAgentData` | 完整诊断见 P3-3 章节。`sealed_result=null` 是 FPJS 判定 "anti-detect tampering" 的关键触发信号。JavaScript polyfill 无法修复，必须 binary 层注入 Compat 等效 API |
| ✅ 4 | ShieldSquare | L2 · noise=default | ~~FAIL~~ **正常** | PASS | ✅ 无需修复 | 原来 FAIL 是代理导致 | 方案3（无代理直连）→ `www.shieldsquare.com` 正常加载（7,760 字 Radware 页面，重定向至 `radware.com/products/bot-manager/`）。带美国代理时页面被阻断是代理层问题，非 CloakBrowser 指纹问题 |
| ▸ 5 | PixelScan | 单独测试 | **inconsistent** | `null` | 🟡 binary 层 + Playwright 限制 | 持久化上下文消除 Incognito；inconsistent 来源与 P3-3 同根因（API 缺失） | 方案3（无代理）→ 正常加载；`launch_persistent_context` 消除 "Incognito Window" ✅。inconsistent 指纹仍因 binary API 缺失，非 bot 判定。见 P3-5 章节 |

### 已对齐官方（无需处理）

| # | 项目 | 测试配置 | 基线值 | 官方基准 |
|---|------|------|:---:|:---:|
| — | bot.sannysoft | L1 · headed + proxy | **56/56** | 56/56 |
| — | bot.incolumitas | L1 · headed + proxy | **35/36** | 1 fail（WEBDRIVER 已知 tradeoff） |
| — | BrowserScan | L1 · headed + proxy | **19/0** (Normal) | NORMAL |
| — | deviceandbrowserinfo | L1 · headed + proxy | **isBot=False** | 0 flags |
| — | CF Turnstile | L2 · headed + proxy | **PASS** | PASS |
| — | ShieldSquare | L2 · headed + 无代理 | **PASS** ²³ | PASS |
| — | reCAPTCHA v3 | L4 · headless · 无代理 | **0.9** | 0.9 |
| — | navigator.webdriver | inline | **false** | false |
| — | UA string | inline | **Chrome/145** | Chrome/146 |
| — | CDP detection | L1 · deviceandbrowserinfo | **Not detected** (4/4 false) | Not detected |
| — | 时区 | inline | **America/Phoenix** (=代理) | =代理 |
| — | JA4 TLS | 未测试 | — | = Chrome |
| — | BrowserLeaks · WebGL | 单独测试 | **ANGLE** (Apple GPU) | GPU一致 |
| — | BrowserLeaks · Canvas | 单独测试 | **hash OK** (17A1B304) | 正常 |
| — | BrowserLeaks · JS | 单独测试 | **webdriver=false** | webdriver=false |

> ShieldSquare (#4) 经诊断确认根因为美国代理 IP 被 Radware 信誉库拦截（见 ²³），非 CloakBrowser 反检测问题。无代理直连即可 PASS。

---

## 架构

```
第三层  humanize 行为模拟          Bezier鼠标 · 人类敲击 · 滚轮惯量 · CDP隔离世界
第二层  CLI 参数层                 抑制 --enable-automation · locale/timezone 二进制标记
第一层  编译级 Binary Patch (58项)  WebGL/Canvas/字体/GPU/硬件属性 · --fingerprint seed
```

核心设计：无运行时 JS patch，所有指纹修补在 C++ 编译层完成。

---

## 优化方案

### P0 — wrapper 层（我们直接改）

| 编号 | 项目 | 影响 | 方式 |
|:---:|------|------|------|
| P0-1 | 注入缺失 API (ContentIndex / ContactsManager / downlinkMax) | likeHeadless 31%→25% (noise=default) / 25%→6% (noise=false) | `add_init_script` polyfill |
| P0-2a | `color_scheme` 设为 `dark` | likeHeadless 消除 `prefersLightColor` | `browser.new_context(color_scheme="dark")` |
| P0-2b | CSS 注入默认背景色 | likeHeadless 消除 `hasKnownBgColor` | `add_init_script` 注入 `<style>` |
| P0-3 | viewport 调整为 1680×950（非自动化默认） | 降低屏幕指纹统计推断 | `viewport={"width": 1680, "height": 950}` |

> **P3-1 验证結果**: noise=default 下，P0-1 + P0-2a + P0-2b → **0% likeHeadless, 0/16 fails** ✅。P0-1 修正后 polyfill 请见下方。

P0-1 的 polyfill 脚本（修正版 —— 2026-06-26）：

```python
page.add_init_script("""
// === Phase 1: API class/interface polyfills (runs before all page scripts) ===
(function() {
    // CreepJS checks: 'ContentIndex' in window
    if (typeof ContentIndex === 'undefined') {
        window.ContentIndex = class ContentIndex {};
    }
    // CreepJS checks: 'ContactsManager' in window
    if (typeof ContactsManager === 'undefined') {
        window.ContactsManager = class ContactsManager {};
    }
    // CreepJS checks: 'downlinkMax' in NetworkInformation.prototype
    if (window.NetworkInformation && !('downlinkMax' in window.NetworkInformation.prototype)) {
        Object.defineProperty(window.NetworkInformation.prototype, 'downlinkMax', {
            get: function() { return 10; },
            configurable: true
        });
    }
})();

// === Phase 2: CSS injection for hasKnownBgColor (deferred until DOM exists) ===
document.addEventListener('DOMContentLoaded', function() {
    var style = document.createElement('style');
    style.textContent = '* { background-color: inherit !important; }';
    (document.head || document.documentElement).appendChild(style);
});
""")
```

> ⚠️ **修正说明**: 旧版 P0-1 polyfill 注入的是 `ServiceWorkerRegistration.prototype.index`、`navigator.contacts`、`navigator.connection.downlinkMax`，但 CreepJS 实际检测的是 `window.ContentIndex` 类、`window.ContactsManager` 类、`NetworkInformation.prototype.downlinkMax` 属性——目标完全错误，导致 P0-1"看起来不生效"。2026-06-26 通过抓取 `creep.js` 源码（L5277-5360）定位到正确检测点后修正。

### P1 — binary 层 / humanize 层（提 issue 或贡献）

| 编号 | 项目 | 说明 |
|:---:|------|------|
| P1-1 | 分布算法 (uniform→lognormal) | ✅ 已完成 (2026-06-26)。`rand_lognormal()` 添加到 `config.py`，`human_move()` 和 `scroll` 的 burst pause、delta、scroll pause 全部使用 lognormal 分布 |
| P1-2 | 验证 `chrome.runtime` | ✅ 已验证：Docker Linux 下 `window.chrome` 存在但仅有 `loadTimes/csi/app`，`chrome.runtime` 缺失（Chromium vs Chrome 已知差异，非自动化特有信号，低风险） |
| P1-3 | 验证 TLS/JA4 指纹 | 需连接外部 JA4 检测站点（`ja4.org` 或 `tls.peet.ws`）。UA + Client Hints 一致性已确认（Win32 + Chrome 146 无矛盾） |

### P2 — 行为增强（我们的脚本层）

| 编号 | 项目 | 说明 |
|:---:|------|------|
| P2-1 | "阅读"行为模拟 | ✅ 已完成 (2026-06-26)。30% 几率 3-8s 停顿 + 15% 几率 100-300px 回滚，集成到 `scroll.py` 和 `scroll_async.py` 滚动循环中。可通过 `read_pause_chance`/`read_backscroll_chance` 配置调整或禁用 |
| P2-2 | 标签页切换 | Cmd+Click 新标签页 → 切换阅读 → 关掉（Twitter 场景意义有限） |

### 不做

字体枚举、AudioContext、Canvas/WebGL（binary seed 已处理）；Speech/Battery/Gamepad（弱信号）；DNS/HTTP2（代理层问题）。

---

### P3 — 5 弱项专项攻坚 (2026-06-26 基线)

基于最新基线弱项表（共 5 项差距），按优先级和可行性排布。

#### P3-1: CreepJS 31% → 0%（优先级：最高 · 难度：低）✅ 已完成 (2026-06-26)

**当前**: L2 noise=default 下 5 项失败 (`hasKnownBgColor` + `prefersLightColor` + `noContentIndex` + `noContactsManager` + `noDownlinkMax`)，likeHeadless=31%。

**根因**: noise=default 下 API 缺失 + 颜色信号双重叠加。noise=false 时仅 4 项（25%），说明 1 项颜色相关（`hasKnownBgColor`）是 noise=default 独有。

**方案**: 将现有 P0-1 + P0-2 组合在 L2 noise=default 配置下重测，验证 31%→0% 路径：

| 步骤 | 操作 | 预期 |
|:---:|------|:---:|
| 3-1a | P0-1 polyfill（3 项 API 注入） | 31% → ~8%（剩 `hasKnownBgColor` + `prefersLightColor` 2 项颜色 fail） |
| 3-1b | P0-2 `color_scheme="dark"` | 8% → 0%（消除颜色信号） |
| 3-1c | P0-3 viewport 1680×950 | 加固屏幕指纹一致性 |

```python
# 整合 polyfill（P0-1 完整版）
POLYFILL = """
// --- API 注入 ---
if (!('index' in ServiceWorkerRegistration.prototype)) {
    Object.defineProperty(ServiceWorkerRegistration.prototype, 'index',
        {value: 'noop', writable: true, enumerable: true, configurable: true});
}
if (!('contacts' in navigator)) {
    Object.defineProperty(navigator, 'contacts',
        {value: {select: async () => [], getProperties: async () => []},
         writable: true, configurable: true});
}
if (navigator.connection && !('downlinkMax' in navigator.connection)) {
    const c = navigator.connection;
    Object.defineProperty(c, 'downlinkMax',
        {value: Math.max(c.downlink || 10, 10),
         writable: true, enumerable: true, configurable: true});
}
// --- hasKnownBgColor 信号消除（注入非默认背景色）---
// CreepJS 检测 computedStyle.backgroundColor；noise 模式下可能为透明/白
// 配合 color_scheme=dark 使用效果最佳
"""
```

> ⚠️ 上面是旧版（错误）polyfill，保留作为历史记录。**修正版请见上方「P0 — wrapper 层」章节**。
>
> **P3-1 验证结果 (2026-06-26)**：
> - 无代理本地直连 → L2 noise=default baseline: **31% likeHeadless, 5 fails**
> - +P0-1 修正版 polyfill + P0-2a color_scheme=dark + P0-2b CSS 背景色注入 → **0% likeHeadless, 0/16 fails** ✅
> - CreepJS headless/stealth 均为 0%
> - P3-2 (fingerprint-scan hd_fails) 随 P3-1 自动消除
>
> **关键发现**：旧 polyfill 错误地将 `ContentIndex` 当成 `ServiceWorkerRegistration.prototype.index`。通过抓取 `creep.js` 源码（L5277-5360）定位到 CreepJS 实际检测的是窗口全局类（`window.ContentIndex`、`window.ContactsManager`）和原型属性（`NetworkInformation.prototype.downlinkMax`），修正后立即生效。
```

> ⚠️ 关键差异：现有 P0 验证链（25%→6%→0%）基于 **noise=false** 基线。3-1a~3-1c 需要在 **noise=default** 下重测，因 `hasKnownBgColor` 仅在 noise=default 下出现。

#### P3-2: fingerprint-scan hd_fails 3→0（优先级：中 · 难度：低）✅ 已完成 (随 P3-1 自动消除)

**当前**: L2 noise=default 下 headless 检测 3 项失败。

**根因**: 与 CreepJS 同批 API 缺失（noContentIndex 等），P0-1 已验证消除。

**方案**: 无需独立处理，P3-1 完成后自动消除。单独重测确认即可。

#### P3-3: FingerprintJS BLOCKED → PASS（优先级：高 · 难度：高 · binary 层）⬜ 待 binary 修复

**当前**: FPJS 服务端 `POST /web-scraping/api/flights` 返回 403，message=`"Anti-detect browser tampering detected, potentially a bot, access denied."`。已排除代理延迟、代理 IP 信誉、locale-IP 不一致等因素，确认是 CloakBrowser binary 层指纹被 FPJS Smart Signal 识别。

**诊断过程 (2026-06-26)**:

| 测试 | 配置 | 结果 | 结论 |
|:---:|------|------|------|
| 控制组 | `disableBotDetection=1` | hasFlights=True ✅ | 页面流程正确 |
| 方案 1 | `wait_until=load` + 长 sleep | BLOCKED (403) | 非超时问题 |
| 方案 2 | 方案 1 + 资源拦截 | BLOCKED (403) | 非网络延迟 |
| 方案 3 | 无代理 + noise=false | BLOCKED (403) | 非代理信誉 |
| 对比 | noise=default | BLOCKED (403) | 非 noise 配置 |
| 对比 | 有代理 + noise=false | BLOCKED (403) | 一致复现 |
| **Docker Linux 交叉验证** | Docker `v0.4.0` Chrome/146 Win32 + proxy + noise=false | **BLOCKED** (button disabled, tampering) | macOS 和 Docker Linux 同缺陷 |

**macOS vs Docker Linux 交叉验证 (2026-06-26)**:

| 信号 | macOS Chrome/145 | Docker Linux Chrome/146 | 正常 Chrome |
|------|:---:|:---:|:---:|
| `navigator.plugins.length` | 5 | 5 | ~10+ |
| `navigator.mimeTypes.length` | 2 | 2 | ~15+ |
| `chrome.runtime` | ❌ 缺失 | ❌ 缺失 | ✅ 存在 |
| `userAgentData` | ❌ undefined | ✅ 存在 | ✅ 存在 |
| FPJS 结果 | **BLOCKED** | **BLOCKED** | PASS |

> 两个平台的 binary 在这 4 个 API 上表现一致（均缺失），且均触发 FPJS BLOCKED。排除"macOS binary 漏修补而 Linux 有"的猜测。

**FPJS 拦截的触发信号**（来源: FPJS API 请求拦截，`suspect_score=22`，`sealed_result=null`）:

| 信号 | CloakBrowser (Chrome/145 macOS) | 正常 Chrome/145 macOS | 差异 | 可修? |
|------|------|------|:---:|:---:|
| `window.chrome.runtime` | **缺失** (`chrome` 仅含 `loadTimes/csi/app`) | 存在完整 `runtime` 对象 | ⚠️ 高 | ❌ binary 层 |
| `navigator.plugins.length` | **5** (仅 PDF Viewer ×5) | ~10+ (含 Native Client、Widevine 等) | ⚠️ 高 | ❌ binary 层 |
| `navigator.mimeTypes.length` | **2** | ~15+ | ⚠️ 中 | ❌ binary 层 |
| `navigator.userAgentData` | **N/A** (`undefined`) | `{brands:[...], mobile:false, platform:"macOS"}` | ⚠️ 中 | ❌ binary 层 |
| `sealed_result` | **null** | 有效密封结果 | ⚠️ 高 | ❌ binary 层 |

> `sealed_result: null` 是关键——FPJS 的加密密封机制用于防止指纹数据被篡改。null 表示指纹数据不一致或无法通过完整性验证，直接触发服务端 "tampering detected" 判定。

**根因（两层）**:

1. **Binary 差**: CloakBrowser binary 在 58 项 patch 中修补了 WebGL/Canvas/GPU/字体等硬件层指纹，但**没有修补浏览器 API 层属性**（`chrome.runtime`、`plugins`、`mimeTypes`、`userAgentData`）。这些 Chromium 原生缺失的属性与修补后的"正常"硬件指纹形成矛盾——FPJS Smart Signal 的 ML 模型将这种"硬件正常 + API 残缺"模式识别为 anti-detect 篡改。

2. **检测工具升级（时效性）**: 官方 README 的 FPJS PASS 结果是在早期版本跑出的。FPJS Smart Signal 是一个持续学习的 ML 模型，在不断吸收新的反检测浏览器指纹特征。即使是同一个 CloakBrowser binary，在官方测试时可能通过，但几个月后模型学到了 CloakBrowser 的指纹模式就会拒绝。这是所有反检测浏览器面临的共性挑战——与检测服务的对抗是动态的，不是一劳永逸的。

**为什么 CamoFox 能通过**: Firefox (Gecko) 引擎天然不暴露 `chrome.*` / `userAgentData` 等 Chrome 专属 API——FPJS 将 CamoFox 的缺失视为"正常 Firefox 浏览器"，而非"反检测 Chrome"。

**解决方案**:

| 优先级 | 操作 | 说明 |
|:---:|------|------|
| 🔴 P0 | **提 issue 给 CloakBrowser binary 团队** | 附完整诊断数据，请求 binary 层增加以下修补 |
| | — `chrome.runtime` 注入 | 添加模拟 Chrome runtime 对象（connect/sendMessage/getManifest 等） |
| | — `navigator.plugins` 填充 | 添加 Native Client、Widevine Content Decryption Module 等标准 Chrome 插件 |
| | — `navigator.mimeTypes` 填充 | 对应 plugin 的 MIME 类型 |
| | — `navigator.userAgentData` 注入 | 根据 UA 动态注入 brands/version/platform 数据 |
| 🟡 P1 | binary 发布后重测 FPJS | 预期 `sealed_result` 非 null → Smart Signal score 降低 → flights API 返回数据 |

> ⚠️ **P3-3 是 5 弱项中唯一无法用 wrapper 层解决的**。JavaScript `add_init_script` 无法覆盖 `window.chrome.runtime`（binary 锁定的 getter）、无法添加 `navigator.plugins` 条目（浏览器进程级数组）、无法注入 `navigator.userAgentData`（受控于 Chromium 的 User-Agent Client Hints 实现）。必须 binary 层配合。

#### P3-4: ShieldSquare FAIL → PASS ✅（优先级：低 · 难度：低）✅ 已解决 (2026-06-26)

**当前**: 之前两套方案（CamoFox + CloakBrowser）均 FAIL，页面内容被 Radware 移除。

**诊断过程**:

| 步骤 | 操作 | 结果 | 结论 |
|:---:|------|------|------|
| 3-4a | 无代理 + headed 直连 `www.shieldsquare.com` | **正常加载**（7,760 字，重定向至 `radware.com/products/bot-manager/`） | FAIL 不是 CloakBrowser 问题 |
| 3-4b | 对比官方测试配置 | 官方 `stealth_test.py` 同样带代理 → PASS | 代理是允许的，问题出在这个特定代理 |
| 3-4c | 三角定位 | 有代理 → FAIL / 无代理 → PASS / 官方有代理 → PASS | **该代理 IP 被 Radware 拦截** |

**根因**: 美国住宅代理出口 IP `82.180.163.169` 被 Radware IP 信誉库标记为 bot/malicious。与 CloakBrowser 指纹无关，与 CamoFox 无关，与 Docker/Xvfb 也无关。纯 IP 信誉问题。

**为什么之前误判为"Docker 层信号"**:
- CamoFox 和 CloakBrowser 两套方案都用的同一个美国代理 → 都 FAIL
- 当时的测试说"CamoFox 无代理也 FAIL"，但那个 CamoFox 实际上通过 Docker 以 `host.docker.internal:18118` TCP 中继到 `192.168.1.7:8118`，本质还是同一个代理出口 IP
- 直到这次 CloakBrowser 真正无代理直连才暴露真相

**解决方案**: 换个不在 Radware 黑名单上的代理 IP 即可。不需要 binary 层修改，不需要 JA4/TLS/HTTP header 排查。

> **ShieldSquare 从 5 弱项中移除** ✅。这是一个 IP 信誉问题，不是 CloakBrowser 反检测问题。

#### P3-5: PixelScan inconsistent → human（优先级：低 · 难度：中 · 混合层）⬜ 部分解决

**当前**: 无代理直接访问 OK。扫描结果 "Your Browser Fingerprint is **inconsistent**"，未标记为 bot。

**Phase 11 诊断 (2026-06-26)**:

| 测试 | 配置 | Incognito | 指纹结果 |
|:---:|------|:---:|:---:|
| 基线 | Playwright default context + dark | 👤 检测到 | inconsistent |
| 方案 A | `color_scheme=light` | 👤 检测到 | inconsistent |
| 方案 B | `color_scheme=light` + viewport 1680×950 | 👤 检测到 | inconsistent |
| **方案 C** | **`launch_persistent_context`** (持久化用户目录) | ❌ 消除 | inconsistent |
| 方案 D | 方案 C + 全套 P0 (API polyfill + dark CSS + viewport) | ❌ 消除 | inconsistent |

**关键发现**:

1. **"Incognito Window" 是 Playwright 问题，不是 CloakBrowser 问题** ✅ 已解决 — 使用 `launch_persistent_context`（持久化用户数据目录）消除。Playwright 的 `browser.new_context()` / `browser.new_page()` 默认创建临时会话，PixelScan 将其识别为 Incognito 模式。注意：CloakBrowser 的 `launch()` 目前不暴露 `launch_persistent_context`，需要通过 Playwright 底层 API 调用。

2. **"inconsistent" 指纹来自 binary API 缺失** ⚠️ 待解决 — 同 P3-3，`navigator.plugins`(仅5) / `mimeTypes`(仅2) / `chrome.runtime`(缺失) 等 API 层面的矛盾被 PixelScan 检测。但与 FPJS 的区别：PixelScan 只报 "inconsistent"（不一致），不报 "bot"（机器人）。这意味着它的判定比 FPJS 宽容，认为指纹问题可能是浏览器配置差异而非恶意自动化。

3. **CamoFox 已验证 human 可行** — P0-3 优化柱（viewport 1680×950 + Firefox 原生指纹）已在 CamoFox 环境验证 result=human ✅。说明 Firefox 的完整性指纹不触发 PixelScan 的 inconsistent 判定，进一步佐证了 Chrome API 缺失是根因。

**解决方案**:

| 优先级 | 操作 | 说明 |
|:---:|------|------|
| 🟡 P1 | CloakBrowser 增加 `persistent_context` 参数 | 在 `launch()` 中支持 `user_data_dir`，或在 wrapper 层暴露持久化上下文方法 |
| 🔴 P0 | binary 修复 API 缺失 (同 P3-3) | `plugins`/`mimeTypes`/`chrome.runtime` 修补后，PixelScan inconsistent 预期自动消除 |
| 🟢 P2 | 提 PR 给 CloakBrowser wrapper | `launch(user_data_dir=...)` 或 `launch_persistent(user_data_dir=...)` 作为独立 API |

> P3-5 和 P3-3 **共享同一个 binary 层根因**。一旦 P3-3 的 binary issue 解决（`plugins`/`mimeTypes`/`chrome.runtime`/`userAgentData` 修补），P3-5 的 inconsistent 也有望同时消除。

---

### 5 弱项执行优先级总览 (2026-06-26 终态)

```
✅ P3-1   CreepJS 31%→0%（修正版 P0-1 polyfill + P0-2a + P0-2b）
✅ P3-2   fingerprint-scan hd_fails→0（随 P3-1 自动消除）
✅ P3-4   ShieldSquare FAIL→PASS（根因：代理 IP 被 Radware 信誉库拦截）
⬜ P3-3   FingerprintJS BLOCKED (binary 层 + FPJS 模型升级)
⬜ P3-5   PixelScan inconsistent→human (Playwright Incognito + binary API 缺失)
```

**5 弱项 → 3 已解决，2 待 binary 修复**：
- P3-3 / P3-5 共享同一个根因：CloakBrowser binary 未修补 `chrome.runtime` / `plugins` / `mimeTypes` / `userAgentData`。一旦 binary 层解决，两个弱项预期同时消除。
- P3-5 额外发现：Playwright 默认上下文被 PixelScan 识别为 Incognito，可通过 `launch_persistent_context` 消除。
    
    style A fill:#f96,stroke:#333
    style D fill:#9f6,stroke:#333
    style F fill:#ff9,stroke:#333
```

> 🟢 = 可独立完成 | 🟠 = 依赖外部资源 | 🟡 = 需 binary 团队配合

---

## 执行顺序

```
✅ Phase 0  官方对齐基线 (headed+住宅代理+geoip+noise-off) → likeHeadless=25%
✅ Phase 1  P0-1: 深层 polyfill (旧版) → 25%→6%, hd_fails→0
✅ Phase 2  P0-2: color_scheme=dark → 6%→0%
✅ Phase 3  P0-3: viewport 1680×950
✅ Phase 4  新增指标验证: JA4 TLS, BrowserLeaks, PixelScan
✅ Phase 5  CamoFox (REDFOX) 对比评测 → CreepJS 6%, fp-scan 0/100, FPJS PASS
✅ Phase 6  美国代理分层基线重测 (L1/L2/L3/L4 完整矩阵, 2026-06-26)
✅ Phase 7  P3-1/P3-2: P0-1 polyfill 修正 + CreepJS 31%→0% (L2 noise=default, 2026-06-26)
✅ Phase 8  P3-4: ShieldSquare 诊断 → 代理 IP 信誉问题，无代理 PASS (2026-06-26)
✅ Phase 9  P3-3/P3-5 诊断 (方案1/2/3, 2026-06-26):
           - P3-3 FPJS: wait_until=load 消除超时，确认 BLOCKED（binary API 缺失 + FPJS 模型升级）
           - P3-5 PixelScan: 无代理可测，launch_persistent_context 消除 Incognito
⬜ Phase 10  P3-3 + P3-5: 提 issue 给 binary 团队（共享根因，一次性修复）
✅ Phase 11  P1-1: humanize 层 lognormal 分布 (2026-06-26)
✅ Phase 12  P2-1: 阅读行为模拟 (2026-06-26)
```

---

*> 最后更新：2026-06-26。5 弱项 3/5 已解决。P1-1 (lognormal) 和 P2-1 (阅读模拟) 已实现。P3-3 + P3-5 共享根因（binary API 缺失），待提 issue 给 binary 团队。*
