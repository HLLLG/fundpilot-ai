# Fund Discovery（推荐基金）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「推荐基金」Tab：窄池候选（A）+ 可选关注方向（C），一键异步生成推荐报告 + SSE 追问。

**Architecture:** 独立 `FundDiscoveryReport` 与 `discovery_pipeline`（板块热度 → 候选池 → DeepSeek → `discovery_guard`），不复用日报 `Report` 表；前端新 Tab 复用 `JobStatusFloat` 轮询模式与 `ReportChatPanel` SSE 模式。

**Tech Stack:** FastAPI, Pydantic v2, AkShare 子进程, DeepSeek API, Next.js/React/TypeScript

**Spec:** `docs/superpowers/specs/2026-06-13-fund-discovery-design.md`

---

## File Map

| File | Responsibility |
|------|----------------|
| `app/models.py` | `DiscoveryRequest`, `DiscoveryRecommendation`, `FundDiscoveryReport` |
| `app/db_migrations.py` | Schema v4 → v5：`fund_discovery_reports`, `discovery_jobs`, `discovery_chat_messages` |
| `app/mysql_bootstrap.py` | MySQL 同步建表 |
| `app/database.py` | discovery report/job/chat CRUD |
| `app/services/discovery_sector_heat.py` | canonical 板块当日 + 近5日涨跌排序 |
| `app/services/discovery_target_sectors.py` | 自动缺口板块 ∪ `focus_sectors` |
| `app/services/discovery_candidate_pool.py` | 窄池构建、enrich、排除已持有 |
| `app/services/discovery_facts.py` | 组装 `discovery_facts` |
| `app/services/discovery_payload.py` | 喂模型 user JSON |
| `app/services/discovery_prompt.py` | `DEFAULT_DISCOVERY_ROLE_PROMPT` + 输出约束 |
| `app/services/discovery_client.py` | DeepSeek 生成 + JSON 解析 |
| `app/services/discovery_guard.py` | 白名单、追高、预算、新闻引用 |
| `app/services/discovery_offline.py` | 无 API Key 兜底 |
| `app/services/discovery_pipeline.py` | `run_discovery()` 主流程 |
| `app/services/discovery_job_store.py` | 异步任务（mirror `job_store`） |
| `app/services/discovery_chat.py` | SSE 追问 |
| `app/services/discovery_export.py` | Markdown 导出 |
| `app/services/akshare_subprocess.py` | `fetch_open_fund_rank()` |
| `app/main.py` | `/api/fund-discovery/*` 路由 |
| `tests/test_discovery_*.py` | 单元 + pipeline + API |
| `web/src/lib/api.ts` | 类型与 API 封装 |
| `web/src/components/FundDiscoveryPanel.tsx` | Tab 主面板 |
| `web/src/components/DiscoveryReportPanel.tsx` | 报告展示 |
| `web/src/components/DiscoveryChatPanel.tsx` | 追问（基于 ReportChatPanel） |
| `web/src/components/JobStatusFloat.tsx` | 支持 discovery job 轮询 |
| `web/src/components/Dashboard.tsx` | 新 Tab |
| `docs/PROJECT_CONTEXT.md` | 能力清单与 API 更新 |

---

## Task 1: Models + DB schema v5

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/api/app/db_migrations.py`
- Modify: `apps/api/app/mysql_bootstrap.py`
- Modify: `apps/api/app/database.py`
- Test: `apps/api/tests/test_discovery_database.py`

- [ ] **Step 1: Add Pydantic models**

```python
# models.py additions
class DiscoveryRecommendation(BaseModel):
    fund_code: str
    fund_name: str
    sector_name: str
    action: str
    suggested_amount_yuan: float | None = None
    amount_note: str | None = None
    hold_horizon: str = ""
    confidence: str = "中"
    points: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    news_bullish: list[str] = Field(default_factory=list)

class FundDiscoveryReport(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    title: str
    summary: str = ""
    market_view: str = ""
    focus_sectors: list[str] = Field(default_factory=list)
    target_sectors: list[str] = Field(default_factory=list)
    candidate_pool: list[dict] = Field(default_factory=list)
    recommendations: list[DiscoveryRecommendation] = Field(default_factory=list)
    discovery_facts: dict = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)
    provider: str = "offline"
    analysis_mode: AnalysisMode = "deep"

class DiscoveryRequest(BaseModel):
    profile: InvestorProfile
    analysis_mode: AnalysisMode = "deep"
    focus_sectors: list[str] = Field(default_factory=list)
    budget_yuan: float | None = None
    holdings: list[Holding] = Field(default_factory=list)
```

- [ ] **Step 2: Migration v5** — `SCHEMA_VERSION = 5`; create `fund_discovery_reports`, `discovery_jobs`, `discovery_chat_messages`

- [ ] **Step 3: database.py** — `save_discovery_report`, `list_discovery_reports`, `get_discovery_report`, `delete_discovery_report`, chat message helpers

- [ ] **Step 4: Test persistence**

```python
def test_save_and_list_discovery_report(tmp_path, monkeypatch, auth_client):
    # POST minimal report via save_discovery_report; GET list returns it
```

Run: `cd apps/api && pytest tests/test_discovery_database.py -q`

---

## Task 2: Sector heat + target sectors

**Files:**
- Create: `apps/api/app/services/discovery_sector_heat.py`
- Create: `apps/api/app/services/discovery_target_sectors.py`
- Test: `apps/api/tests/test_discovery_sector_heat.py`

- [ ] **Step 1: `build_sector_heat_ranking()`** — iterate `list_canonical_sector_labels()`, use `fetch_canonical_sector_quote` + 5d K线变化（`fetch_eastmoney_daily_kline_series` 最近 6 根算 5d）

- [ ] **Step 2: `select_target_sectors(holdings, focus_sectors, heat_ranking)`** — auto top 3 gap sectors (weight < 15% or not held) ∪ focus (max 3), dedupe max 5

- [ ] **Step 3: Tests with mocked K-line**

Run: `pytest tests/test_discovery_sector_heat.py -q`

---

## Task 3: Candidate pool + AkShare rank

**Files:**
- Modify: `apps/api/app/services/akshare_subprocess.py`
- Create: `apps/api/app/services/discovery_candidate_pool.py`
- Test: `apps/api/tests/test_discovery_candidate_pool.py`

- [ ] **Step 1: `fetch_open_fund_rank()`** — subprocess `ak.fund_open_fund_rank_em(symbol="全部")`, return list of `{code, name, return_1y, scale_yi, ...}`

- [ ] **Step 2: `build_candidate_pool(target_sectors, exclude_codes)`** — seeds from `GLOBAL_FUND_SECTOR_SEEDS` + `fund_primary_sectors`; rank filter by sector keywords; top 5/sector; cap 25

- [ ] **Step 3: `enrich_candidates(pool)`** — attach snapshot fields via existing `FundDataService._from_akshare_combined` pattern

- [ ] **Step 4: Tests** — mock rank data; verify exclude held codes `519674`

Run: `pytest tests/test_discovery_candidate_pool.py -q`

---

## Task 4: Discovery facts + payload + prompt

**Files:**
- Create: `apps/api/app/services/discovery_facts.py`
- Create: `apps/api/app/services/discovery_payload.py`
- Create: `apps/api/app/services/discovery_prompt.py`
- Test: `apps/api/tests/test_discovery_payload.py`

- [ ] **Step 1: `build_discovery_facts()`** — portfolio_gap, sector_heat, market_flow, signal_backtest, news pipeline, candidate_pool

- [ ] **Step 2: `build_user_payload()`** — slim JSON for LLM

- [ ] **Step 3: `DEFAULT_DISCOVERY_ROLE_PROMPT`** + `OUTPUT_DISCOVERY_REQUIREMENTS` JSON schema

Run: `pytest tests/test_discovery_payload.py -q`

---

## Task 5: Discovery client + guard + offline

**Files:**
- Create: `apps/api/app/services/discovery_guard.py`
- Create: `apps/api/app/services/discovery_offline.py`
- Create: `apps/api/app/services/discovery_client.py`
- Test: `apps/api/tests/test_discovery_guard.py`

- [ ] **Step 1: `discovery_guard.apply_discovery_guards()`** — whitelist, held-code filter, avoid_chasing, budget cap, news citation

- [ ] **Step 2: `discovery_offline.build_offline_discovery_report()`** — top 3 candidates by return_1y as `建议关注`

- [ ] **Step 3: `DiscoveryClient.generate_report()`** — mirror `DeepSeekClient._generate_with_tools` simplified; no holdings fund_recs

Run: `pytest tests/test_discovery_guard.py tests/test_discovery_pipeline.py -q`

---

## Task 6: Pipeline + job store + API routes

**Files:**
- Create: `apps/api/app/services/discovery_pipeline.py`
- Create: `apps/api/app/services/discovery_job_store.py`
- Create: `apps/api/app/services/discovery_chat.py`
- Create: `apps/api/app/services/discovery_export.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_api.py` (discovery section)

- [ ] **Step 1: `run_discovery(request, on_progress)`** — wire stages: sector_heat → candidate_pool → news → generating → guarding → saving

- [ ] **Step 2: `discovery_job_store`** — `create_discovery_job`, `get_discovery_job`; worker sets `userId` context

- [ ] **Step 3: Routes**
  - `GET /api/fund-discovery/sectors`
  - `POST /api/fund-discovery/async`
  - `GET /api/fund-discovery/jobs/{id}`
  - `GET|DELETE /api/fund-discovery/reports/{id}`
  - `GET /api/fund-discovery/reports`
  - chat + markdown

- [ ] **Step 4: API tests with `auth_client` + monkeypatch pipeline**

Run: `pytest tests/test_api.py -k discovery -q`

---

## Task 7: Frontend Tab + panels

**Files:**
- Modify: `apps/web/src/lib/api.ts`
- Create: `apps/web/src/components/FundDiscoveryPanel.tsx`
- Create: `apps/web/src/components/DiscoveryReportPanel.tsx`
- Create: `apps/web/src/components/DiscoveryChatPanel.tsx`
- Modify: `apps/web/src/components/JobStatusFloat.tsx`
- Modify: `apps/web/src/components/Dashboard.tsx`

- [ ] **Step 1: api.ts** — types + `fetchDiscoverySectors`, `startDiscoveryJob`, `fetchDiscoveryJob`, report list/get, chat stream

- [ ] **Step 2: FundDiscoveryPanel** — sector chips, budget optional, mode toggle, generate button, risk disclaimer

- [ ] **Step 3: DiscoveryReportPanel** — recommendation cards (code, sector, action, amount, horizon, points/risks)

- [ ] **Step 4: DiscoveryChatPanel** — copy ReportChatPanel pattern with discovery endpoints

- [ ] **Step 5: JobStatusFloat** — add `jobKind: "analysis" | "discovery"` prop; poll correct endpoint

- [ ] **Step 6: Dashboard** — add `discovery` tab between dashboard and report

Run: `cd apps/web && npm run typecheck && npm run build`

---

## Task 8: Full verification + docs

- [ ] **Step 1:** `cd apps/api && pytest tests -q` — all green
- [ ] **Step 2:** `cd apps/web && npm run lint && npm run typecheck`
- [ ] **Step 3:** Update `docs/PROJECT_CONTEXT.md` — 能力清单、API、流程、目录
- [ ] **Step 4:** Manual smoke — discovery tab generates offline report with no API key

---

## Self-Review (plan vs spec)

| Spec § | Task |
|--------|------|
| A 窄池 15~25 | Task 3 |
| C focus_sectors | Task 2 + DiscoveryRequest |
| 混合 UX | Task 7 |
| discovery_guard | Task 5 |
| 独立存储 | Task 1 |
| SSE 追问 | Task 6 + 7 |
| 无持仓可生成 | Task 6 pipeline (empty holdings OK) |
| Markdown 导出 | Task 6 |
| MVP 验收标准 §15 | Task 8 |

No TBD placeholders remain.
