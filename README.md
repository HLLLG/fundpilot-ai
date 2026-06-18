# FundPilot AI

私人基金投研助手：上传支付宝/养基宝总览截图更新账户汇总，按个人风控规则生成 DeepSeek V4 Pro 投研日报；**推荐基金** Tab 从多板块窄池候选中扫描新基机会（默认全市场横向对比）。

你的默认终端路径是：

```bash
/d/Code/HL_Project/fundpilot-ai
```

所以下面的命令都按 **Git Bash / MINGW64** 写法提供。

## 功能

- 支持上传截图或粘贴 OCR 文本。
- PaddleOCR 可选本地识别；未安装时仍可手动录入。
- 支持上传养基宝总览截图，以及**支付宝「我的基金」持有列表**截图（预览确认后更新账户汇总）。
- 总览 OCR 缺少基金代码时，优先用 **AkShare 基金名称表**查码，本地 `fund_profiles` 元数据按名称兜底。
- 份额、成本、板块、购入日等元数据由 OCR **自动维护**（SQLite `fund_profiles`），无需单独「基金档案」页。
- 养基宝总览 OCR：识别多只基金、区分当日/持有收益（未收盘时当日列为 `-` 则不填当日收益）；**OCR 漏负号**时按收益率/账户总收益/独立行减号规则补符号（当日收益额、板块涨跌与收益率一致）。详见 `docs/PROJECT_CONTEXT.md`。
- **持有**页养基宝式账户汇总：基金代码、名称、金额、持有/当日收益、板块涨跌；**估算当日收益率**（≈ 板块涨跌 + 持有收益率，与传给模型一致）；**刷新后 instant 展示**（localStorage 缓存 + 服务端 `refreshed_at`）。
- **市场** Tab：子 Tab「主题板块 | 美股」；主题板块小倍式涨幅榜（~66 白名单、主力净流入、列头排序）；美股指数期货 + QDII 盘前参考。
- **盈亏分析**页：收益走势（我的收益 vs 沪深300）、盈亏日历、当日 TOP5、持仓分布图。
- 内置稳健型风控规则：最大浮亏线、单只基金集中度、偏定投、拒绝追高（规则守卫 + 传入 AI `profile`）。
- DeepSeek V4 Pro 生成日报；新闻来自东财/基金公告/宏观主题（可配置），生成前用 **Flash 按主题摘要**（`topic_briefs`），深度模式还可 `fetch_market_news` 补拉。
- 报告含**逐基金操作建议**、**主题要闻摘要**与**新闻原文列表**；利好/利空标题须能对应原文（`news_citation` 守卫）。
- **后台异步分析**：点击「生成报告」后后台执行，右下角悬浮面板查看进度；完成后桌面通知（需开启通知权限）。
- 支持**快速/深度**两种分析模式（生成日报时选择）。
- 报告支持与上一份日报**对比差异**；可**导出 Markdown**；报告内 **调仓示意模拟**（按动作与集中度自动补算示意金额）。
- **报告追问**：分析页「决策建议」右侧可与 AI 流式对话（Markdown 渲染、加高可滚动面板）；支持快速/深度追问模式，深度模式可按需拉取最新新闻；对话按报告存 SQLite，可**导出对话 Markdown**。
- **SQLite 全库备份/恢复**（历史页）；风控参数、分析模式、追问模式保存在浏览器本地，风控画像同步至 SQLite。
- SQLite 本地保存历史日报，支持在历史侧栏删除旧报告。
- 基金详情页：**业绩走势**（近1月～3年、本基金 vs 沪深300）、历史净值滚动加载；**持有天数**可点选首次购入日期；关联板块分时图。
- **邮箱注册/登录**（JWT，默认 30 天有效）；用户菜单 → **账号设置** 可绑定微信，与小程序共享持仓。
- **微信小程序**（`apps/miniprogram`）：微信登录、持有列表、基金详情（只读）；云端部署见 [docs/deploy/cloudbase.md](docs/deploy/cloudbase.md)。
- **推荐基金** Tab：默认 **全市场机会** 模式，从 21 个板块热度 Top 8 建窄池并 AI 横向精选；可切换 **持仓缺口补充**；选基策略、关注方向（限时拉取 + 本地缓存）、预算、基金类型偏好；历史推荐（含批量删除）、候选池、7 日复盘与追问。详见下文「推荐基金」。
- **页面缓存：** 持有 localStorage 优先、Dashboard/市场子 Tab sessionStorage 记忆；盈亏分析、业绩走势、持仓详情等 SWR 缓存；板块涨跌后台轻量轮询，手动刷新走精确模式。
- **日报数据口径**：AI 分析使用 `estimated_holding_return_percent`，与持有页「持有」列一致（盘中含板块估算）。
- **调仓示意**：报告页展开「调仓示意模拟」；AI 未填金额时按集中度或减仓动作自动补算变动额。

**分析模式（生成日报）：**

| 模式 | 说明 |
|------|------|
| 快速 | 使用 `deepseek-v4-flash`；预取新闻 + 主题摘要，不调用新闻 Tool，适合交易日赶时间。 |
| 深度 | 使用 `.env` 中的 Pro 模型；主题摘要 + 可主动 `fetch_market_news` 补拉，更慢但更全。 |

**追问模式（报告页右侧对话）：**

| 模式 | 说明 |
|------|------|
| 快速 | Flash；仅基于已生成日报与历史对话作答。 |
| 深度 | Pro；模型在需要时可调用 `fetch_market_news` 补充当日新闻后再流式回答。 |

## 推荐基金

「推荐基金」与「生成日报」共用同一套风控画像（`InvestorProfile`），但使用**独立的 AI 角色设定**与报告存储，不会把荐新基混进日报 Prompt。

**适用场景：** 想了解「当前市场哪些板块/基金值得关注」时，在受控窄池（约 15～25 只）内扫描机会（非全市场盲推，不自动下单）。

**扫描模式：**

| 模式 | 说明 |
|------|------|
| 全市场机会（默认） | 按板块热度取 Top 8，多板块横向对比；持仓仅作背景参考 |
| 持仓缺口补充 | 优先未重仓、热度靠前的缺口板块（原 MVP 逻辑） |

**关注方向（21 个板块）：** 商业航天、半导体、国防军工、人工智能、电网设备、互联网、有色金属、新能源车、医药、证券、银行、白酒、光伏、锂电池、消费电子、机器人、云计算、5G、医疗器械、CPO、PCB。可选勾选最多 3 个优先方向。

**使用步骤：**

```text
1. 先在「持有」页恢复/更新持仓（缺口模式会参考组合结构；全市场模式仅作背景）
2. 打开「推荐基金」Tab，选择扫描模式（默认「全市场机会」）
3. 可选：勾选关注方向（最多 3 个板块）、设置预算、选择选基策略与基金类型偏好
4. 可选：展开「AI 角色设定」微调荐基风格（会持久化，下次扫描生效）
5. 选择快速/深度 → 点击「扫描今日机会」
6. 右下角 DiscoveryJobStatusFloat 查看进度；完成后查看推荐报告
7. 右侧历史列表可切换往日报告；报告内可展开候选池、7 日复盘；点击推荐卡片可预览基金详情
8. 在报告下方用追问面板细化需求（SSE 流式，同日报追问）
```

**选基策略：**

| 策略 | 说明 |
|------|------|
| 均衡潜力（默认） | 综合近 3/6 月强弱，惩罚近 1 年极端涨幅，避免追「年度冠军」 |
| 含新发观察 | 每板块约 2 只近 6 月内新发基金 + 3 只均衡老基；候选池标「新发」 |

开启风控「拒绝追高」时，板块当日大涨、近 1 年涨幅过高或净值贴近区间高点的推荐会自动降为「等待回调」。

**基金类型偏好：**

| 选项 | 说明 |
|------|------|
| 不限 | 默认 |
| ETF 联接优先 | 候选池中联接类基金加权 |
| 排除 C 类 | 过滤名称含 C 类份额的基金 |

**分析模式（扫描）：** 与生成日报相同——快速用 Flash、深度用 Pro；深度模式可拉更多新闻语境。扫描为后台异步任务，通过 `GET /api/jobs/{id}` 轮询状态。

**与日报的区别：**

| | 生成日报 | 推荐基金 |
|---|----------|----------|
| 分析对象 | 已有持仓 | 窄池新基候选 |
| AI 角色 | `analysis-prompt` | `discovery-prompt` |
| 输出 | `fund_recommendations` 调仓建议 | `FundDiscoveryReport` 荐基报告 |
| 历史 | 历史日报侧栏（批量删除） | 推荐基金右侧 `DiscoveryHistoryRail`（批量删除） |

设计细节见 [V2 设计](docs/superpowers/specs/2026-06-14-fund-discovery-v2-design.md)、[V3 选基策略](docs/superpowers/specs/2026-06-14-fund-discovery-v3-selection-strategy-design.md)、[全市场扫描](docs/superpowers/specs/2026-06-15-fund-discovery-full-market-design.md)；API 与架构见 [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)。

## 目录

```text
apps/api        FastAPI 后端（含 auth/、Dockerfile）
apps/web        Next.js 前端（/login、/register、/settings）
apps/miniprogram  微信小程序
data            SQLite 数据库（云端可迁 MySQL）
uploads         本地上传截图
scripts         dev.sh / dev.ps1 / migrate_sqlite_to_mysql.py
docs            项目文档（含 AI 上下文、CloudBase 部署）
```

面向 AI 或新开发者的架构与业务说明见 **[docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)**，可在新对话开头 `@` 该文件，避免重复扫描全仓库。

## 环境变量

在项目根目录复制模板：

```bash
cp .env.example .env
```

编辑 `.env`，将 DeepSeek 控制台复制的 API Key 填入 `FUND_AI_DEEPSEEK_API_KEY`（勿提交到 Git）：

```text
FUND_AI_DEEPSEEK_API_KEY=
FUND_AI_DEEPSEEK_MODEL=deepseek-v4-pro
FUND_AI_DEEPSEEK_MODEL_FAST=deepseek-v4-flash
FUND_AI_DEEPSEEK_TIMEOUT_SECONDS=300
FUND_AI_DEEPSEEK_MAX_TOKENS=384000
FUND_AI_DEEPSEEK_MAX_TOKENS_REPORT=384000
FUND_AI_NEWS_ENABLED=true
FUND_AI_NEWS_MAX_TOPICS=5
FUND_AI_NEWS_PER_TOPIC=5
FUND_AI_NEWS_TOOL_MAX_ROUNDS=3
FUND_AI_NEWS_SOURCES=eastmoney,announcement,macro
FUND_AI_NEWS_SUMMARIZE=true
FUND_AI_NEWS_MACRO_TOPIC=上证指数
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
FUND_AI_JWT_SECRET=change-me-to-a-random-secret-at-least-32-chars
```

鉴权与云端（可选，详见 `.env.example` 与 [docs/deploy/cloudbase.md](docs/deploy/cloudbase.md)）：

| 变量 | 说明 |
|------|------|
| `FUND_AI_JWT_SECRET` | JWT 签名密钥（本地开发也建议设置） |
| `FUND_AI_JWT_ACCESS_EXPIRE_MINUTES` | JWT 有效期（分钟）；默认 43200（30 天） |
| `FUND_AI_DATABASE_URL` | 设则使用 MySQL；省略则用 `data/app.db` |
| `FUND_AI_CLOUDBASE_ENV_ID` | 云开发环境 ID（微信登录） |
| `FUND_AI_CLOUDBASE_AUTH_DEV_MODE` | `true` 时小程序本地联调可用开发 UID |

`.env` 已被 `.gitignore` 忽略，不会提交到 Git。

如果 DeepSeek 报 `read operation timed out`，通常是 `deepseek-v4-pro` 响应较慢。可以先把
`FUND_AI_DEEPSEEK_TIMEOUT_SECONDS` 调大，例如 `300`；如果你更看重速度，也可以把
`FUND_AI_DEEPSEEK_MODEL` 改成 `deepseek-v4-flash`。

新闻相关（可选）：

| 变量 | 说明 |
|------|------|
| `FUND_AI_NEWS_ENABLED` | `false` 时不注册新闻 Tool，仍可在分析前预取新闻 |
| `FUND_AI_NEWS_MAX_TOPICS` | 从持仓推导的检索主题上限 |
| `FUND_AI_NEWS_PER_TOPIC` | 每个主题保留的新闻条数 |
| `FUND_AI_NEWS_TOOL_MAX_ROUNDS` | 模型调用 `fetch_market_news` 的最大轮数 |
| `FUND_AI_NEWS_SOURCES` | 新闻源：`eastmoney`、`announcement`、`macro` |
| `FUND_AI_NEWS_SUMMARIZE` | 是否用 Flash 按主题生成 `topic_briefs` |
| `FUND_AI_NEWS_MACRO_TOPIC` | 宏观主题检索词（如上证指数） |

## 安装

后端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/api

# 如果 .venv 已存在，可以跳过这行
/d/Users/hegl/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe -m venv .venv

./.venv/Scripts/python.exe -m pip install -r requirements.txt
```

前端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/web
npm install
```

可选 OCR：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/api
./.venv/Scripts/python.exe -m pip install -r requirements-ocr.txt
```

PaddleOCR 依赖较大，首次安装和首次识别会比较慢。你也可以先用文本粘贴和手动校对跑完整流程。

## 启动

**推荐：一条命令同时启动前后端（Git Bash）：**

```bash
cd /d/Code/HL_Project/fundpilot-ai
bash scripts/dev.sh
```

或 Windows PowerShell：

```powershell
.\scripts\dev.ps1
```

也可以分别开两个终端：

开第一个 Git Bash 终端启动后端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/api
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

开第二个 Git Bash 终端启动前端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/web
npm run dev
```

前端 dev 脚本使用 Next.js 的 webpack 模式，避开 Windows 下偶发的 Turbopack panic。

浏览器打开：

```text
http://127.0.0.1:3001
```

## 推荐使用流程

```text
0. 首次使用在 /register 注册账号，或 /login 登录（需在 .env 配置 FUND_AI_JWT_SECRET）
1. bash scripts/dev.sh → 打开 http://127.0.0.1:3001（**刷新后恢复上次 Tab**，首次默认「持有」）
2. 启动后 **localStorage 优先**展示上次持仓，再请求 API 同步；需更新金额时点击「新增持有」上传支付宝/养基宝总览截图
3. 预览确认后写入账户汇总，并自动同步份额、查码、刷新板块涨跌
4. 「盈亏分析」查看收益走势、盈亏日历、当日 TOP5
5. 「市场」→ 主题板块涨幅榜 / 美股概览
6. 点击持仓行可查看基金详情（业绩走势、持有天数、板块分时）
7. 「推荐基金」→ 选择扫描模式（默认全市场）→ 可选关注板块、预算、**选基策略**、基金类型与荐基角色 → 扫描今日机会 → 查看报告 / 历史 / 候选池 / 复盘 / 追问
8. 切到「生成日报」→ 确认风控画像与日报 AI 角色 → 选快速/深度 → 生成日报
9. 右下角 JobStatusFloat 查看进度；完成后可在报告页追问（可选深度模式拉最新新闻）
10. 换机迁移：用户菜单 → 历史日报 → SQLite 备份导出/导入
11. 需要留存时可导出日报 Markdown 或导出对话 Markdown
12. 使用小程序：用户菜单 → 账号设置 → 绑定微信；详见 apps/miniprogram/README.md
```

## 云端部署（可选）

面向 ≤5 人私有部署：FastAPI 云托管 + CloudBase MySQL + 静态 Web + 微信小程序。完整步骤见 **[docs/deploy/cloudbase.md](docs/deploy/cloudbase.md)**。

```bash
# 本地验证 Docker 镜像
export FUND_AI_JWT_SECRET=your-secret-32chars-minimum
docker compose -f docker-compose.cloud.yml up --build
```

## 验证

后端单元测试（约 **304** 项，本地串行 ~28s；默认离线 stub，不访问东财/AkShare/MySQL）：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/api
./.venv/Scripts/python.exe -m pytest tests -q
```

与 CI 一致并行跑（需 `pytest-xdist`，Linux/macOS 推荐；Windows 上 xdist 偶发不稳定）：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/api
./.venv/Scripts/python.exe -m pytest tests -q -n auto --dist loadscope
```

本地若 `.env` 配置了 MySQL，跑测前建议临时清空数据库 URL，强制使用 SQLite 内存库（与 CI 相同）：

```bash
export FUND_AI_DATABASE_URL=
export FUND_AI_OCR_PRELOAD=false
export FUND_AI_NEWS_ENABLED=false
export FUND_AI_SECTOR_SIGNAL_BACKTEST_ENABLED=false
```

单测默认 **30s** 超时（`apps/api/pytest.ini`）。外部行情、交易日历、板块热度等在 `tests/conftest.py` 中统一 stub，避免子进程拉 AkShare。

**CI（GitHub Actions）：** `api` job 并行 pytest + 关闭 OCR 预加载/新闻/回测；`web` job lint/typecheck/build；`e2e-smoke` Playwright 冒烟。详见 `.github/workflows/ci.yml`。

前端：

```bash
cd /d/Code/HL_Project/fundpilot-ai/apps/web
npm run lint
npm run typecheck
npm run build
```

## 常见 Git Bash 路径写法

Git Bash 里不要写：

```bash
cd D:\Code\HL_Project\fundpilot-ai
```

要写成：

```bash
cd /d/Code/HL_Project/fundpilot-ai
```

Windows 可执行文件路径也要用 `/d/...` 形式，或者使用已有虚拟环境里的：

```bash
./.venv/Scripts/python.exe
```

## 隐私和边界

本项目面向个人自用。截图、数据库和上传文件默认保存在本地项目目录。DeepSeek 会收到你确认后的持仓、风控参数、净值摘要、主题新闻摘要（Flash 生成）、新闻标题/短摘要，以及已生成日报全文（追问时）；不会自动上传原始截图。报告与对话只用于个人投研辅助，不构成投资建议，也不会执行任何交易。
