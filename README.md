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
- OCR 会自动维护估算金额、板块和购入日等元数据；首次使用决策功能前，可在持有页确认**实际份额、可选总成本和可选现金余额**。未确认的费用或份额保持 unknown，不按 0 猜测。
- 养基宝总览 OCR：识别多只基金、区分当日/持有收益（未收盘时当日列为 `-` 则不填当日收益）；**OCR 漏负号**时按收益率/账户总收益/独立行减号规则补符号（当日收益额、板块涨跌与收益率一致）。详见 `docs/PROJECT_CONTEXT.md`。
- **持有**页养基宝式账户汇总：基金代码、名称、金额、持有/当日收益、板块涨跌；**估算当日收益率**（≈ 板块涨跌 + 持有收益率，与传给模型一致）；**刷新后 instant 展示**（localStorage 缓存 + 服务端 `refreshed_at`）。
- 手动修改持有金额会同步重建估算份额、成本和收益率基线，后续净值/板块刷新不会跳回旧金额；已确认真实份额的仓位须通过同步加仓/减仓或重新确认份额对账，清仓使用删除持仓。
- **市场** Tab：子 Tab「主题板块 | 美股」；主题板块小倍式涨幅榜（~66 白名单、主力净流入、列头排序）；美股指数期货 + QDII 盘前参考。
- **盈亏分析**页：收益走势（我的收益 vs 沪深300）、盈亏日历、当日 TOP5、持仓分布图。
- 内置稳健型风控规则：最大浮亏线、单只基金集中度、偏定投、拒绝追高（规则守卫 + 传入 AI `profile`）。
- DeepSeek V4 Pro 生成日报；新闻来自东财/基金公告/宏观主题（可配置），生成前用 **Flash 按主题摘要**（`topic_briefs`），主报告使用有界预取证据。
- 报告含**逐基金操作建议**、**主题要闻摘要**与**新闻原文列表**；利好/利空标题须能对应原文（`news_citation` 守卫）。
- **后台异步分析**：点击「生成报告」后后台执行，右下角悬浮面板查看进度；完成后桌面通知（需开启通知权限）。
- **生产专职后台 Worker**：市场/板块刷新、基金板块预计算与启用时的 Prompt shadow 不再随每个 Uvicorn 进程重复启动；独立容器通过 MySQL/OS 全局 leader lease、会话保活和 PID 心跳 fail-closed 监督，部署必须等 Worker 健康并通过决策质量 dry-run。
- **按领域组织的接口边界**：Factor 证据、市场诊断和决策质量运维端点由独立 FastAPI Router 承载；前端把鉴权请求核心、Factor 证据、市场诊断拆成域模块，旧 `@/lib/api` 继续作为兼容门面。可展开诊断组件共用可取消、可重试的懒加载状态钩子，避免各自复制请求生命周期。
- **证据成熟度控制台**：盈亏分析页可查看 Worker、PIT 基金池/Factor IC、DecisionScore shadow 和决策质量前向标签的真实积累进度；缺失数据明确显示“尚无证据”，17.5/19.5 个月仅为理论最短研究窗口，任何状态都不会自动启用新模型。
- **不可变 NAV 首次观测账本**：交易日晚间从同一份公开基金目录同时捕获成员 PIT 与当时可见的净值；首次观测时间绝不回填，修订只追加新行，周度研究按历史截止时点读取最早可见版本。真实观测尚未形成至少 250 个可用因子点前继续使用既有 membership-PIT v3/v2，禁止用历史 NAV 冒充 observation PIT 或自动晋级。
- 日报与推荐基金主生成统一使用**深度分析**；旧快速报告仍可查看。
- 报告支持与上一份日报**对比差异**；可**导出 Markdown**；报告内 **调仓示意模拟**（按动作与集中度自动补算示意金额）。
- **报告追问**：日报阅读区提供按需对话抽屉（桌面右侧、移动端底部），支持 Markdown、快速/深度追问和深度模式按需补拉新闻；对话按报告存 SQLite，可**导出对话 Markdown**。
- API 支持 **SQLite 全库备份/恢复**；风控参数与追问模式保存在浏览器本地，风控画像同步至数据库。
- SQLite 本地保存历史日报；日报阅读区可通过上一份/下一份和历史抽屉检索、切换或删除，荐基历史使用独立的有界侧轨/抽屉工作区。
- 基金详情页：**业绩走势**（近1月～3年、本基金 vs 沪深300）、历史净值滚动加载；**持有天数**可点选首次购入日期；关联板块分时图。
- **邮箱注册/登录**（JWT，默认 30 天有效）；用户菜单 → **账号设置** 查看当前账号。
- **推荐基金** Tab：默认 **全市场机会** 模式，从 21 个板块热度 Top 8 建窄池并 AI 横向精选；可切换 **持仓缺口补充**；选基策略、关注方向（限时拉取 + 本地缓存）、预算、基金类型偏好；历史推荐、候选池、T+5/T+20/T+60 复盘与追问。详见下文「推荐基金」。
- **页面缓存：** 持有 localStorage 优先、Dashboard/市场子 Tab sessionStorage 记忆；盈亏分析、业绩走势、持仓详情等 SWR 缓存；板块涨跌后台轻量轮询，手动刷新走精确模式。
- **日报数据口径**：AI 分析使用 `estimated_holding_return_percent`，与持有页「持有」列一致（盘中含板块估算）。
- **调仓示意**：报告页展开「调仓示意模拟」；AI 未填金额时按集中度或减仓动作自动补算变动额。
- **决策准确性 V2**：日报与荐基会冻结决策时点持仓、数据证据、费用假设和基准映射，并按基金自身估值日分别评价毛方向、假设费后正收益、合同基准毛超额和合同基准假设费后超额；legacy 报告只作参考，不进入正式分母。完整契约见 [docs/DECISION_ACCURACY_V2.md](docs/DECISION_ACCURACY_V2.md)。
- **候选排序 D4（内部 shadow）**：冻结荐基 `prescreen` 全集、K=3 与保守次日入场的共同 T+20 路径；audit/outcome 使用数据库可见后的独立 receipt，日历/NAV 使用 live adapter stdout、请求参数、版本、解析与规范化 hash 绑定的追加式来源 receipt，只有完整 source-verified 标签才计算 Precision/NDCG/regret。它不同于报告 UI 的单基金 T+5/T+20/T+60 复盘，当前无普通用户界面、不调用 LLM 调参，也不会自动修改 Prompt、权重、Guard 或执行交易。详见 [准确性 V4 设计](docs/design/RECOMMENDATION_DAILY_ACCURACY_V4.md#phase-d4-提交时点与来源验真链2026-07-15)。
- **配对 Prompt D5（默认关闭、当前冻结休眠）**：原契约只覆盖荐基“全市场 + 快速 + 默认角色”；主扫描统一深度后不会再产生新 eligible 请求，既有审计样本不迁移、不冒充深度样本。完整边界见 [准确性 V4 设计](docs/design/RECOMMENDATION_DAILY_ACCURACY_V4.md)。

**主报告分析模式：** 日报与推荐基金的新生成请求固定使用深度分析（Pro + 有界扩展证据 + 可选审校）；旧客户端传 `fast` 时服务端会兼容升级，历史快速报告仍可读取。

**追问模式（报告页按需对话抽屉）：**

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
5. 点击「扫描今日机会」（固定深度分析）
6. 右下角 DiscoveryJobStatusFloat 查看进度；完成后查看推荐报告
7. 桌面历史侧轨或移动历史抽屉可检索、切换和删除往日报告；报告内可展开候选池与 T+N 复盘；点击推荐卡片可预览基金详情
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

**分析模式（扫描）：** 固定深度分析。扫描为后台异步任务，通过 `GET /api/jobs/{id}` 轮询状态；报告内追加提问仍可选择快速/深度。

**与日报的区别：**

| | 生成日报 | 推荐基金 |
|---|----------|----------|
| 分析对象 | 已有持仓 | 窄池新基候选 |
| AI 角色 | `analysis-prompt` | `discovery-prompt` |
| 输出 | `fund_recommendations` 调仓建议 | `FundDiscoveryReport` 荐基报告 |
| 历史 | 阅读区 `ReportNavigator` + `ReportHistoryDrawer` | `DiscoveryHistoryWorkspace` 有界侧轨/抽屉 |

API 与架构见 [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)（推荐基金 V2/V3、全市场扫描等行为以本文为准）。

## 目录

```text
apps/api        FastAPI 后端（app/routes 为领域路由，app/services 为领域服务）
apps/web        Next.js 前端（src/lib/api 为 API 域模块，src/lib/api.ts 为兼容门面）
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
FUND_AI_DEEPSEEK_MAX_TOKENS=32768
FUND_AI_DEEPSEEK_MAX_TOKENS_REPORT=32768
FUND_AI_DEEPSEEK_CONNECTION_RETRIES=2
# 可选的内部 paired Prompt 实验；默认关闭。密钥必须使用独立随机值且不得提交。
FUND_AI_PROMPT_SHADOW_ENABLED=false
FUND_AI_PROMPT_SHADOW_ASSIGNMENT_SECRET=
FUND_AI_PROMPT_SHADOW_SAMPLE_BASIS_POINTS=10000
FUND_AI_PROMPT_SHADOW_MAX_CHALLENGER_CALLS_PER_DAY=100
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
| `FUND_AI_MYSQL_SCHEMA_LOCK_TIMEOUT_SECONDS` | 多进程启动时 MySQL schema 锁等待秒数；默认 60，进程内并发自动合并 |
| `WEB_CONCURRENCY` | Uvicorn worker 进程数；4 核轻量服务器生产默认 2，本地开发仍为 1 |
| `FUND_AI_RUNTIME_ROLE` | `all`（本地默认）/`api`/`worker`；生产 Compose 已将请求与长期后台任务分离 |
| `FUND_AI_BACKGROUND_WORKER_*` | leader 锁等待/重试、心跳间隔及过期门槛；默认 `5/5/10/45` 秒 |
| `FUND_AI_BACKGROUND_WORKER_HEARTBEAT_PATH` | 可选心跳文件路径；生产/Cloud Compose 已固定到 API/Worker 共享的数据卷 |
| `FUND_AI_PORTFOLIO_MUTATION_LOCK_TIMEOUT_SECONDS` | 同账户持仓跨 worker 写锁等待秒数；默认 30，超时返回可重试的 503 |
| `FUND_AI_HOLDINGS_MEMORY_CACHE_ENABLED` | 持仓响应进程内缓存；MySQL 默认关闭，避免不同 worker 返回不同版本 |
| `FUND_AI_CLOUDBASE_ENV_ID` | CloudBase 环境 ID；用于 Web 静态托管域名 CORS 自动放行 |

`.env` 已被 `.gitignore` 忽略，不会提交到 Git。

DeepSeek 请求会复用进程级连接池；仅在 TCP/TLS 尚未建立时自动重试
`ConnectError/ConnectTimeout`，不会重放已经开始响应的模型请求。流式读取与同步调用统一
遵循 `FUND_AI_DEEPSEEK_TIMEOUT_SECONDS`。若仍出现 `read_timeout`，应检查真实报告负载、
服务商状态与网络链路，不建议把报告输出预算提高到模型的理论上限。

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

./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
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

Docker 镜像默认仍安装本地 OCR 作为云端 VLM 的故障回退。如果部署环境已明确只使用云端 VLM、并接受 VLM 不可用时截图识别暂时失败，可在构建时传入 `--build-arg INSTALL_LOCAL_OCR=false`，省去约 550 MiB 的 PaddleOCR 及其传递依赖；Compose 可设置同名环境变量。该选项不会影响文本录入和其他分析功能。

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

Windows 本地开发默认以单进程启动 API，避免 Uvicorn 热重载进程与行情子进程残留；
启动脚本会在 8000/3001 端口已占用时拒绝再启动一份。仅在主动修改 API 代码时可先设置
`FUND_AI_DEV_RELOAD=true` 启用热重载，退出 Git Bash 启动脚本时会清理本次启动的完整子进程树。

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
3. 预览确认后写入账户汇总并刷新查码与板块涨跌；首次启用决策前，在持有页确认实际份额、可选成本和现金账本基线
4. 「盈亏分析」查看收益走势、盈亏日历、当日 TOP5
5. 「市场」→ 主题板块涨幅榜 / 美股概览
6. 点击持仓行可查看基金详情（业绩走势、持有天数、板块分时）或登记包含实际确认份额的交易
7. 「推荐基金」→ 选择扫描模式（默认全市场）→ 可选关注板块、预算、**选基策略**、基金类型与荐基角色 → 扫描今日机会 → 查看报告 / 历史 / 候选池 / T+N 复盘 / 追问
8. 切到「生成日报」→ 确认风控画像与日报 AI 角色 → 生成深度日报
9. 右下角 JobStatusFloat 查看进度；完成后可在报告页追问（可选深度模式拉最新新闻），并在结果面板查看四项独立准确率指标
10. 换机迁移可调用数据库导出/导入 API；导入前务必保留原库备份
11. 需要留存时可导出日报 Markdown 或导出对话 Markdown
```

## 云端部署（可选）

面向 ≤5 人私有部署：FastAPI 云托管 + CloudBase MySQL + 静态 Web。完整步骤见 **[docs/deploy/cloudbase.md](docs/deploy/cloudbase.md)**。

```bash
# 本地验证 Docker 镜像
export FUND_AI_JWT_SECRET=your-secret-32chars-minimum
docker compose -f docker-compose.cloud.yml up --build
```

## 验证

后端单元测试（当前 **1998** 项，本机串行约 106s；默认离线 stub，不访问东财/AkShare/MySQL）：

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
export FUND_AI_FUND_NAME_PRELOAD_ENABLED=false
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
# 可选：截图验收（需本地开发服务器已启动）
node scripts/_verify-shots.mjs    # 落地页 / 登录 / 注册
node scripts/_verify-auth.mjs     # 注册并进入 App（Dashboard / 市场）
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

本项目面向个人自用。截图、数据库和上传文件默认保存在本地项目目录。DeepSeek 会收到你确认后的持仓、风控参数、净值摘要、主题新闻摘要（Flash 生成）、新闻标题/短摘要，以及已生成日报全文（追问时）。报告与对话只用于个人投研辅助，不构成投资建议，也不会执行任何交易。

**截图识别引擎与外传说明：** 「新增持有」截图识别默认 `FUND_AI_OCR_PROVIDER=auto`——配置 `FUND_AI_VLM_OCR_API_KEY`（阿里云百炼）后，截图图片会发往云端视觉模型 `qwen3-vl-flash` 做识别（更快更准、QDII/截不全更鲁棒），失败自动回退本地 PaddleOCR；未配置 key 时仅走本地。若不希望截图外传，设 `FUND_AI_OCR_PROVIDER=local` 强制本地识别。无论何种引擎，发给 DeepSeek 的始终是结构化持仓而非原始截图。
