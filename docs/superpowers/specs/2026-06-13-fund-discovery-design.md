# 推荐基金（Fund Discovery）— 设计规格

> **版本：** 2026-06-13  
> **状态：** 已实现（MVP）  
> **范围：** 新增「推荐基金」Tab；窄池候选（A）+ 可选关注方向（C）；一键生成 + 报告追问；与「生成日报」职责分离

---

## 1. 背景与目标

FundPilot AI 日报已明确 **仅分析已有持仓、不荐新基**（`analysis_prompt.DEFAULT_ROLE_PROMPT`）。用户需要独立能力：根据大盘/板块/新闻与组合缺口，由大模型从**受控候选池**中精选 3~5 只值得关注的场外基金，并给出持有期、理由与示意金额。

### 1.1 产品目标

| 目标 | 说明 |
|------|------|
| 一键扫描 | 类似「生成日报」，点击按钮异步产出可存档的推荐报告 |
| 方向可选 | 用户可指定 1~3 个关注板块（C）；未指定时由系统自动推断热点 + 组合缺口 |
| 窄池可控 | 候选基金仅来自规则筛出的 15~25 只（A），模型不得编造池外代码 |
| 追问细化 | 报告生成后可对话调整（「只要联接基金」「预算降到 3000」） |
| 风控一致 | 复用 `InvestorProfile`（期望投入、集中度、偏定投、拒绝追高） |

### 1.2 非目标（MVP）

- 全市场 Tool 自由检索（B 模式，二期再议）
- 自动下单、券商对接
- 小程序端推荐 Tab（Web 先行）
- 估值分位、复杂量化打分模型
- 与日报合并为同一 Prompt / 同一报告类型

---

## 2. 模式决策（已确认）

用户确认：**A 为主 + C 为可选参数**。

| 模式 | MVP 行为 |
|------|----------|
| **A 窄池** | 按目标板块从 AkShare 排行 + `fund_primary_sectors` 映射筛候选，每板块 Top 3~5，总量上限 25 |
| **C 可选方向** | 请求体 `focus_sectors?: string[]`（canonical 板块名）；与自动推断板块取并集，用户指定优先 |
| **混合 UX** | Tab 内「一键生成」+ 报告下方 `DiscoveryChatPanel`（复用日报追问 SSE 模式） |

---

## 3. 与现有模块的关系

```text
┌─────────────────┐     ┌──────────────────────┐
│  生成日报 Tab    │     │  推荐基金 Tab（新）    │
│  holdings 输入   │     │  持仓只读 + 市场面输入 │
│  DEFAULT_ROLE    │     │  DISCOVERY_ROLE      │
│  analysis_facts  │     │  discovery_facts     │
│  fund_recs 持仓  │     │  discovery_recs 新基 │
└─────────────────┘     └──────────────────────┘
         │                         │
         └──────────┬──────────────┘
                    ▼
         共享：profile、news、sector_canonical、
               job 异步模式、DeepSeek 客户端、守卫哲学
```

**明确不复用：** 日报 `Report` 表与 `fund_recommendations` 字段；新建 `FundDiscoveryReport` 避免 schema 污染。

---

## 4. 用户流程

```text
1. 用户打开「推荐基金」Tab
2. （可选）多选「关注方向」：商业航天 / 半导体 / …（canonical 列表）
3. （可选）填写「本次可投入预算」覆盖默认（期望投入 − 当前持仓总额）
4. 选择 快速 / 深度，点击「扫描今日机会」
5. JobStatusFloat 展示阶段：板块热度 → 候选池 → 生成中 → 保存
6. 展示推荐报告卡片（3~5 只）+ 风险提示
7. 可在 DiscoveryChatPanel 追问；深度模式可拉新闻 Tool
```

**无持仓场景：** 允许生成（候选池仍可用）；组合缺口逻辑退化为「全板块按热度」；UI 提示「尚未录入持仓，建议仅从方向与风控出发参考」。

---

## 5. 数据与决策维度

### 5.1 喂给模型的结构化事实（`discovery_facts`）

| 块 | 来源（现有服务） | 用途 |
|----|------------------|------|
| `session` | `trading_session` | 交易日语义 |
| `portfolio_gap` | 持仓板块权重 vs canonical 热度 | 缺口/潜伏板块 |
| `sector_heat` | `sector_canonical` + 东财日 K | 当日 + 近 5 日涨跌排序 |
| `market_flow` | `market_flow_client` | 北向资金 |
| `signal_backtest` | `sector_signal_context` | 目标板块短线规则命中率 |
| `news` | `news_service` + `news_summarizer` | 主题摘要与新鲜度 |
| `candidate_pool` | 本节 5.2 | **唯一允许推荐的基金代码集合** |
| `profile` | `InvestorProfile` | 风控与预算 |

### 5.2 候选池构建（A 窄池）

**步骤：**

1. **`select_target_sectors(holdings, focus_sectors?)`**
   - 计算全部 `list_canonical_sector_labels()` 的 `sector_heat`（当日涨跌幅 `change_1d`、近 5 日 `change_5d`）
   - 自动板块：热度 Top 3 中，用户持仓权重 &lt; 15% 或未持有的板块
   - 合并 `focus_sectors`（用户指定，最多 3 个，须能 `get_canonical_sector` 解析）
   - 去重后最多 **5 个目标板块**

2. **`build_candidates_for_sector(sector_label, exclude_codes)`**
   - 来源 a：`fund_primary_sectors` + `GLOBAL_FUND_SECTOR_SEEDS` 中 `sector_name` 匹配的 code
   - 来源 b：AkShare `fund_open_fund_rank_em`（子进程，与现有 `akshare_subprocess` 一致）按近 1 年收益排序，基金名称含板块关键词（`sector_labels` / canonical `source_name`）
   - 过滤：排除 `exclude_codes`（已持有）、规模 &lt; 1 亿（有数据时）、暂停申购（有数据时）
   - 每板块取 **Top 5**，按 `return_1y_percent` 降序

3. **`enrich_candidates(codes)`**
   - 对每只拉 `FundDataService` 快照：近 1 年收益、最大回撤、管理费、规模、`nav_trend` 摘要
   - 写入 `candidate_pool[]`，附带 `sector_label`、`selection_reason`（种子/排行/映射）

4. **硬约束**
   - 候选总数 **15~25**；不足 3 只时仍可调模型，但 `caveats` 须声明「候选不足」
   - 模型输出 `fund_code` **必须** ∈ `candidate_pool`；否则 `discovery_guard` 剔除并记入 caveat

### 5.3 推荐输出字段（`DiscoveryRecommendation`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `fund_code` | str | 6 位，来自候选池 |
| `fund_name` | str | 与候选池一致 |
| `sector_name` | str | 主关联板块 |
| `action` | str | `建议关注` \| `分批买入` \| `等待回调`（MVP 三档） |
| `suggested_amount_yuan` | float? | 示意金额，受 profile 约束 |
| `amount_note` | str? | 如「约占可投入预算 20%」 |
| `hold_horizon` | str | 如 `2-4周` / `1-3个月` / `3-6个月` |
| `confidence` | str | `高` \| `中` \| `低` |
| `points` | list[str] | 理由要点 |
| `risks` | list[str] | 风险点 |
| `news_bullish` | list[str] | 引用标题，须过 `news_citation` 守卫 |

---

## 6. API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/fund-discovery/sectors` | 返回可选 `focus_sectors`（canonical 标签 + 当日涨跌） |
| POST | `/api/fund-discovery/async` | 创建异步任务；body 见下 |
| GET | `/api/fund-discovery/jobs/{id}` | 任务状态（可复用 `analysis_jobs` 表 + `job_type` 字段，或独立 `discovery_jobs`） |
| GET | `/api/fund-discovery/reports` | 最近 30 条推荐报告 |
| GET | `/api/fund-discovery/reports/{id}` | 报告详情 |
| DELETE | `/api/fund-discovery/reports/{id}` | 删除 |
| GET | `/api/fund-discovery/reports/{id}/chat` | 追问历史 |
| POST | `/api/fund-discovery/reports/{id}/chat` | SSE 追问；body `{ message, chat_mode }` |
| GET | `/api/fund-discovery/reports/{id}/markdown` | 导出 Markdown |

### 6.1 `DiscoveryRequest`（POST body）

```python
class DiscoveryRequest(BaseModel):
    profile: InvestorProfile
    analysis_mode: AnalysisMode = "deep"
    focus_sectors: list[str] = Field(default_factory=list, max_length=3)
    budget_yuan: float | None = None  # 覆盖「可投入余额」
    holdings: list[Holding] = Field(default_factory=list)  # 前端传 displayableHoldings；空则服务端拉 portfolio
```

### 6.2 异步任务阶段（`DISCOVERY_JOB_STAGES`）

| stage | label |
|-------|-------|
| `queued` | 排队中… |
| `sector_heat` | 计算板块热度… |
| `candidate_pool` | 构建候选基金池… |
| `news` | 拉取市场要闻… |
| `generating` | AI 分析中… |
| `guarding` | 校验推荐结果… |
| `saving` | 保存报告… |
| `completed` | 完成 |

---

## 7. AI Prompt 分层

新文件 `discovery_prompt.py`：

| 层级 | 内容 |
|------|------|
| `DEFAULT_DISCOVERY_ROLE_PROMPT` | 投顾人设；**仅**从 `candidate_pool` 选 3~5 只；须输出 `hold_horizon`、`risks`；禁止编造池外代码；结合 `portfolio_gap` 解释「为何现在看这只」 |
| System 后缀 | 时间戳、新闻规则、JSON 输出 schema、`OUTPUT_DISCOVERY_REQUIREMENTS` |
| User JSON | `discovery_payload.build_user_payload()` — 瘦身版，不含完整持仓 OCR |

**与日报分工：** 日报 `role_prompt` 用户可编辑；推荐基金 MVP 使用固定 `DEFAULT_DISCOVERY_ROLE_PROMPT`（二期可加独立编辑）。

### 7.1 JSON 输出 schema（模型）

```json
{
  "title": "string",
  "summary": "string",
  "market_view": "string",
  "recommendations": [
    {
      "fund_code": "519674",
      "fund_name": "...",
      "sector_name": "半导体",
      "action": "分批买入",
      "suggested_amount_yuan": 3000,
      "amount_note": "...",
      "hold_horizon": "1-3个月",
      "confidence": "中",
      "points": ["..."],
      "risks": ["..."],
      "news_bullish": ["标题"]
    }
  ],
  "caveats": ["..."]
}
```

---

## 8. 守卫（`discovery_guard.py`）

| 规则 | 行为 |
|------|------|
| 代码白名单 | 非 `candidate_pool` 的推荐剔除 |
| 已持有 | 已持有 code 降级为「已持仓，不建议重复加仓」或剔除 |
| `avoid_chasing` | 板块当日涨幅 ≥ 4% 时，`分批买入` → `等待回调` |
| 预算 | 单只 `suggested_amount_yuan` ≤ `budget_yuan * concentration_limit` |
| 新闻 | 复用 `news_citation.apply_news_citation_guards` |
| 总数 | 保留 3~5 条；不足 3 条时 caveat 说明 |

离线兜底：`discovery_offline.py` 按板块热度 + 候选池排行规则生成 3 条「建议关注」，无 API Key 时可演示。

---

## 9. 存储

### 9.1 Schema v5 迁移

新增表（SQLite + `mysql_bootstrap.py` 同步）：

**`fund_discovery_reports`**

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | TEXT PK | uuid hex |
| `userId` | INTEGER | 多租户 |
| `title` | TEXT | |
| `payload` | TEXT/JSON | 完整 `FundDiscoveryReport` |
| `created_at` | TEXT | ISO UTC |

**`discovery_chat_messages`** — 结构同 `report_chat_messages`，`report_id` → `discovery_report_id`

**`discovery_jobs`**（或扩展 `analysis_jobs.job_type`）

- 推荐独立 `discovery_jobs`，避免污染日报 job 查询；字段 mirror `analysis_jobs` + `discovery_report_id`

### 9.2 Pydantic 模型（`models.py`）

- `DiscoveryRequest`
- `DiscoveryRecommendation`
- `FundDiscoveryReport`（含 `discovery_facts`、`candidate_pool`、`recommendations`、`caveats`、`provider`）

---

## 10. 前端

### 10.1 Tab 布局

`Dashboard.tsx` 新增主 Tab：

```typescript
{ id: "discovery", label: "推荐基金" }
```

顺序建议：`持有 | 盈亏分析 | 推荐基金 | 生成日报`

### 10.2 组件

| 组件 | 职责 |
|------|------|
| `FundDiscoveryPanel` | 关注方向多选、预算、模式、生成按钮 |
| `DiscoverySectorPicker` | 拉 `/api/fund-discovery/sectors`，展示涨跌 chips |
| `DiscoveryReportPanel` | 报告展示，卡片布局 |
| `DiscoveryRecommendationCard` | 单只基金：代码、板块、动作、金额、持有期、理由/风险 |
| `DiscoveryChatPanel` | 复用 `ReportChatPanel` SSE 模式，换 API 路径 |
| `JobStatusFloat` | 扩展支持 `discovery` job 类型（或通用化 stage 轮询） |

### 10.3 状态与 API

- `api.ts`：新增 discovery 相关类型与 fetch 函数
- 生成时传 `displayableHoldings` + `profile` + `focus_sectors`
- 历史列表可收进用户菜单子入口（MVP 可仅 Tab 内「上次推荐」）

---

## 11. 后端文件规划

| 文件 | 职责 |
|------|------|
| `services/discovery_sector_heat.py` | canonical 板块热度排序 |
| `services/discovery_candidate_pool.py` | 窄池构建 + enrich |
| `services/discovery_facts.py` | 组装 `discovery_facts` |
| `services/discovery_payload.py` | 喂模型 user JSON 瘦身 |
| `services/discovery_prompt.py` | 角色 Prompt + 输出约束 |
| `services/discovery_client.py` | DeepSeek 调用、解析 JSON |
| `services/discovery_guard.py` | 守卫 |
| `services/discovery_offline.py` | 离线兜底 |
| `services/discovery_pipeline.py` | `run_discovery()` 主流程 |
| `services/discovery_job_store.py` | 异步任务（mirror `job_store`） |
| `services/discovery_chat.py` | SSE 追问（mirror `report_chat`） |
| `services/akshare_subprocess.py` | 新增 `fetch_open_fund_rank` |
| `main.py` | 路由注册 |
| `database.py` | CRUD |
| `db_migrations.py` | v4 → v5 |

---

## 12. 测试策略

| 测试文件 | 覆盖 |
|----------|------|
| `test_discovery_sector_heat.py` | 板块排序、focus 合并 |
| `test_discovery_candidate_pool.py` | 窄池过滤、排除已持有、上限 25 |
| `test_discovery_guard.py` | 白名单、追高、预算 |
| `test_discovery_payload.py` | user JSON 结构 |
| `test_discovery_pipeline.py` | 离线端到端 |
| `test_api.py` | 路由、鉴权、持久化、chat 404 |

不要求 MVP 对 AkShare 排行做 live 集成测试；注入 mock 排行数据。

---

## 13. 风险与合规

- 所有报告 `caveats` 须含固定免责声明：「仅供参考，不构成投资建议」
- 推荐基金 Tab 顶部常驻风险提示文案
- 不传用户 OCR 原图至模型；仅结构化候选与持仓摘要
- `suggested_amount_yuan` 仅为示意，文案避免「必须买入」

---

## 14. 迭代路线（二期）

- 用户可编辑「推荐基金角色 Prompt」
- 宽池模式（Tool + `search_funds`）
- 推荐准确率复盘（对接 `recommendation_accuracy` 框架）
- 小程序只读展示
- 估值分位、季报重仓变化信号

---

## 15. 验收标准（MVP）

1. 「推荐基金」Tab 可一键生成报告；无 API Key 时离线兜底可展示
2. 指定 `focus_sectors=["半导体"]` 时，候选池须含该板块基金且推荐偏向该方向
3. 模型返回池外 code 时被守卫剔除并记录 caveat
4. 已持有基金不会作为「新买入」推荐（剔除或明确标注）
5. 报告可 SSE 追问至少 3 轮；深度模式可触发新闻 Tool
6. 报告落库、可列表查看、可导出 Markdown
7. pytest 新增项全部通过；`npm run typecheck` 通过

---

## 16. 文档维护

实现完成后更新 `docs/PROJECT_CONTEXT.md`：能力清单、API 表、目录结构、推荐使用流程第 5 步后插入推荐基金步骤。
