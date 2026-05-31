# FundPilot AI — 项目上下文（给 AI / 新开发者）

> **用途：** 在新对话或新开发者接手时，先读本文，再按需深入具体文件。避免从零扫描仓库才能理解项目在做什么。
>
> **维护：** 功能或架构有实质变化时，请同步更新「API」「数据流」「目录」「环境变量」几节。

---

## 一句话

**FundPilot AI** 是面向个人自用的本地基金投研助手：养基宝截图 OCR → 校对持仓 → 稳健型风控 → **东方财富新闻**（AkShare）+ **DeepSeek V4**（可 Tool 补拉新闻）生成带**逐基金操作建议**的日报；无 API Key 或调用失败时走本地规则报告。数据默认留在本机。

---

## 产品边界（必须遵守）

| 会做 | 不会做 |
|------|--------|
| 截图/文本 OCR、持仓校对、风控提示、AI 日报、逐基金建议金额示意 | 自动下单、券商对接、多用户 SaaS |
| 本地 SQLite、本地上传目录 | 默认上传原始截图到云端 |
| 拉取公开新闻标题/摘要供模型参考 | 投资建议（报告内须有 caveats） |

**隐私：** DeepSeek 收到用户确认后的**结构化持仓、风控参数、净值快照、新闻标题/摘要**（经 `NewsService` 从东方财富/AkShare 拉取），不传原始截图。详见 `README.md`「隐私和边界」。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js（App Router）、React、TypeScript、Tailwind CSS、Lucide |
| 后端 | FastAPI、Pydantic v2、pydantic-settings |
| 存储 | SQLite（`data/app.db`），截图在 `uploads/` |
| AI | DeepSeek OpenAI 兼容 API；`fetch_market_news` Function Calling |
| OCR（可选） | PaddleOCR（`requirements-ocr.txt`） |
| 行情/新闻 | AkShare：`FundDataService`（净值）、`NewsService`（`stock_news_em`、基金公告） |

环境变量前缀：`FUND_AI_*`（后端）、`NEXT_PUBLIC_API_BASE_URL`（前端）。模板见 `.env.example`。

---

## 仓库结构

```text
fundpilot-ai/
├── apps/api/
│   ├── app/
│   │   ├── main.py
│   │   ├── models.py              # Holding、Report、FundRecommendation、NewsItem…
│   │   ├── config.py              # Settings（含 news_*、deepseek_* token 上限）
│   │   ├── database.py            # reports / fund_profiles / ocr_text_cache
│   │   └── services/
│   │       ├── ocr_engine.py / ocr_parser.py
│   │       ├── fund_profile.py
│   │       ├── risk.py
│   │       ├── fund_data.py       # AkShare 净值
│   │       ├── news_service.py    # 主题提取 + 东方财富新闻 + 去重排序
│   │       ├── recommendations.py # 离线建议、JSON 解析、利好利空归类、金额示意
│   │       ├── deepseek_client.py # Tool 循环、JSON 解析/修复、报告组装
│   │       └── market_context.py  # 遗留占位服务，分析流程已不再调用
│   └── tests/
│       ├── test_api.py
│       ├── test_news_service.py
│       ├── test_recommendations.py
│       ├── test_deepseek_tools.py
│       └── …
├── apps/web/src/
│   ├── lib/api.ts
│   └── components/
│       ├── Dashboard.tsx      # Tab：capture | profiles | holdings | analysis | history
│       ├── ReportPanel.tsx    # 组合摘要 + fund_recommendations 卡片 + 新闻区
│       ├── HistoryRail.tsx    # 历史列表 + 删除
│       └── …
├── docs/PROJECT_CONTEXT.md    # 本文
└── README.md
```

---

## 核心业务流

### 1. 首次：建立基金档案

```text
养基宝「单基金详情」截图
  → POST /api/fund-profiles/ocr
  → parse_profile_from_text → FundProfile
  → SQLite fund_profiles（fund_code 主键）
```

### 2. 日常：总览 → 分析日报

```text
POST /api/ocr → holdings（000000 占位码由档案补全）
  → 前端 HoldingTable 校对 + RiskControls
  → POST /api/analyze
       ├ evaluate_portfolio_risk
       ├ FundDataService.get_snapshots
       └ DeepSeekClient.generate_report
            ├ NewsService.prefetch_for_holdings（离线/预取）
            ├（有 Key）_generate_with_tools：
            │    · 可选 fetch_market_news Tool（最多 news_tool_max_rounds 轮）
            │    · 最终轮 json_object 输出 title/summary/fund_recommendations/caveats
            │    · JSON 不完整时自动重试一轮
            ├ recommendations.enrich_fund_recommendations（利好利空、金额）
            └ save_report
  → ReportPanel 展示；HistoryRail 可回看/删除
```

**交易日场景：** Prompt 强调用户多在 14:30 左右分析、15:00 前决策，新闻检索优先当日（`NewsItem.is_today`）。

### 3. 占位码 `000000`

总览 OCR 常无 6 位代码；`FundProfileService.resolve_holding` 按基金名匹配本地档案后补全 `fund_code` / `fund_name`。

---

## HTTP API

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/api/ocr` | `file` / `raw_text` → holdings |
| POST | `/api/analyze` | `AnalysisRequest` → `Report` 并持久化 |
| GET | `/api/reports` | 最近 50 条 |
| GET | `/api/reports/{id}` | 单条详情 |
| DELETE | `/api/reports/{id}` | 删除历史日报 |
| POST | `/api/fund-profiles/ocr` | 详情截图 → 保存档案 |
| GET | `/api/fund-profiles` | 列出档案 |

前端封装：`apps/web/src/lib/api.ts`（含 `deleteReport`）。

---

## 领域模型（`models.py` 摘要）

| 模型 | 要点 |
|------|------|
| **Holding** | 6 位代码、金额、持有/当日收益、板块名与板块涨跌 |
| **InvestorProfile** | 默认稳健；浮亏线 8%、集中度 35%、定投偏好、拒绝追高 |
| **RiskAssessment** | `level`、`suggested_action`、`alerts[]` |
| **NewsItem** | `topic`、`title`、`published_at`、`snippet`、`is_today` |
| **FundRecommendation** | 逐基金 `action`、`amount_yuan`/`amount_note`、`news_bullish`/`news_bearish`、`points[]` |
| **Report** | 含 `fund_recommendations`、`market_news`；`market_context` 现恒为空数组（字段保留兼容） |
| **FundProfile** | 长期档案，用于 OCR 补码 |

风控：`risk.py` 加权浮亏触及 `max_drawdown_percent` → high；单只超 `concentration_limit_percent` → 集中度告警。

---

## 新闻与 DeepSeek Tool

**NewsService**（`news_service.py`）：

- 从持仓 `sector_name`、基金名关键词提取主题（上限 `news_max_topics`）。
- `search(topic)`：`stock_news_em`；主题为 6 位代码时额外尝试基金公告。
- `prefetch_for_holdings`：离线模式与在线首轮预取。
- 去重、按时间排序，snippet 截断。

**DeepSeek Tool `fetch_market_news`**（`deepseek_client.py`）：

- `FUND_AI_NEWS_ENABLED=false` 时不注册 Tool，仅预取新闻。
- Tool 轮次由 `news_tool_max_rounds` 控制；最后一轮用 `json_object` 收结构化报告。
- 解析失败/截断：`_parse_model_json` 多策略修复 + 不完整时重试；仍失败则 `_offline_report` + `recommendations` 补齐。

**recommendations.py**：

- 解析模型返回的 `fund_recommendations` 或 legacy 字符串列表。
- `suggest_trade_amount`：按集中度上限给加仓/减仓示意金额（非实盘指令）。
- `classify_sector_news`：关键词粗分利好/利空标题。

---

## 前端 UI

| Tab | 组件 | 行为 |
|-----|------|------|
| capture | `UploadDropzone` | 总览图/粘贴 → `parseOcr` |
| profiles | `FundProfilePanel` | 档案 OCR |
| holdings | `HoldingTable` + `RiskControls` | 校对与风控参数 |
| analysis | `ReportPanel` | 组合风险、逐基金卡片（操作/金额/利好利空/要点）、新闻列表 |
| history | `HistoryRail` | 选择历史报告；`deleteReport` 删除 |

`ReportPanel` 会过滤内部 caveat（如 JSON 截断提示），用户只看友好文案。

---

## 环境变量（常用）

| 变量 | 含义 |
|------|------|
| `FUND_AI_DEEPSEEK_API_KEY` | 无则全程离线报告 |
| `FUND_AI_DEEPSEEK_MODEL` | 默认 `deepseek-v4-pro`，可改 `deepseek-v4-flash` |
| `FUND_AI_DEEPSEEK_TIMEOUT_SECONDS` | 默认 300 |
| `FUND_AI_DEEPSEEK_MAX_TOKENS` | Tool 轮次 max_tokens（默认 384000） |
| `FUND_AI_DEEPSEEK_MAX_TOKENS_REPORT` | 最终 JSON 报告轮 |
| `FUND_AI_NEWS_ENABLED` | 是否启用新闻 Tool/预取 |
| `FUND_AI_NEWS_MAX_TOPICS` / `NEWS_PER_TOPIC` / `NEWS_TOOL_MAX_ROUNDS` | 新闻规模与 Tool 轮数 |

---

## 扩展与替换点

| 能力 | 入口 | 注意 |
|------|------|------|
| OCR | `ocr_parser.py` | 保持输出 `list[Holding]` |
| 新闻源 | `NewsService._from_eastmoney` | 保持 `NewsItem` 结构 |
| 报告 Prompt/Tool | `deepseek_client.py` | 改 JSON schema 时同步 `recommendations.parse_*` |
| 离线逻辑 | `_offline_report` + `build_offline_fund_recommendation` | 与在线 `FundRecommendation` 字段一致 |
| 持久化 | `database.py` | 报告整包 JSON 存 `reports.payload` |

---

## 本地开发与验证

```bash
# 后端
cd /d/Code/HL_Project/fundpilot-ai/apps/api
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 前端
cd /d/Code/HL_Project/fundpilot-ai/apps/web
npm run dev
```

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -v
cd apps/web && npm run lint && npm run typecheck && npm run build
```

---

## 给 AI 的使用建议

1. **先读本文**，再打开 `main.py`、`models.py`、`deepseek_client.py`、`ReportPanel.tsx`。
2. **改 API** 时同步：`models.py` → `main.py` → `api.ts` → 组件 → `tests/`。
3. 新报告字段需同时考虑：DeepSeek JSON schema、`_offline_report`、`enrich_fund_recommendations`、前端 `Report` 类型。
4. 历史计划见 `docs/superpowers/plans/2026-05-29-fund-ai-mvp.md`（以代码为准）。

---

## 文档索引

| 文件 | 内容 |
|------|------|
| `README.md` | 安装、启动、环境变量、推荐流程 |
| `docs/PROJECT_CONTEXT.md` | 本文 |
| `.env.example` | 环境变量模板 |
