# FundPilot AI — 项目上下文（给 AI / 新开发者）

> **用途：** 新对话或接手开发时先读本文，再按需打开具体文件。避免从零扫描仓库。
>
> **维护：** 功能或架构有实质变化时，同步更新「能力清单」「数据流」「API」「目录」「环境变量」。

**文档版本：** 2026-06-03（养基宝持仓看板、板块实时三层兜底、首页持久化恢复、UI 双栏布局）

---

## 一句话

**FundPilot AI** 是面向个人自用的本地基金投研助手：养基宝总览/详情截图 → OCR → **板块实时涨跌估算当日收益** → 校对持仓 → 稳健风控 → 东方财富新闻（AkShare）+ DeepSeek V4 生成**逐基金操作建议**日报；首页自动恢复持仓，点击刷新更新板块；点击「生成报告」后台异步执行，右下角悬浮面板查看进度。数据默认留在本机。

---

## 能力清单（当前已实现）

| 类别 | 能力 |
|------|------|
| 输入 | 养基宝总览 OCR（无代码草稿解析）；当日列为 `-` 时不填当日收益；**OCR 漏负号**时规则补符号；总览上传在「基金档案」Tab |
| 当日收益 | **刷新时按关联板块实时涨跌估算**：`holding_amount × sector_return%`；**忽略** OCR/截图中的当日利润；账户合计 = 各行之和 |
| 校对 | `HoldingTable` 含估算当日收益率；OCR 返回 `holding_warnings` / `holding_diffs`；**沿用上次基金列表** |
| 档案 | 详情截图建档；总览 OCR **自动同步**档案；`000000` 仅通过**已保存档案**按名称补码（不在分析/OCR 流程中 AkShare 自动查码） |
| 首页看板 | **今日** Tab：`YangjibaoHoldingsBoard` 养基宝式卡片；启动 `GET /api/portfolio/holdings` 恢复持仓并自动刷新板块；点击行打开 `YangjibaoFundDetail` |
| 仪表盘 | **仪表盘** Tab：`GET /api/portfolio/dashboard` — 资产/当日收益走势、持仓分布条 |
| 风控 | 浮亏线、单只集中度、定投偏好、拒绝追高（`InvestorProfile`） |
| 报告 | 组合摘要 + `fund_recommendations` + `topic_briefs` + `market_news`；`analysis_facts`；守卫 + 深度 `report_judge` |
| 今日工作台 | 双栏布局（大屏）：左侧 sticky 持仓看板，右侧工作流/风控/日报 |
| 复盘/模拟 | outcomes / outcomes-weekly / rebalance-simulation |
| 交易日语义 | `trading_session.py` + `trade_calendar_cache`（子进程拉日历，避免主进程 `py_mini_racer`）；`TradingSessionBar` |
| 穿透估算 | 未收盘时按板块权重分配账户当日收益 |
| 板块实时 | 东财 httpx 直连 + AkShare 子进程补全 + **单板块按需拉取**（`sector_on_demand`）；120s 自动 + 手动；低置信度 `SectorMappingModal`；分时 `GET /api/sector-quotes/intraday` |
| 阻塞清单 | `TodayBlockingChecklist` + `workflowBlockers` |
| 数据备份 | SQLite export/import；`DatabaseBackupPanel` |
| CI / E2E | GitHub Actions：pytest（**130** 项）+ lint/typecheck/build + Playwright |
| 基金诊断 | AkShare 概况/累计收益；详情页可 AkShare **按名称查码**并持久化 |
| 分析模式 | 快速 / 深度 |
| 体验 | 报告 diff、Markdown 导出、档案 JSON、桌面通知、Plus Jakarta 字体 UI |
| 报告追问 | SSE + ChatMarkdown |
| 异步分析 | `/api/analyze/async` + `JobStatusFloat` |
| 前端偏好 | localStorage：风控、分析模式、板块自动刷新 |

---

## 产品边界

| 会做 | 不会做 |
|------|--------|
| OCR、校对、风控、AI 日报、示意金额 | 自动下单、券商对接、多用户 SaaS |
| 本地 SQLite / 上传目录 | 默认把原始截图发往云端 |
| 公开新闻标题/摘要供模型参考 | 投资建议（报告须有 caveats） |

**隐私：** DeepSeek 收到**结构化持仓、风控、净值摘要、新闻标题/摘要**，不传原始截图。见 `README.md`「隐私和边界」。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js、React、TypeScript、Tailwind、Lucide；浏览器 `Notification` |
| 后端 | FastAPI、Pydantic v2、uvicorn；`lifespan` 可选 DB 自动导入 |
| 存储 | SQLite：`reports`、`fund_profiles`、`ocr_text_cache`、`analysis_jobs`、`report_chat_messages`、`portfolio_*`、`news_cache` |
| AI | DeepSeek API；`fetch_market_news` Function Calling |
| OCR（可选） | PaddleOCR |
| 数据 | AkShare：净值 + `stock_news_em` / 基金公告 |

环境变量：`FUND_AI_*`、 `NEXT_PUBLIC_API_BASE_URL`。模板：`.env.example`。

---

## 仓库结构

```text
fundpilot-ai/
├── apps/api/app/
│   ├── main.py              # 路由
│   ├── lifespan.py          # 启动时可选 DB 自动导入
│   ├── config.py / models.py / database.py
│   └── services/
│       ├── ocr_engine.py / ocr_parser.py / ocr_pipeline.py / overview_pipeline.py
│       ├── portfolio_parser.py / portfolio_snapshot.py / portfolio_holdings_service.py
│       ├── holding_validation.py / holding_metrics.py / holding_estimates.py / holding_detail_service.py
│       ├── sector_quote_service.py / sector_quote_provider.py / sector_quote_resolver.py
│       ├── eastmoney_spot_client.py / akshare_spot_client.py / sector_on_demand.py / sector_intraday_provider.py
│       ├── trade_calendar_cache.py / sector_labels.py / sector_quote_cache.py
│       ├── fund_code_resolver.py / fund_name_utils.py
│       ├── deepseek_http.py / fund_profile.py / risk.py / fund_data.py
│       ├── recommendation_guard.py / analysis_facts.py / news_citation.py
│       ├── recommendation_outcomes.py / rebalance_simulator.py / report_judge.py
│       ├── news_service.py / news_summarizer.py / news_cache.py
│       ├── penetration_daily_allocator.py / market_signal.py / trading_session.py
│       ├── db_backup.py
│       ├── job_store.py           # 异步分析任务（含 stage）
│       ├── report_diff.py / report_export.py
│       ├── report_chat.py         # 追问 SSE + Tool 轮次
│       ├── report_chat_runtime.py # 追问 fast/deep
│       ├── report_chat_export.py  # 对话 Markdown
│       ├── deepseek_client.py / analysis_runtime.py / analyze_pipeline.py
│       └── recommendations.py
├── apps/web/src/
│   ├── lib/api.ts / storage.ts / holdingMetrics.ts / useSectorQuoteRefresh.ts / workflowBlockers.ts
│   └── components/
│       ├── Dashboard.tsx          # 今日 / 仪表盘 / 基金档案 / 历史
│       ├── YangjibaoHoldingsBoard / YangjibaoFundDetail / SectorMappingModal / IntradayPercentChart
│       ├── TradingSessionBar / TodayBlockingChecklist / DatabaseBackupPanel
│       ├── PortfolioDashboard / UploadDropzone / HoldingTable / RiskControls
│       ├── ReportPanel / JobStatusFloat / HistoryRail / FundProfilePanel
├── uploads/
├── data/app.db
├── scripts/dev.sh / dev.ps1
├── docs/PROJECT_CONTEXT.md   # 本文
└── README.md
```

---

## 推荐使用流程

```text
1. bash scripts/dev.sh → 打开 http://127.0.0.1:3000（默认「今日」Tab）
2. 首页自动恢复上次持仓；点刷新更新板块涨跌 → 当日收益按板块估算
3. 需更新金额时 →「基金档案」上传养基宝总览 OCR，或展开校对表手动改
4. 校对 → 选快速/深度 →「生成报告」→ JobStatusFloat 进度 → 今日日报
5. 点击持仓行 → 基金详情（净值、板块分时）；低置信度板块 → 映射弹窗
```

### 基金档案与持仓总览

```text
profiles 页 → 养基宝总览截图 → POST /api/ocr → sync_profiles + sector_refresh
profiles 页 → 单基金详情截图 → POST /api/fund-profiles/ocr
打开应用 → GET /api/portfolio/holdings → 恢复 holdings + 可选自动 refresh-sector-quotes
```

设计说明见 `docs/design/2026-06-01-portfolio-holdings.md`。

### 养基宝 OCR：负号与符号一致性（2026-06-01）

养基宝亏损为**绿色 + 减号**；PaddleOCR 常只识别数字，导致「当日收益额」「板块涨跌」为正而「当日收益率」已为负。

`ocr_parser.py` 在解析后依次：

1. **独立行减号**：`-` 单独成行时，绑定下一行数字/百分比。
2. **行内对齐**：`daily_profit` / `holding_profit` 与对应收益率同号；`sector_return_percent` 与 `daily_return_percent` 同号。
3. **账户级校验**：顶部「当日收益」为负且各行金额加总符号矛盾时，批量修正当日收益额符号。
4. **两种列表版式**：区分 ￥ 前「当日+持有」双金额 vs 仅「当日」单金额，避免误把持有收益当日化。

回归：`tests/fixtures/yangjibao_overview_signed_daily_ocr.txt`、`test_parse_overview_restores_negative_daily_profit_and_sector_when_ocr_drops_signs`。

---

## 核心业务流

### 同步分析（兜底，前端不主动调用）

```text
POST /api/analyze
  → FundProfileService.resolve_holdings
  → evaluate_portfolio_risk
  → FundDataService.get_snapshots
  → DeepSeekClient.generate_report（analysis_mode: fast | deep）
  → save_report
```

### 异步分析（主流程）

```text
POST /api/analyze/async → job_id
  → 线程池 run_analysis()
  → GET /api/jobs/{id} 轮询（JobStatusFloat，1.5s；含 stage_label）
  → status=completed 时含 report → onComplete 回调 → 切换报告 Tab
```

---

## 分析模式：快速 vs 深度

| | 快速 `fast` | 深度 `deep` |
|---|-------------|-------------|
| 模型 | `deepseek-v4-flash` | `.env` 中 `FUND_AI_DEEPSEEK_MODEL`（默认 pro） |
| 新闻预取 | 有，主题数 ≤3 | 有，按 `NEWS_MAX_TOPICS` |
| `fetch_market_news` Tool | **关闭**（`news_tool_max_rounds=0`） | 可开启（按 `NEWS_TOOL_MAX_ROUNDS`） |
| 适用 | 交易日赶时间 | 需要模型主动补新闻 |

实现：`analysis_runtime.resolve_analysis_runtime()`，请求字段 `AnalysisRequest.analysis_mode`。

---

## 报告追问：快速 vs 深度

| | 快速 `fast` | 深度 `deep` |
|---|-------------|-------------|
| 模型 | `deepseek-v4-flash` | `.env` 中 `FUND_AI_DEEPSEEK_MODEL` |
| 上下文 | 已生成日报 Markdown + 历史对话 | 同上 |
| `fetch_market_news` | **关闭** | 按需调用（受 `NEWS_TOOL_MAX_ROUNDS` 限制） |
| 传输 | SSE：`user_message` → `status`（深度）→ `token` → `done` | 同上 |
| 存储 | SQLite `report_chat_messages`，按 `report_id` | 同上 |

实现：`report_chat_runtime.resolve_report_chat_runtime()`；`POST /api/reports/{id}/chat` body 含 `chat_mode`。

```text
POST /api/reports/{id}/chat  { message, chat_mode }
  → save user message
  → [deep] 非流式 Tool 轮次（fetch_market_news）
  → 流式 chat/completions
  → save assistant message
```

---

## HTTP API

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/ocr` | 截图/文本 → holdings |
| POST | `/api/analyze` | 同步生成 Report（兜底） |
| POST | `/api/analyze/async` | `{ job_id, status }` |
| GET | `/api/trading-session` | 交易日/收盘窗口语义 |
| GET | `/api/reports/{id}/outcomes-weekly?days=7` | 7 日建议复盘 |
| GET | `/api/database/export` | 下载 SQLite |
| POST | `/api/database/import` | 上传替换 DB（自动备份 `.db.bak`） |
| GET | `/api/jobs/{id}` | 任务状态；含 `stage`/`stage_label`/`analysis_mode`；完成时含 `report` |
| GET | `/api/reports` | 最近 50 条 |
| GET | `/api/reports/{id}` | 详情 |
| DELETE | `/api/reports/{id}` | 删除 |
| GET | `/api/reports/{id}/diff` | 与上一份对比 |
| GET | `/api/reports/{id}/markdown` | 导出 Markdown |
| GET | `/api/reports/{id}/chat` | 报告追问历史 |
| POST | `/api/reports/{id}/chat` | SSE 流式追问（body: `{ message, chat_mode }`） |
| GET | `/api/reports/{id}/chat/markdown` | 导出追问对话 Markdown |
| GET/POST | `/api/fund-profiles/export` `import` | 档案 JSON |
| POST | `/api/fund-profiles/ocr` | 详情页建档 |
| GET | `/api/fund-profiles` | 列表 |
| GET | `/api/fund-profiles/{code}/nav-history?days=` | 单位净值走势（AkShare，默认 90 交易日） |
| POST | `/api/holdings/refresh-sector-quotes` | 刷新板块涨跌；返回 `sector_quote_meta`、映射候选 |
| POST | `/api/sector-mappings/apply` | 持久化板块映射选择 |
| GET | `/api/sector-quotes/status` | 自动刷新开关/间隔/交易时段 |
| GET | `/api/sector-quotes/intraday` | 板块分时涨跌 |
| POST | `/api/holdings/detail` | 单只持仓详情（含 AkShare 查码、净值） |
| GET | `/api/portfolio/holdings` | 恢复首页持仓（快照优先，否则档案） |
| GET | `/api/portfolio/summary` | 账户汇总 + 全部档案 |
| GET | `/api/portfolio/dashboard` | 走势历史 + 持仓分布 |
| GET | `/api/reports/{id}/outcomes` | 上一份日报建议复盘 |
| GET | `/api/reports/{id}/rebalance-simulation` | 按报告示意金额模拟调仓 |

前端封装：`apps/web/src/lib/api.ts`。

---

## 领域模型（摘要）

| 模型 | 要点 |
|------|------|
| **Holding** | 6 位代码、金额、持有/当日收益、板块；模型侧见 `holding_analysis_payload` |
| **InvestorProfile** | 稳健默认；浮亏 8%、集中度 35% |
| **FundRecommendation** | action、amount_*、news_bullish/bearish、points |
| **NewsItem** | topic、title、is_today |
| **Report** | 含 `fund_recommendations`、`market_news`、`topic_briefs`、`analysis_facts`；`market_context` 保留字段恒 `[]` |
| **AnalysisRequest** | holdings、profile、ocr_text、**analysis_mode** |
| **ChatMessage** | report_id、role、content |
| **ReportChatRequest** | message、**chat_mode**（fast \| deep） |

占位码 `000000`：总览 OCR 无代码时，**仅**通过已保存 `FundProfile` 按名称补全；未知代码分析时保留 `yangjibao-ocr` 快照。用户在详情页打开基金时可 AkShare 按名称查码。

### 板块实时行情（2026-06-03）

| 层级 | 实现 |
|------|------|
| 1 | `eastmoney_spot_client` httpx 直连 push2（多 host、分页保留部分结果、`trust_env=False`） |
| 2 | `akshare_spot_client` 子进程补全稀疏/空 concept/industry |
| 3 | `sector_on_demand` 单板块 AkShare 子进程（如商业航天） |
| 解析 | `sector_quote_resolver` 自动匹配中证人工智能、电网→电力设备主题、半导体、商业航天等 |
| 缓存 | `sector_quote_cache` 按日 TTL；`force_refresh` 跳过持久化映射 |

设计说明：`docs/design/2026-06-02-live-sector-quotes.md`。

### 养基宝收益率语义（传给 DeepSeek / 首页展示）

| 字段 | 含义 |
|------|------|
| `sector_return_percent` | 关联板块**当日**实时涨跌 |
| `holding_return_percent` | 持有收益率，多为**昨日结算** |
| `daily_return_percent` | 明确当日基金收益率（有则优先） |
| `estimated_daily_return_percent` | 无当日时 ≈ `sector_return_percent + holding_return_percent`（估算，须在报告中注明） |

实现：`app/services/holding_metrics.py`；`deepseek_client._user_payload` 含 `holding_return_semantics`。

### 净值走势摘要（传给 DeepSeek，非完整 K 线）

生成报告时 `FundDataService.get_snapshots_with_nav_trends` 与 AkShare 快照**同一次拉取**近 N 日净值，经 `nav_trend_summary.summarize_nav_history` 压缩后写入 `analysis_facts.holdings[].nav_trend`：

| 字段 | 含义 |
|------|------|
| `period_change_percent` | 区间内涨跌幅 |
| `recent_5d_change_percent` | 近 5 交易日涨跌 |
| `distance_from_high_percent` / `distance_from_low_percent` | 距区间高/低点 |
| `trend_label` | 区间 + 近 5 日综合标签（如「区间震荡，近5日走弱」） |
| `recent_nav_series` | 最近若干日 `date` + `nav` 采样（默认 8 点） |

配置：`FUND_AI_NAV_TREND_DAYS`（默认 66）、`FUND_AI_NAV_TREND_RECENT_SAMPLE`（默认 8）。前端 `GET /api/fund-profiles/{code}/nav-history` 仍用于完整折线图，与 AI 摘要独立。

---

## 新闻与 DeepSeek

- **数据源（`FUND_AI_NEWS_SOURCES`）：** `eastmoney`（东财 `stock_news_em`）、`announcement`（基金公告）、`macro`（宏观主题，默认「上证指数」）。
- **预取：** `NewsService.prefetch_for_holdings` → `market_news`（标题 + ≤200 字 snippet + 链接）。
- **按主题摘要：** `news_summarizer.summarize_all_topics`（Flash，每主题 1 次）→ `topic_briefs`；失败 → `rule-fallback`；关闭：`FUND_AI_NEWS_SUMMARIZE=false`。
- **喂模型：** `_user_payload` 含 `topic_briefs` + `prefetched_news`；`news_citation` 校验利好/利空须命中原文标题或 `source_titles`。
- **Tool：** 仅深度模式且 `news_tool_max_rounds > 0` 时注册 `fetch_market_news`（默认最多 3 轮）；Tool 补拉后 `merge_topic_briefs` 增量摘要。
- **缓存：** `news_cache` 表按 `topic+date` 同日复用。
- **兜底：** JSON 解析失败 → `_offline_report` + `recommendations.enrich_*`。

---

## 前端要点

- **今日 Tab：** `YangjibaoHoldingsBoard` + `useSectorQuoteRefresh`（120s 自动刷新）；大屏 `xl:grid-cols` 双栏。
- **基金档案 Tab：** `UploadDropzone` 总览 OCR；`FundProfilePanel` 详情建档。
- **分析：** `ReportPanel` + `JobStatusFloat` 异步轮询。
- **偏好：** `lib/storage.ts`（profile、analysisMode、sectorAutoRefresh）。

---

## 环境变量

### 板块实时

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_SECTOR_QUOTES_ENABLED` | true | 关闭则不走 live 板块 |
| `FUND_AI_SECTOR_QUOTES_TTL_SECONDS` | 60 | spot 缓存 TTL |
| `FUND_AI_SECTOR_QUOTES_AUTO_INTERVAL_SECONDS` | 120 | 前端自动刷新间隔 |
| `FUND_AI_SECTOR_QUOTES_DISCREPANCY_WARN` | 0.5 | OCR vs 实时板块相差阈值（百分点） |

### DeepSeek / 新闻

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_DEEPSEEK_API_KEY` | — | 无/占位符则离线；校验见 `config.normalize_deepseek_api_key` |
| `FUND_AI_DEEPSEEK_MODEL` | deepseek-v4-pro | 深度模式模型 |
| `FUND_AI_DEEPSEEK_MODEL_FAST` | deepseek-v4-flash | 快速模式（日报/追问） |
| `FUND_AI_DEEPSEEK_TIMEOUT_SECONDS` | 300 | 读超时 |
| `FUND_AI_NEWS_ENABLED` | true | 关闭则不注册 Tool |
| `FUND_AI_NEWS_TOOL_MAX_ROUNDS` | 3 | Tool 轮数上限 |
| `FUND_AI_NEWS_SOURCES` | eastmoney,announcement,macro | 新闻源 |
| `FUND_AI_NEWS_SUMMARIZE` | true | Flash 按主题摘要 |
| `FUND_AI_NEWS_MACRO_TOPIC` | 上证指数 | 宏观检索主题 |
| `FUND_AI_NAV_TREND_DAYS` | 66 | 报告生成时拉取净值交易日数 |
| `FUND_AI_NAV_TREND_RECENT_SAMPLE` | 8 | `nav_trend.recent_nav_series` 采样点数 |
| `FUND_AI_NEWS_REQUIRE_TODAY_FOR_ADD` | true | 无当日新闻时守卫压过加仓建议 |
| `FUND_AI_DB_AUTO_IMPORT_PATH` | — | 启动时若文件存在则自动导入 DB（会先备份当前库） |

修改 `.env` 后需重启 API。

---

## 本地开发

```bash
cd /d/Code/HL_Project/fundpilot-ai
bash scripts/dev.sh    # 或 scripts/dev.ps1
```

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q   # 当前 130 项
cd apps/web && npm run lint && npm run typecheck && npm run build
cd apps/web && npm run test:e2e   # Playwright 冒烟
```

---

## 给 AI 的修改建议

1. 改 API：`models.py` → `main.py` → `api.ts` → 组件 → `tests/`。
2. 改报告结构：同步 `deepseek_client` JSON、`recommendations`、`_offline_report`、`Report` 类型。
3. 改异步流程：`job_store.py`（后端）→ `JobStatusFloat.tsx`（前端轮询）→ `Dashboard.tsx`（回调）。
4. 改追问：`report_chat.py` / `report_chat_runtime.py` → `main.py` chat 路由 → `ReportChatPanel.tsx` / `ChatMarkdown.tsx` → `tests/test_report_chat*.py`。
5. 改 OCR/估算收益：`ocr_parser.py` → `holding_metrics.py` → `HoldingTable` / `holdingMetrics.ts` → `tests/test_ocr_parser.py`、`tests/fixtures/`。
6. 历史 MVP 计划：`docs/superpowers/plans/2026-05-31-remove-automation-async-float.md`（以代码为准）。

---

## 文档索引

| 文件 | 内容 |
|------|------|
| `README.md` | 安装、启动、环境变量、流程 |
| `docs/PROJECT_CONTEXT.md` | 本文 |
| `docs/design/2026-06-01-portfolio-holdings.md` | 持仓档案、总览同步、OCR 验收 |
| `docs/design/2026-06-02-news-sources-and-topic-briefs.md` | 多源新闻 + 主题摘要设计 |
| `docs/design/2026-06-02-live-sector-quotes.md` | 板块实时行情、映射与兜底 |
| `.env.example` | 环境变量模板 |
