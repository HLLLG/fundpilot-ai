# 短线抄底 + 板块注册表 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付 M0 板块注册表、M1 推荐基金「短线抄底」扫描、M2/M3 大跌反弹雷达及复盘指标，偏新基 discovery + 大跌预筛。

**Architecture:** 新建 `sector_registry` 统一板块元数据；`dip_drop_scanner` 在板块内按 NAV 跌幅预筛后接入现有 `discovery_pipeline`（`scan_mode=dip_swing`）；Phase 2 用 `dip_radar_snapshot` + `GET /api/market/dip-radar` 提供基金级榜单并与 discovery 联动。

**Tech Stack:** FastAPI、Pydantic v2、pytest；Next.js/React/TypeScript、vitest、Playwright 冒烟。

**Design spec:** `docs/superpowers/specs/2026-06-21-dip-swing-discovery-design.md`

---

## Milestone 概览

| ID | 交付 | 验证 |
|----|------|------|
| M0 | `sector_registry` + 旧模块薄封装 | `test_sector_registry.py` + 原 theme/discovery 测试绿 |
| M1 | `dip_swing` 管线 + outcomes + 前端扫描模式 | `test_dip_drop_scanner.py` + API 冒烟 |
| M2 | `dip-radar` API + `DipReboundRadar` UI | `test_dip_radar.py` + 手动/Playwright |
| M3 | 板块历史命中率 + 市场榜/雷达联动关注方向 | `test_fund_dip_rebound_backtest.py` |

---

## M0 — 板块注册表

### Task 1: 定义 `SectorRegistryEntry` 与初始数据

**Files:**
- Create: `apps/api/app/services/sector_registry.py`
- Create: `apps/api/tests/test_sector_registry.py`

- [ ] **Step 1: Write failing tests for registry lookups**

```python
# apps/api/tests/test_sector_registry.py
from app.services.sector_registry import (
    get_sector_entry,
    list_discovery_sector_labels,
    list_theme_board_labels,
    resolve_discovery_quote,
    resolve_market_quote,
)


def test_list_discovery_sector_labels_count_and_cpo():
    labels = list_discovery_sector_labels()
    assert "CPO" in labels
    assert "PCB" in labels
    assert len(labels) >= 21


def test_list_theme_board_labels_includes_ai_and_count():
    labels = list_theme_board_labels()
    assert "人工智能" in labels
    assert len(labels) >= 60


def test_alias_military_maps_to_same_discovery_quote():
    entry_gf = get_sector_entry("国防军工")
    entry_jg = get_sector_entry("军工")
    assert entry_gf is not None
    assert entry_jg is not None
    assert resolve_discovery_quote("国防军工") == resolve_discovery_quote("军工")


def test_market_and_discovery_quotes_differ_for_ai_when_configured():
  # 人工智能：市场榜用 931071，荐基用 930713（与现网一致）
    market = resolve_market_quote("人工智能")
    discovery = resolve_discovery_quote("人工智能")
    assert market is not None and discovery is not None
    assert market.eastmoney_secid != discovery.eastmoney_secid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_sector_registry.py -v`  
Expected: FAIL — `ModuleNotFoundError: sector_registry`

- [ ] **Step 3: Implement `sector_registry.py`**

核心结构：

```python
@dataclass(frozen=True)
class SectorQuoteRef:
    eastmoney_secid: str
    source_code: str | None
    source_type: str  # industry | concept | index
    source_name: str

@dataclass(frozen=True)
class SectorRegistryEntry:
    label: str
    aliases: tuple[str, ...] = ()
    market_quote: SectorQuoteRef | None = None
    discovery_quote: SectorQuoteRef | None = None
    board_kind: str = "concept"
    discovery_eligible: bool = False
    theme_board_eligible: bool = False

# _ENTRIES: dict[str, SectorRegistryEntry] 从 theme_board_snapshot + sector_canonical 迁移生成
```

迁移规则：
- 从 `theme_board_snapshot._THEME_BOARD_WHITELIST` + `_THEME_BOARD_INDEX` + `_THEME_BOARD_ALIAS` 填 `market_quote` / `theme_board_eligible`
- 从 `sector_canonical._CANONICAL_BY_LABEL` + `_DISCOVERY_CHIP_LABELS` 填 `discovery_quote` / `discovery_eligible`
- `军工` 作为 `国防军工` 的 alias；`discovery_quote` 用 BK0490，`market_quote` 用 930749

导出函数：
- `get_sector_entry(label) -> SectorRegistryEntry | None`（走 normalize + alias）
- `list_discovery_sector_labels() -> list[str]`
- `list_theme_board_labels() -> list[str]`
- `resolve_market_quote(label) -> SectorQuoteRef | None`
- `resolve_discovery_quote(label) -> SectorQuoteRef | None`

- [ ] **Step 4: Run tests**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_sector_registry.py -v`  
Expected: PASS

---

### Task 2: 接入 `sector_canonical` 与 `theme_board_snapshot`

**Files:**
- Modify: `apps/api/app/services/sector_canonical.py`
- Modify: `apps/api/app/services/theme_board_snapshot.py`
- Test: `apps/api/tests/test_sector_canonical.py`
- Test: `apps/api/tests/test_theme_board_snapshot.py`

- [ ] **Step 1: 改 `list_discovery_sector_labels` 委托注册表**

```python
# sector_canonical.py
from app.services.sector_registry import list_discovery_sector_labels as _registry_discovery_labels

def list_discovery_sector_labels() -> list[str]:
    return _registry_discovery_labels()
```

保留 `_CANONICAL_BY_LABEL` 与 `get_canonical_sector` 不变（M0 仅标签列表走注册表；canonical 对象仍从原 dict 读，避免大范围重构）。在 `test_sector_canonical.py` 确认 `len(labels) == 21` 仍通过（或更新为 `>= 21` 若注册表条数一致）。

- [ ] **Step 2: 改 `_THEME_BOARD_WHITELIST` 为注册表派生**

```python
# theme_board_snapshot.py
from app.services.sector_registry import list_theme_board_labels

def _theme_board_whitelist() -> tuple[str, ...]:
    return tuple(list_theme_board_labels())
```

将循环 `for name in _THEME_BOARD_WHITELIST` 改为 `for name in _theme_board_whitelist()`；保留 `_THEME_BOARD_INDEX` / `_THEME_BOARD_ALIAS` 作为 fallback 直至 Task 3 完全迁入注册表。

- [ ] **Step 3: 回归**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_sector_canonical.py tests/test_theme_board_snapshot.py tests/test_sector_registry.py -v`  
Expected: PASS

---

### Task 3: 注册表吸收 theme 指数映射（去重）

**Files:**
- Modify: `apps/api/app/services/sector_registry.py`
- Modify: `apps/api/app/services/theme_board_snapshot.py`

- [ ] **Step 1: 将 `_THEME_BOARD_INDEX`、`_THEME_BOARD_ALIAS` 数据迁入 `_ENTRIES`**

`theme_board_snapshot` 中 `_resolve_theme_board_entry(name)` 优先：
1. `resolve_market_quote(name)` from registry
2. fallback 原 `_THEME_BOARD_INDEX` / alias dict（过渡期，测绿后删 fallback）

- [ ] **Step 2: 添加测试：云计算 secid 仍为 930851（非 BK0968）**

```python
def test_cloud_computing_market_quote_secid():
    from app.services.sector_registry import resolve_market_quote
    q = resolve_market_quote("云计算")
    assert q is not None
    assert q.source_code == "930851"
```

- [ ] **Step 3: 全量 API 板块测试**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_theme_board_snapshot.py tests/test_discovery_sector_heat.py -v`

---

## M1 — Phase 1 短线抄底扫描

### Task 4: 扩展模型与类型

**Files:**
- Modify: `apps/api/app/models.py`
- Modify: `apps/web/src/lib/api.ts`

- [ ] **Step 1: 后端类型**

```python
DiscoveryScanMode = Literal["full_market", "portfolio_gap", "dip_swing"]

class DiscoveryRequest(BaseModel):
    ...
    scan_mode: DiscoveryScanMode = "full_market"
    dip_lookback_days: int = Field(default=5, ge=3, le=5)
    dip_min_drop_percent: float = Field(default=3.0, ge=1.0, le=15.0)

class DiscoveryRecommendation(BaseModel):
    ...
    target_exit_days: int | None = None
    fee_break_even_percent: float | None = None
    dip_drop_percent: float | None = None
    rebound_signals: list[dict] = Field(default_factory=list)
```

- [ ] **Step 2: 前端镜像类型**

`api.ts`：`DiscoveryScanMode` 加 `"dip_swing"`；`DiscoveryRequest` / `DiscoveryRecommendation` 加对应可选字段。

- [ ] **Step 3: typecheck**

Run: `cd apps/web && npm run typecheck`

---

### Task 5: `dip_drop_scanner.py`

**Files:**
- Create: `apps/api/app/services/dip_drop_scanner.py`
- Create: `apps/api/tests/test_dip_drop_scanner.py`

- [ ] **Step 1: 失败测试 — 跌幅排序与过滤**

```python
def test_prescreen_prefers_deeper_dip(monkeypatch):
    from app.services.dip_drop_scanner import prescreen_dip_candidates

    rank_rows = [
        {"基金代码": "000001", "基金简称": "深跌A", "基金公司": "x"},
        {"基金代码": "000002", "基金简称": "浅跌B", "基金公司": "x"},
    ]
    nav_by_code = {
        "000001": {"recent_5d_change_percent": -6.0, "distance_from_high_percent": -10.0},
        "000002": {"recent_5d_change_percent": -2.0, "distance_from_high_percent": -3.0},
    }
    rows = prescreen_dip_candidates(
        sector_label="半导体",
        rank_rows=rank_rows,
        nav_by_code=nav_by_code,
        lookback_days=5,
        min_drop_percent=3.0,
        keywords=("半导体",),
        name_resolver=lambda c: rank_rows[int(c[-1]) - 1]["基金简称"],
    )
    assert rows[0]["fund_code"] == "000001"
    assert rows[0]["dip_drop_percent"] <= -5.0


def test_prescreen_skips_shallow_dip():
    ...
    assert rows == []
```

- [ ] **Step 2: 实现 `prescreen_dip_candidates` + `build_dip_pool_for_sectors`**

公开 API：

```python
def prescreen_dip_candidates(...) -> list[dict]: ...

def build_dip_pool_for_sectors(
    target_sectors: list[str],
    *,
    lookback_days: int = 5,
    min_drop_percent: float = 3.0,
    exclude_codes: set[str],
    per_sector_top: int = 8,
    pool_cap: int = 30,
    budget_seconds: float = 15.0,
) -> list[dict]:
    ...
```

复用 `discovery_candidate_pool._sector_keywords`、`_name_matches_sector`、`_MIN_SCALE_YI`。  
`rebound_signals` 调用 `sector_momentum._classify_pattern` 逻辑或抽取轻量 helper。  
`rebound_score` 对齐 `discovery_selection_strategy.dip_rebound_score` 并 normalize 到 0–100。

- [ ] **Step 3: pytest 绿**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_dip_drop_scanner.py -v`

---

### Task 6: `discovery_target_sectors` + `discovery_pipeline`

**Files:**
- Modify: `apps/api/app/services/discovery_target_sectors.py`
- Modify: `apps/api/app/services/discovery_pipeline.py`
- Modify: `apps/api/app/services/discovery_candidate_pool.py`
- Test: `apps/api/tests/test_discovery_target_sectors.py`（若无则新建）

- [ ] **Step 1: `dip_swing` 板块选择测试**

```python
def test_dip_swing_selects_deepest_sectors_first():
    heat = [
        {"sector_label": "半导体", "heat_score": 1, "change_5d_percent": -8.0},
        {"sector_label": "银行", "heat_score": 2, "change_5d_percent": -1.0},
        {"sector_label": "光伏", "heat_score": 3, "change_5d_percent": -5.0},
    ]
    sectors = select_target_sectors([], None, heat, scan_mode="dip_swing", max_sectors=3)
    assert sectors[0] == "半导体"
    assert "银行" not in sectors[:2]
```

- [ ] **Step 2: 实现 `select_target_sectors` 的 `dip_swing` 分支**

按 `change_5d_percent` 升序（None 排后）；`focus_sectors` 仍最优先。

- [ ] **Step 3: `discovery_pipeline.run_discovery` 分支**

```python
DISCOVERY_JOB_STAGES["dip_prescreen"] = "预筛大跌基金…"

if request.scan_mode == "dip_swing":
    progress("dip_prescreen")
    from app.services.dip_drop_scanner import build_dip_pool_for_sectors
    pool = build_dip_pool_for_sectors(
        target_sectors,
        lookback_days=request.dip_lookback_days,
        min_drop_percent=request.dip_min_drop_percent,
        exclude_codes=held_codes,
    )
    pool = enrich_candidates(pool)
else:
    pool = build_candidate_pool(...)
```

`scan_mode=dip_swing` 时若 `selection_strategy` 非 `dip_rebound`，强制为 `dip_rebound`。

- [ ] **Step 4: 集成测试（mock AI）**

扩展 `tests/test_api.py` 或 `tests/test_discovery_pipeline.py`：`scan_mode=dip_swing` 返回 job 完成且 `candidate_pool` 含 `dip_drop_percent` 字段。

---

### Task 7: Facts / Prompt / Guard / Outcomes

**Files:**
- Modify: `apps/api/app/services/discovery_facts.py`
- Modify: `apps/api/app/services/discovery_prompt.py`
- Modify: `apps/api/app/services/discovery_guard.py`
- Modify: `apps/api/app/services/discovery_outcomes.py`
- Test: `apps/api/tests/test_discovery_outcomes.py`（新建或扩展）

- [ ] **Step 1: `build_discovery_facts` 增加 `dip_swing` 块**（见 design spec §5.4）

- [ ] **Step 2: `DEFAULT_DISCOVERY_ROLE_PROMPT` 增补 `dip_swing` 段落**

- [ ] **Step 3: `discovery_guard`：`dip_swing` 下候选近 1 日涨幅 > 3% 降档**

- [ ] **Step 4: outcomes 测试**

```python
def test_hit_take_profit_within_3_days():
    # mock nav series: day0 rec, day2 +3% cumulative >= 2.5% threshold
    ...
    assert item["hit_take_profit_within_days"] is True
```

- [ ] **Step 5: pytest**

Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests/test_discovery_outcomes.py tests/test_discovery_guard.py -v`

---

### Task 8: 前端 `FundDiscoveryPanel`

**Files:**
- Modify: `apps/web/src/components/FundDiscoveryPanel.tsx`
- Modify: `apps/web/src/lib/api.ts`（`startFundDiscovery` body）
- Test: `apps/web/src/lib/api.test.ts` 或 vitest（若有）

- [ ] **Step 1: 扫描模式第三项「短线抄底」**

```typescript
const SCAN_MODE_OPTIONS = [
  ...
  { id: "dip_swing", label: "短线抄底", hint: "近几日大跌、有反弹信号；默认 2～5 天波段" },
];
```

- [ ] **Step 2: `scanMode === "dip_swing"` 时 `setSelectionStrategy("dip_rebound")`**

- [ ] **Step 3: 激进预设提示条** — 非 aggressive 时显示「建议切换激进波段预设」+ 一键 `applyInvestmentPreset("aggressive_swing")`

- [ ] **Step 4: 高级折叠：回看 3/5 日、最小跌幅 3%/5%** — 传入 `dipLookbackDays` / `dipMinDropPercent`

- [ ] **Step 5: 动态「今日跌深板块」chips** — `sectors` 按 `change_5d_percent` 升序取 Top 5，样式与现有 chips 一致

- [ ] **Step 6: `DiscoveryReportPanel` 展示新字段**（`dip_drop_percent`、`fee_break_even_percent`、`target_exit_days`、`rebound_signals`）

- [ ] **Step 7: 验证**

Run: `cd apps/web && npm run lint && npm run typecheck && npm run build`  
Run: `cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q`（全量回归）

---

### Task 9: 更新 `PROJECT_CONTEXT.md`（M1 部分）

**Files:**
- Modify: `docs/PROJECT_CONTEXT.md`

- [ ] 能力清单、API 表、`DiscoveryRequest` 摘要、推荐基金流程图补充 `dip_swing`。

---

## M2 — 大跌反弹雷达

### Task 10: `dip_radar_snapshot.py` + API

**Files:**
- Create: `apps/api/app/services/dip_radar_snapshot.py`
- Create: `apps/api/tests/test_dip_radar.py`
- Modify: `apps/api/app/main.py`

- [ ] **Step 1: 失败测试**

```python
def test_get_dip_radar_returns_items_sorted_by_drop(client, monkeypatch):
    monkeypatch.setattr(
        "app.services.dip_radar_snapshot.build_dip_radar_snapshot",
        lambda **kw: {
            "refreshed_at": "2026-06-21T12:00:00Z",
            "trade_date": "2026-06-21",
            "lookback_days": 5,
            "items": [
                {"fund_code": "000001", "fund_name": "A", "dip_drop_percent": -7.0, "rebound_score": 80},
                {"fund_code": "000002", "fund_name": "B", "dip_drop_percent": -4.0, "rebound_score": 60},
            ],
            "sector_dip_leaders": [],
        },
    )
    resp = client.get("/api/market/dip-radar?lookback_days=5&limit=20")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["dip_drop_percent"] == -7.0
```

- [ ] **Step 2: 实现 `build_dip_radar_snapshot`**

跨注册表 `discovery_eligible` + `theme_board_eligible` 板块并集，调用 `build_dip_pool_for_sectors` 逻辑或 rank 全表 Top N（`limit` 默认 20）。缓存 key `dip:radar:v1:{trade_date}:{lookback}`。

- [ ] **Step 3: 路由**

```python
@app.get("/api/market/dip-radar")
def market_dip_radar(
    lookback_days: int = 5,
    sector: str | None = None,
    limit: int = 20,
    force_refresh: bool = False,
) -> dict:
    ...
```

- [ ] **Step 4: pytest + conftest stub**

在 `conftest.py` 为 e2e 离线 stub `build_dip_radar_snapshot`。

---

### Task 11: 前端雷达 UI

**Files:**
- Create: `apps/web/src/lib/dipRadar.ts`
- Create: `apps/web/src/components/DipReboundRadar.tsx`
- Modify: `apps/web/src/components/MarketTab.tsx`
- Modify: `apps/web/src/lib/marketThemeBoard.ts`（`MarketSubTab` 加 `"dip_radar"`）
- Modify: `apps/web/src/lib/api.ts`

- [ ] **Step 1: `fetchDipRadar` + 格式化 helper**

- [ ] **Step 2: `DipReboundRadar` 列表 UI**

列：基金名、板块、近 N 日跌幅、反弹信号、rebound_score；底部免责文案；空态/加载/刷新。

- [ ] **Step 3: `MarketTab` 子 Tab「大跌雷达」**

`loadMarketSubTab` / `saveMarketSubTab` 支持第三项；sessionStorage key 保持兼容（未知值回退 `themes`）。

- [ ] **Step 4: 「深度扫描」按钮**

```typescript
function openDipSwingDiscovery(sectorLabel: string) {
  saveDashboardTab("discovery"); // 或项目既有 tab 切换 helper
  sessionStorage.setItem("fundpilot-discovery-prefill", JSON.stringify({
    scanMode: "dip_swing",
    focusSectors: [sectorLabel].slice(0, 3),
  }));
}
```

`FundDiscoveryPanel` mount 时读取 prefill 并应用。

- [ ] **Step 5: build + lint**

Run: `cd apps/web && npm run lint && npm run typecheck && npm run build`

---

## M3 — 历史命中率 + 联动

### Task 12: `fund_dip_rebound_backtest.py`

**Files:**
- Create: `apps/api/app/services/fund_dip_rebound_backtest.py`
- Test: `apps/api/tests/test_fund_dip_rebound_backtest.py`

- [ ] **Step 1: 测试板块代理命中率**

```python
def test_sector_rebound_rate_computes_from_kline_series():
    from app.services.fund_dip_rebound_backtest import compute_sector_dip_rebound_stats

    series = [
        {"date": "2026-01-01", "change_percent": -4.0},
        {"date": "2026-01-02", "change_percent": 1.0},
        {"date": "2026-01-03", "change_percent": 1.5},
        {"date": "2026-01-04", "change_percent": 0.5},
    ]
    stats = compute_sector_dip_rebound_stats(
        series,
        dip_threshold_percent=3.0,
        rebound_threshold_percent=2.5,
        forward_days=3,
    )
    assert stats["sample_count"] >= 1
```

- [ ] **Step 2: 实现** — 滑动窗口：当日跌幅 ≥ 阈值 → 看未来 3 日累计是否 ≥ `fee_break_even`

- [ ] **Step 3: 接入 `dip_radar_snapshot`** — 每项 `historical_hint` 填板块 stats

---

### Task 13: 主题板块 + 关注方向联动

**Files:**
- Modify: `apps/web/src/components/ThemeSectorOverview.tsx`
- Modify: `apps/web/src/lib/storage.ts` 或 `marketThemeBoard.ts`

- [ ] **Step 1: 主题榜行 actions**

「看大跌基金」→ `saveMarketSubTab("dip_radar")` + sector query  
「加入关注方向」→ `sessionStorage` `fundpilot-discovery-focus-sectors`（max 3）

- [ ] **Step 2: `FundDiscoveryPanel` 读取 focus prefill**

- [ ] **Step 3: Playwright 冒烟（可选）**

`apps/web/tests/e2e` 增加：打开市场 Tab → 大跌雷达 → 列表渲染不报错。

---

### Task 14: 文档与全量验证

**Files:**
- Modify: `docs/PROJECT_CONTEXT.md`

- [ ] **Step 1: 更新能力清单、API、`MarketSubTab`、环境变量（若有）**

- [ ] **Step 2: 全量 CI 命令**

```bash
cd apps/api && ./.venv/Scripts/python.exe -m pytest tests -q -n auto --dist loadscope
cd apps/web && npm run lint && npm run typecheck && npm run build
```

Expected: 全绿；pytest 总量较现网 +15～25 项。

---

## conftest 补充

**File:** `apps/api/tests/conftest.py`

- [ ] stub `build_dip_pool_for_sectors` 返回固定 2 条候选（含 `dip_drop_percent`）
- [ ] stub `build_dip_radar_snapshot` 返回固定 radar payload

避免 CI 拉 AkShare/东财。

---

## 执行顺序依赖

```text
Task 1 → 2 → 3 (M0)
    → Task 4 → 5 → 6 → 7 → 8 → 9 (M1)
        → Task 10 → 11 (M2)
            → Task 12 → 13 → 14 (M3)
```

M1 可在 M0 Task 2 完成后并行启动 Task 4（模型不依赖 registry 完成），但 **Task 5 应用 registry 的板块 label**。

---

## Spec 覆盖自检

| Spec 章节 | 任务 |
|-----------|------|
| §4 板块注册表 | Task 1–3 |
| §5 Phase 1 dip_swing | Task 4–9 |
| §6 Phase 2 雷达 | Task 10–11 |
| §6.3 历史命中率 | Task 12 |
| §6.4 联动 | Task 13 |
| §8 测试 | 各 Task pytest |
| §9 验收标准 | Task 14 全量验证 |

无 TBD 占位。
