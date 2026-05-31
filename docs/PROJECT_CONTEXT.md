# FundPilot AI — 项目上下文（给 AI / 新开发者）

> **用途：** 新对话或接手开发时先读本文，再按需打开具体文件。避免从零扫描仓库。
>
> **维护：** 功能或架构有实质变化时，同步更新「能力清单」「数据流」「API」「目录」「环境变量」。

**文档版本：** 2026-05-31（异步分析 + 报告追问对话）

---

## 一句话

**FundPilot AI** 是面向个人自用的本地基金投研助手：养基宝总览/详情截图 → OCR → 校对持仓 → 稳健风控 → 东方财富新闻（AkShare）+ DeepSeek V4 生成**逐基金操作建议**日报；点击"生成报告"后台异步执行，右下角悬浮面板查看进度。数据默认留在本机。

---

## 能力清单（当前已实现）

| 类别 | 能力 |
|------|------|
| 输入 | 页面上传/粘贴 OCR；可选 PaddleOCR；基金档案补全 `000000` 占位码 |
| 风控 | 浮亏线、单只集中度、定投偏好、拒绝追高（`InvestorProfile`） |
| 报告 | 组合摘要 + `fund_recommendations` + 新闻列表；离线规则兜底 |
| 分析模式 | **快速**（Flash + 仅预取新闻）/ **深度**（Pro + 可选新闻 Tool） |
| 体验 | 今日一键、报告 vs 昨日 diff、导出 Markdown、档案 JSON 导入导出 |
| 报告追问 | 决策建议右侧 SSE 流式对话；快速 Flash / 深度 Pro+新闻 Tool；导出对话 Markdown |
| 异步分析 | `/api/analyze/async` 后台任务，右下角 `JobStatusFloat` 悬浮面板查看进度 |
| 前端偏好 | localStorage：风控参数、分析模式、追问模式（`fundpilot-report-chat-mode`） |

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
| 后端 | FastAPI、Pydantic v2、uvicorn；`lifespan` 启后台线程 |
| 存储 | SQLite：`reports`、`fund_profiles`、`ocr_text_cache`、`analysis_jobs`、`report_chat_messages` |
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
│   ├── lifespan.py          # 应用启动（仅 yield，无后台线程）
│   ├── config.py / models.py / database.py
│   └── services/
│       ├── ocr_engine.py / ocr_parser.py
│       ├── fund_profile.py / risk.py / fund_data.py
│       ├── news_service.py / recommendations.py
│       ├── deepseek_client.py
│       ├── analysis_runtime.py    # fast/deep 运行时参数
│       ├── analyze_pipeline.py    # 同步分析入口
│       ├── job_store.py           # 异步分析任务
│       ├── report_diff.py / report_export.py
│       ├── report_chat.py         # 追问 SSE + Tool 轮次
│       ├── report_chat_runtime.py # 追问 fast/deep
│       ├── report_chat_export.py  # 对话 Markdown
│       └── market_context.py      # 遗留，主流程未用
├── apps/web/src/
│   ├── lib/api.ts / storage.ts / notifications.ts
│   └── components/
│       ├── Dashboard.tsx
│       ├── JobStatusFloat.tsx     # 右下角悬浮任务面板
│       ├── AnalysisModeToggle.tsx / ReportDiffPanel.tsx
│       ├── UploadDropzone / HoldingTable / RiskControls
│       ├── ReportPanel / ReportChatPanel / HistoryRail / FundProfilePanel
├── uploads/
├── data/app.db
├── scripts/dev.sh / dev.ps1
├── docs/PROJECT_CONTEXT.md   # 本文
└── README.md
```

---

## 推荐使用流程

```text
1. bash scripts/dev.sh → 打开 http://127.0.0.1:3000
2. capture 页上传或粘贴 → 自动 OCR
3. 校对 HoldingTable → 选快速/深度 → 点击"生成报告"
4. 右下角悬浮面板显示进度 → 完成后点击"查看报告"
5. analysis 页查看 diff、导出日报 Markdown
6. 决策建议右侧追问（SSE）；可导出对话 Markdown
```

### 首次：基金档案

```text
profiles 页 → 单基金详情截图 → POST /api/fund-profiles/ocr → SQLite fund_profiles
```

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
  → GET /api/jobs/{id} 轮询（JobStatusFloat 内部，1.5s 间隔）
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
| GET | `/api/jobs/{id}` | 任务状态；完成时含 `report` |
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

前端封装：`apps/web/src/lib/api.ts`。

---

## 领域模型（摘要）

| 模型 | 要点 |
|------|------|
| **Holding** | 6 位代码、金额、持有/当日收益、板块 |
| **InvestorProfile** | 稳健默认；浮亏 8%、集中度 35% |
| **FundRecommendation** | action、amount_*、news_bullish/bearish、points |
| **NewsItem** | topic、title、is_today |
| **Report** | 含 fund_recommendations、market_news；market_context 恒 `[]` |
| **AnalysisRequest** | holdings、profile、ocr_text、**analysis_mode** |
| **ChatMessage** | report_id、role、content |
| **ReportChatRequest** | message、**chat_mode**（fast \| deep） |

占位码 `000000`：总览 OCR 无代码时，靠 `FundProfileService` 按名称匹配档案补全。

---

## 新闻与 DeepSeek

- **预取：** `NewsService.prefetch_for_holdings`（两种模式都做）。
- **Tool：** 仅深度模式且 `news_tool_max_rounds > 0` 时注册 `fetch_market_news`。
- **兜底：** JSON 解析失败 → `_offline_report` + `recommendations.enrich_*`。

---

## 前端要点

- **capture：** `UploadDropzone`、校对表、`RiskControls`（含"生成报告"按钮）。
- **analysis：** `ReportPanel`（含 `ReportDiffPanel`、`ReportChatPanel` 追问、导出 Markdown）。
- **悬浮面板：** `JobStatusFloat`，固定右下角，内部轮询 job 状态，完成后回调 Dashboard。
- **偏好：** `lib/storage.ts`（profile、analysisMode、reportChatMode）。
- **通知：** `lib/notifications.ts`；分析完成时触发桌面通知。

---

## 环境变量

### DeepSeek / 新闻

| 变量 | 默认 | 含义 |
|------|------|------|
| `FUND_AI_DEEPSEEK_API_KEY` | — | 无则离线报告 |
| `FUND_AI_DEEPSEEK_MODEL` | deepseek-v4-pro | 深度模式模型 |
| `FUND_AI_DEEPSEEK_TIMEOUT_SECONDS` | 300 | 读超时 |
| `FUND_AI_NEWS_ENABLED` | true | 关闭则不注册 Tool |
| `FUND_AI_NEWS_TOOL_MAX_ROUNDS` | 3 | Tool 轮数上限 |

修改 `.env` 后需重启 API。

---

## 本地开发

```bash
cd /d/Code/HL_Project/fundpilot-ai
bash scripts/dev.sh    # 或 scripts/dev.ps1
```

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -v   # 当前约 41 项
cd apps/web && npm run lint && npm run typecheck && npm run build
```

---

## 给 AI 的修改建议

1. 改 API：`models.py` → `main.py` → `api.ts` → 组件 → `tests/`。
2. 改报告结构：同步 `deepseek_client` JSON、`recommendations`、`_offline_report`、`Report` 类型。
3. 改异步流程：`job_store.py`（后端）→ `JobStatusFloat.tsx`（前端轮询）→ `Dashboard.tsx`（回调）。
4. 改追问：`report_chat.py` / `report_chat_runtime.py` → `main.py` chat 路由 → `ReportChatPanel.tsx` → `tests/test_report_chat*.py`。
5. 历史 MVP 计划：`docs/superpowers/plans/2026-05-31-remove-automation-async-float.md`（以代码为准）。

---

## 文档索引

| 文件 | 内容 |
|------|------|
| `README.md` | 安装、启动、环境变量、流程 |
| `docs/PROJECT_CONTEXT.md` | 本文 |
| `.env.example` | 环境变量模板 |
