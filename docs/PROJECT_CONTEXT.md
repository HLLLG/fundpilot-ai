# FundPilot AI — 项目上下文（给 AI / 新开发者）

> **用途：** 新对话或接手开发时先读本文，再按需打开具体文件。避免从零扫描仓库。
>
> **维护：** 功能或架构有实质变化时，同步更新「能力清单」「数据流」「API」「目录」「环境变量」。

**文档版本：** 2026-06-08（支付宝持仓 OCR；业绩走势；持有天数；分时/净值图表优化）

**更新记录：**
- **支付宝持仓 OCR（2026-06-08）：** 支持上传支付宝「我的基金」列表截图；`alipay_holdings_parser.py` 解析三列交错 OCR 文本并自动匹配基金代码；`POST /api/ocr?preview=true` 预览、`POST /api/portfolio/apply-holdings` 确认写入；OCR 预热与 mobile 模型加速（`.env` `FUND_AI_OCR_*`）。
- **业绩走势（2026-06-08）：** 基金详情「业绩走势」Tab：近1月/3月/6月/1年/3年区间；本基金 vs 沪深300 区间涨跌对比折线图；成本价图例；历史净值表默认近1月预览，「查看历史净值」弹窗滚动分页加载（`GET /api/fund-profiles/{code}/nav-history/page`）；沪深300 日线优先新浪接口（`index_daily_client.py`），AkShare 备用。
- **持有天数（2026-06-08）：** 详情页点击「持有天数」弹出滚轮日期选择器设置首次购入日（`PATCH /api/fund-profiles/{code}` `first_purchase_date`）；OCR 详情天数随日历递增；持仓明细网格默认收起。
- **图表体验（2026-06-08）：** 分时图边框/虚线基准/十字辅助线；业绩走势图细线、Y 轴留白；日涨幅 `0%` 正确展示（修复 AkShare 日增长率为 0 时被丢弃）。
- **官方净值当日收益（2026-06-07）：** NAV 发布后 `daily_return_percent` 用官方日增长率，`daily_profit = 现金额 × r / (100 + r)`（结算前金额 × 涨幅，对齐支付宝）；`sector_return_percent` 仍仅展示东财板块涨跌；前端刷新不再用板块覆盖官方净值；账户汇总展示「昨日收益」。
- **持有收益展示（2026-06-07）：** 盘中 `持有收益 ≈ 昨日结算 + 板块涨跌`；官方净值公布后直接使用 OCR/档案中的含当日总值，不再叠加当日收益。
- **文档整理（2026-06-06）：** 合并历史迭代要点；`docs/design/` 仅保留分时 push2 运维 runbook，其余设计稿删除，以本文为准。
- **官方净值收益：** 收盘后以官方 T-day NAV 收益率替换板块估算；三层源标签（板块实时 / 收盘估算 / 官方净值）；修复周末日期回溯。
- **板块 canonical：** 养基宝常见板块名 → 东财 `secid` 硬编码映射（`sector_canonical.py`）；涨跌与分时统一走 push2 K 线。
- **分时 / push2：** 见 [design/2026-06-04-eastmoney-intraday-troubleshooting.md](design/2026-06-04-eastmoney-intraday-troubleshooting.md)（931994 电网设备、push2delay、骨架点与小数形式防御）。

---

## 一句话

**FundPilot AI** 是面向个人自用的本地基金投研助手：养基宝总览/详情截图 → OCR → **板块实时涨跌估算当日收益** → 校对持仓 → 稳健风控 → 东方财富新闻（AkShare）+ DeepSeek V4 生成**逐基金操作建议**日报；首页自动恢复持仓，点击刷新更新板块；点击「生成报告」后台异步执行，右下角悬浮面板查看进度。数据默认留在本机。

---

## 能力清单（当前已实现）

| 类别 | 能力 |
|------|------|
| 输入 | 养基宝总览 OCR（无代码草稿解析）；**支付宝持有列表 OCR**（预览确认后写入）；当日列为 `-` 时不填当日收益；**OCR 漏负号**时规则补符号；总览上传在「基金档案」Tab |
| 当日收益 | 盘中/净值未公布：**板块涨跌估算**（`holding_amount × sector_return%`）；NAV 发布后：**官方日增长率** + `daily_profit = amount × r / (100 + r)`；关联板块列始终东财涨跌；账户汇总附「昨日收益」 |
| 校对 | `HoldingTable` 含估算当日收益率；OCR 返回 `holding_warnings` / `holding_diffs`；**沿用上次基金列表** |
| 档案 | 详情 OCR 解析「场内指数 + 关联板块」；拒绝 `+`/`-`/Tab 标签误存为板块名；`POST /api/fund-profiles/repair-sectors` 清理历史脏数据；读取/保存时 `_sanitize_profile_sector_fields`；总览自动同步档案；`000000` 靠档案按名称补码 |
| 首页看板 | **今日** Tab：`YangjibaoHoldingsBoard` 养基宝式卡片（含支付宝截图上传）；启动 `GET /api/portfolio/holdings` 恢复持仓并自动刷新板块；点击行打开 `YangjibaoFundDetail` |
| 基金详情 | 关联板块分时图（边框/十字线）；**业绩走势**（区间涨跌 vs 沪深300、历史净值分页）；**我的收益**；持有天数滚轮选购入日；持仓明细默认收起 |
| 仪表盘 | **仪表盘** Tab：`GET /api/portfolio/dashboard` — 资产/当日收益走势、持仓分布条 |
| 风控 | 浮亏线、单只集中度、定投偏好、拒绝追高（`InvestorProfile`） |
| 报告 | 组合摘要 + `fund_recommendations` + `topic_briefs` + `market_news`；`analysis_facts`；守卫 + 深度 `report_judge` |
| 今日工作台 | 双栏布局（大屏）：左侧 sticky 持仓看板，右侧工作流/风控/日报 |
| 复盘/模拟 | outcomes / outcomes-weekly / rebalance-simulation |
| 交易日语义 | `trading_session.py` + `trade_calendar_cache`（子进程拉日历，避免主进程 `py_mini_racer`）；`TradingSessionBar` |
| 穿透估算 | 未收盘时按板块权重分配账户当日收益 |
| 板块实时 | **canonical 映射优先**（`sector_canonical` → 东财 `secid` K 线）；未知板块再走 spot 批量表 + `sector_quote_resolver` + `sector_on_demand`；可选中继/浏览器命令；300s 自动 + 手动；低置信度 `SectorMappingModal`；有场内指数时优先指数口径（`sector_quote_lookup_label`） |
| 分时图 | `GET /api/sector-quotes/intraday`；push2delay 首选；相对**昨收**对齐养基宝；骨架点 &lt;30 不写缓存；可选 `sector_intraday_browser_command` 浏览器兜底 |
| 官方净值 | AkShare `fund_open_fund_info_em` 覆盖**当日收益**（非板块列）；源标签：板块实时 / 收盘估算 / 官方净值；昨日收益取再上一交易日官方净值或 OCR |
| 阻塞清单 | `TodayBlockingChecklist` + `workflowBlockers` |
| 数据备份 | SQLite export/import；`DatabaseBackupPanel` |
| CI / E2E | GitHub Actions：pytest（**221** 项）+ lint/typecheck/build + Playwright |
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
│       ├── ocr_engine.py / ocr_parser.py / ocr_pipeline.py / alipay_holdings_parser.py / overview_pipeline.py
│       ├── index_daily_client.py   # 沪深300等指数日线（新浪优先）
│       ├── portfolio_parser.py / portfolio_snapshot.py / portfolio_holdings_service.py
│       ├── holding_validation.py / holding_metrics.py / holding_estimates.py / holding_detail_service.py
│       ├── sector_quote_service.py / sector_quote_provider.py / sector_quote_resolver.py / sector_canonical.py
│       ├── fund_nav_service.py / eastmoney_spot_client.py / eastmoney_trends_client.py
│       ├── akshare_spot_client.py / sector_on_demand.py / sector_intraday_provider.py
│       ├── sector_intraday_browser_provider.py / sector_quote_browser_provider.py / sector_quote_relay_provider.py
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
│       ├── YangjibaoHoldingsBoard / YangjibaoFundDetail / AlipayOcrConfirmModal
│       ├── PerformanceTrendPanel / PerformanceReturnChart / NavHistoryListModal / WheelDatePicker
│       ├── SectorMappingModal / IntradayPercentChart
│       ├── TradingSessionBar / TodayBlockingChecklist / DatabaseBackupPanel
│       ├── PortfolioDashboard / HoldingTable / RiskControls
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
5. 点击持仓行 → 基金详情（板块分时、业绩走势、我的收益）；低置信度板块 → 映射弹窗
6. 今日页可上传**支付宝持有列表**截图 → 预览确认 → 写入持仓
```

### 基金档案与持仓总览

```text
profiles 页 → 养基宝总览截图 → POST /api/ocr → sync_profiles + sector_refresh
profiles 页 → 单基金详情截图 → POST /api/fund-profiles/ocr
今日页 → 支付宝持有列表截图 → POST /api/ocr?preview=true → POST /api/portfolio/apply-holdings
打开应用 → GET /api/portfolio/holdings → 恢复 holdings + 可选自动 refresh-sector-quotes
```

### 基金详情：业绩走势与持有天数

```text
业绩走势 Tab → 默认近3月；切换近1月/6月/1年/3年；蓝线本基金、橙线沪深300
下方近1月净值预览 →「查看历史净值」→ 滚动加载更早记录（每页 30 条）
点击「持有天数」→ 滚轮选择首次购入日 → PATCH /api/fund-profiles/{code} → 天数按日历递增
```

**档案合并规则：** 总览有、档案无 → 自动简略档案（`is_provisional`）；总览消失 → 保留档案不删；总览更新金额/收益/板块，不覆盖详情才有的份额/成本/持有天数。

### 养基宝 OCR：负号与符号一致性

养基宝亏损为绿色减号；PaddleOCR 常漏负号。`ocr_parser.py` 规则层补符号（独立行 `-`、收益额与收益率对齐、账户总收益交叉校验、双/单金额版式）。回归：`tests/fixtures/yangjibao_overview_signed_daily_ocr.txt`。

---

## 核心业务流

### 同步分析（兜底，前端不主动调用）

```text
POST /api/analyze
  → FundProfileService.resolve_holdings
  → evaluate_portfolio_risk
  → FundDataService.get_snapshots_with_nav_trends
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
| POST | `/api/ocr` | 截图/文本 → holdings；`preview=true` 仅解析不写入；支持支付宝列表 |
| POST | `/api/portfolio/apply-holdings` | 确认 OCR 预览结果写入持仓与快照 |
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
| GET | `/api/fund-profiles/{code}/nav-history?days=` | 单位净值走势（AkShare，最长约 800 交易日，含日增长率） |
| GET | `/api/fund-profiles/{code}/nav-history/page` | 历史净值分页（`limit`、`before_date`，最新在前） |
| PATCH | `/api/fund-profiles/{code}` | 更新档案字段（如 `first_purchase_date` 首次购入日） |
| GET | `/api/market/index-daily?symbol=000300&days=` | 指数日线（沪深300 等，新浪优先） |
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
| **Holding** | 6 位代码、金额、持有/当日/昨日收益、板块；`sector_return_percent_source`（realtime / closing_estimate）；`daily_return_percent_source`（sector_estimate / official_nav）；`yesterday_profit`；见 `holding_analysis_payload` |
| **InvestorProfile** | 稳健默认；浮亏 8%、集中度 35% |
| **FundRecommendation** | action、amount_*、news_bullish/bearish、points |
| **NewsItem** | topic、title、is_today |
| **Report** | 含 `fund_recommendations`、`market_news`、`topic_briefs`、`analysis_facts`；`market_context` 保留字段恒 `[]` |
| **AnalysisRequest** | holdings、profile、ocr_text、**analysis_mode** |
| **ChatMessage** | report_id、role、content |
| **ReportChatRequest** | message、**chat_mode**（fast \| deep） |

占位码 `000000`：总览 OCR 无代码时，**仅**通过已保存 `FundProfile` 按名称补全；未知代码分析时保留 `yangjibao-ocr` 快照。用户在详情页打开基金时可 AkShare 按名称查码。

### 板块实时行情

**解析优先级：**

1. **Canonical（首选）** — `sector_canonical.get_canonical_sector`：养基宝常见名（商业航天、半导体、中证电网设备等）→ 固定东财 `secid`；涨跌经 `prefetch_canonical_kline_quotes` 拉 K 线收盘涨跌幅。
2. **持久化映射** — SQLite `sector_mappings`（用户曾在 `SectorMappingModal` 点选）。
3. **Spot 批量表** — `eastmoney_spot_client` push2 全市场 concept/industry/index；`sector_quote_resolver` 模糊匹配。
4. **按需补拉** — `sector_on_demand` 单板块 AkShare 子进程（短预算刷新会跳过）。
5. **可选中继/浏览器** — `sector_quote_relay_provider`、`sector_quote_browser_provider`（板块 spot）；`sector_intraday_browser_provider`（分时 push2 全断时）。
6. **兜底** — `fund_estimate_provider` 天天基金估值；前端标记「估值兜底」，不当作真实板块行情。

| 项 | 说明 |
|----|------|
| 场内指数 | 有 `intraday_index_name` 时优先用指数口径（`sector_quote_lookup_label`） |
| 快刷预算 | `/api/holdings/refresh-sector-quotes` 前端同步 5s；短预算下东财单 host 0.5s，跳过 AkShare 慢路径 |
| 缓存 | `sector_quote_cache` 按日 TTL；`force_refresh` 跳过持久化映射 |
| 元数据 | 响应含 `provider_path`、`from_stale_cache`、`summary.secid_matched` / `board_matched` / `estimate_fallback` |
| 分时 | `eastmoney_trends_client` + `sector_intraday_provider`；换机排查见 [design/2026-06-04-eastmoney-intraday-troubleshooting.md](design/2026-06-04-eastmoney-intraday-troubleshooting.md) |

### 养基宝收益率语义（传给 DeepSeek / 首页展示）

| 字段 | 含义 |
|------|------|
| `sector_return_percent` | 关联板块/场内指数**当日**东财涨跌（展示用，不用官方净值替换） |
| `sector_return_percent_source` | `"realtime"` 板块实时 / `"closing_estimate"` 收盘估算 |
| `daily_return_percent` | 当日基金收益率：官方净值或板块估算 |
| `daily_return_percent_source` | `"sector_estimate"` 板块估算 / `"official_nav"` 官方净值 |
| `daily_profit` | 当日收益额；官方净值时 `amount × r / (100 + r)`，盘中估算时 `amount × sector% / 100` |
| `yesterday_profit` | 再上一交易日官方净值收益（或 OCR）；账户汇总「昨」行 |
| `holding_return_percent` | 持有收益率；OCR 多为**昨日结算**；净值公布后展示层用含当日总值 |
| `estimated_daily_return_percent` | 无 `daily_return_percent` 时 ≈ `sector_return_percent + holding_return_percent`（估算） |

实现：`holding_estimates.py`（展示层收益计算）、`holding_metrics.py`（报告语义）、`holdingMetrics.ts`（前端镜像）；`deepseek_client._user_payload` 含 `holding_return_semantics`。

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

### 官方净值收益覆盖（2026-06-07）

**背景：** 养基宝收盘后仅显示板块涨幅估算；NAV 发布前（通常 ~21:00）用板块估算，发布后须切官方净值。养基宝周末界面用**结算后金额** × 涨幅会低估当日收益（如 -166.40），支付宝/正确口径为**结算前金额** × 涨幅（如 -169.04）。

**流程：**

| 时段 | 当日收益源 | 板块列 | 实现 |
|------|-----------|--------|------|
| 09:30–15:00（盘中） | 板块实时估算 | 东财实时 | `sector_return_percent_source = "realtime"` |
| 15:00 后、NAV 前 | 板块收盘估算 | 东财收盘 | `"closing_estimate"` |
| NAV 发布后 | **官方净值** | 仍东财板块 | `daily_return_percent_source = "official_nav"` |

**当日收益公式：**

| 场景 | 公式 |
|------|------|
| 官方净值已公布 | `daily_profit = holding_amount × daily_return% / (100 + daily_return%)` |
| 盘中板块估算 | `daily_profit ≈ holding_amount × sector_return% / 100` |

**昨日收益：** 再上一交易日官方净值涨跌（`compute_yesterday_profit_from_official_nav`），账户汇总「估算当日」列下展示「昨 ±xx」；OCR 详情页 `yesterday_profit` 作兜底。

**持有收益展示：** 盘中 `≈ 昨日结算持有收益 + 当日板块估算`；官方净值公布后直接使用 OCR/档案 `holding_profit`（已含当日），不再叠加 `daily_profit`。

**关键实现：**

- **`fund_nav_service.py`：** `get_official_nav_return()` 取 AkShare 日增长率；`compute_yesterday_profit_from_official_nav()` 算上一交易日收益。
- **`sector_quote_service.refresh_holdings_sector_quotes()`：** 官方 NAV 写入 `daily_return_percent` / `daily_profit` / `daily_return_percent_source`；**不**覆盖 `sector_return_percent`。
- **`holding_estimates.py`：** `overlay_official_nav_returns`（恢复持仓时补官方净值）、`compute_official_daily_profit`、`enrich_holdings_yesterday_profits`。
- **`holdingMetrics.ts`：** `applySectorDailyEstimate` 保留 `official_nav`；`computeDailyProfit` / `computeHoldingProfit` 与后端一致。
- **`YangjibaoHoldingsBoard`：** 估算当日 + 昨日收益子行；关联板块列独立展示东财涨跌。

**缓存策略：** `_NAV_CACHE[f"{fund_code}:{trade_date}"]` TTL 24h（命中）/ 5min（未发布重试）。

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

- **今日 / 生成日报 Tab：** 持仓看板 vs 工作流+风控+校对+`ReportPanel`。
- **用户菜单：** 基金档案、仪表盘、历史日报（含 `HistoryRail`）；`FundProfilePanel` 详情建档。
- **分析：** `ReportPanel` + `JobStatusFloat` 异步轮询。
- **偏好：** `lib/storage.ts`（profile、analysisMode、sectorAutoRefresh）。

---

## 环境变量

### 板块实时

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_SECTOR_QUOTES_ENABLED` | true | 关闭则不走 live 板块 |
| `FUND_AI_SECTOR_QUOTES_TTL_SECONDS` | 60 | spot 缓存 TTL |
| `FUND_AI_SECTOR_QUOTES_AUTO_INTERVAL_SECONDS` | 300 | 前端自动刷新间隔 |
| `FUND_AI_SECTOR_QUOTES_DISCREPANCY_WARN` | 0.5 | OCR vs 实时板块相差阈值（百分点） |
| `FUND_AI_SECTOR_QUOTES_RELAY_URL` | — | 可选真实板块行情中继地址；PC 直连东财失败时在 VPS/NAS 部署 `apps/sector-relay`（`docker compose up -d`），将 URL 填入此项 |
| `FUND_AI_SECTOR_QUOTES_RELAY_TIMEOUT_SECONDS` | 2.5 | 中继请求超时 |
| `FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED` | false | 是否启用浏览器命令链路 |
| `FUND_AI_SECTOR_QUOTES_BROWSER_COMMAND` | — | 浏览器命令，例如 `node scripts/sector-quote-browser-command.mjs` |
| `FUND_AI_SECTOR_QUOTES_BROWSER_TIMEOUT_SECONDS` | 4 | 板块 spot 浏览器命令超时 |
| `FUND_AI_SECTOR_QUOTES_RELAY_TOKEN` | — | 中继可选鉴权 Bearer |
| `FUND_AI_SECTOR_INTRADAY_BROWSER_COMMAND` | — | 分时浏览器兜底，如 `node scripts/sector-intraday-browser-command.mjs` |

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
| `FUND_AI_OCR_PRELOAD` | false | 启动时预热 PaddleOCR |
| `FUND_AI_OCR_USE_MOBILE_MODELS` | false | 使用 mobile 模型（更快，适合列表截图） |
| `FUND_AI_OCR_MAX_IMAGE_SIDE` | — | OCR 前缩放最长边（像素） |

修改 `.env` 后需重启 API。

---

## 本地开发

```bash
cd /d/Code/HL_Project/fundpilot-ai
bash scripts/dev.sh    # 或 scripts/dev.ps1
```

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q   # 当前 221 项
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
6. 改板块/净值收益：`sector_canonical.py` → `sector_quote_service.py`（板块 + 官方 NAV 写入 daily）→ `fund_nav_service.py` → `holding_estimates.py` / `holdingMetrics.ts` → `YangjibaoHoldingsBoard.tsx` → 相关 tests。
7. 改分时：`eastmoney_trends_client.py` → `sector_intraday_provider.py` → `IntradayPercentChart.tsx`；换机排查见 design 分时文档。

---

## 文档索引

| 文件 | 内容 |
|------|------|
| `README.md` | 安装、启动、环境变量、用户流程 |
| `docs/PROJECT_CONTEXT.md` | **本文** — 架构、API、数据流、环境变量（维护主入口） |
| `docs/SECURITY.md` | API Key 与 Secret Scanning |
| `docs/design/2026-06-04-eastmoney-intraday-troubleshooting.md` | 分时 push2 换机自测、指数映射、脏缓存清理（仅运维时查阅） |
| `.env.example` | 环境变量模板 |

### 文档维护约定

- **改功能先改 `PROJECT_CONTEXT.md`**：能力清单、API、环境变量、目录结构须与代码同步。
- **`docs/design/`** 仅保留运维 runbook（当前仅分时 push2 排查）；产品决策与实现细节以本文为准。
- **不保留** 已完成的一次性实现计划、清理报告、迭代日志。
