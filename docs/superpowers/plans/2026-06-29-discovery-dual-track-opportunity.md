# Discovery Dual-Track Opportunity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dual-track sector-first discovery pipeline that selects balanced momentum and setup sectors, treats pullback acceptance as an entry hint, then builds the fund candidate pool from sector evidence instead of name matching alone.

**Architecture:** Add a focused sector opportunity scorer, reuse existing `discovery_sector_heat`, `sector_fund_flow_context`, and sector registry data, then feed selected sector opportunities into `discovery_candidate_pool`. Keep LLM responsibility limited to final explanation and fund selection. Budget slow optional context so discovery does not appear stuck while pre-LLM data is still building.

**Tech Stack:** Python 3.11, FastAPI service modules, SQLite/MySQL database helpers, pytest, existing FundPilot discovery services.

---

## File Structure

- Create `apps/api/app/services/discovery_sector_opportunity.py`: pure scoring and selection of momentum/setup sector opportunities.
- Create `apps/api/tests/test_discovery_sector_opportunity.py`: unit tests for scoring tracks, entry hints, penalties, and class diversification.
- Modify `apps/api/app/database.py`: add reverse lookup helper for `fund_primary_sectors_global` by sector names.
- Modify `apps/api/app/db_migrations.py`: add sector-name index for reverse lookup.
- Modify `apps/api/app/services/discovery_candidate_pool.py`: accept sector opportunity metadata, query primary-sector mappings, de-duplicate fund families, and annotate candidate rows with sector opportunity context.
- Modify `apps/api/app/services/discovery_streaming.py`: select target sectors through the dual-track scorer, emit clearer pre-LLM stage labels, and pass opportunity metadata into facts/payload.
- Modify `apps/api/app/services/discovery_pipeline.py`: keep async non-streaming path aligned with the same sector-first pipeline.
- Modify `apps/api/app/services/discovery_facts.py`: include `sector_opportunities` and budget slow signal context.
- Modify `apps/api/app/services/discovery_payload.py`: include slim `sector_opportunities` for the LLM.
- Modify tests under `apps/api/tests/test_discovery_*.py`: cover integration and payload fields.
- Update `docs/PROJECT_CONTEXT.md`: add the new discovery candidate-pool behavior.

---

### Task 1: Sector Opportunity Scorer

**Files:**
- Create: `apps/api/app/services/discovery_sector_opportunity.py`
- Test: `apps/api/tests/test_discovery_sector_opportunity.py`

- [ ] **Step 1: Write failing tests for momentum/setup/pullback behavior**

Add `apps/api/tests/test_discovery_sector_opportunity.py`:

```python
from __future__ import annotations

from app.services.discovery_sector_opportunity import select_sector_opportunities


def test_selects_balanced_momentum_and_setup_tracks():
    heat = [
        {"sector_label": "半导体", "change_1d_percent": 1.2, "change_5d_percent": 4.5, "heat_score": 88},
        {"sector_label": "创新药", "change_1d_percent": -0.4, "change_5d_percent": -1.2, "heat_score": 52},
        {"sector_label": "白酒", "change_1d_percent": 4.8, "change_5d_percent": 9.0, "heat_score": 95},
    ]
    flow = {
        "半导体": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 12.0,
            "cumulative_5d_net_yi": 28.0,
            "pattern_label": "price_flow_aligned_up",
        },
        "创新药": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 8.0,
            "cumulative_5d_net_yi": 3.0,
            "pattern_label": "accumulation",
        },
        "白酒": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": -5.0,
            "cumulative_5d_net_yi": -15.0,
            "pattern_label": "distribution",
        },
    }

    result = select_sector_opportunities(
        heat,
        sector_flow_by_label=flow,
        focus_sectors=[],
        max_total=4,
        momentum_slots=2,
        setup_slots=2,
    )

    tracks = {item["sector_label"]: item["track"] for item in result}
    assert tracks["半导体"] == "momentum"
    assert tracks["创新药"] == "setup"
    assert "白酒" not in tracks


def test_pullback_acceptance_is_entry_hint_not_a_track():
    heat = [
        {"sector_label": "机器人", "change_1d_percent": -0.8, "change_5d_percent": 3.8, "heat_score": 70},
    ]
    flow = {
        "机器人": {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 2.0,
            "cumulative_5d_net_yi": 11.0,
            "pattern_label": "price_flow_aligned_up",
        }
    }

    result = select_sector_opportunities(
        heat,
        sector_flow_by_label=flow,
        focus_sectors=[],
        max_total=2,
        momentum_slots=2,
        setup_slots=0,
    )

    assert result[0]["track"] == "momentum"
    assert result[0]["entry_hint"] == "回调承接观察"


def test_sector_class_diversification_limits_one_chain_dominance():
    heat = [
        {"sector_label": "半导体", "change_1d_percent": 1.0, "change_5d_percent": 4.0, "heat_score": 90},
        {"sector_label": "半导体材料", "change_1d_percent": 1.1, "change_5d_percent": 4.2, "heat_score": 91},
        {"sector_label": "CPO", "change_1d_percent": 0.9, "change_5d_percent": 3.5, "heat_score": 85},
        {"sector_label": "创新药", "change_1d_percent": 0.7, "change_5d_percent": 2.4, "heat_score": 78},
    ]
    flow = {
        row["sector_label"]: {
            "available": True,
            "date_aligned": True,
            "today_main_force_net_yi": 5.0,
            "cumulative_5d_net_yi": 10.0,
            "pattern_label": "price_flow_aligned_up",
        }
        for row in heat
    }

    result = select_sector_opportunities(
        heat,
        sector_flow_by_label=flow,
        focus_sectors=[],
        max_total=4,
        momentum_slots=4,
        setup_slots=0,
        max_per_group=2,
    )

    labels = [item["sector_label"] for item in result]
    assert len([label for label in labels if label in {"半导体", "半导体材料", "CPO"}]) == 2
    assert "创新药" in labels
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests\test_discovery_sector_opportunity.py -q
```

Expected: fail with `ModuleNotFoundError: No module named 'app.services.discovery_sector_opportunity'`.

- [ ] **Step 3: Implement pure scorer**

Create `apps/api/app/services/discovery_sector_opportunity.py` with:

```python
from __future__ import annotations

from typing import Any

MOMENTUM_TRACK = "momentum"
SETUP_TRACK = "setup"

_DISTRIBUTION_PATTERNS = {"distribution", "weak_outflow"}
_SETUP_PATTERNS = {"accumulation", "multi_day_outflow_then_inflow", "flow_turning_positive"}
_MOMENTUM_PATTERNS = {"price_flow_aligned_up", "aligned_up"}

_SECTOR_GROUPS = {
    "半导体": "tmt",
    "半导体材料": "tmt",
    "存储芯片": "tmt",
    "CPO": "tmt",
    "人工智能": "tmt",
    "机器人": "tmt",
    "创新药": "healthcare",
    "港股医药": "healthcare",
    "医药": "healthcare",
    "医疗器械": "healthcare",
    "白酒": "consumer",
    "消费电子": "consumer",
    "银行": "finance",
    "证券": "finance",
    "有色金属": "cyclical",
    "新能源车": "manufacturing",
    "光伏": "manufacturing",
    "电网设备": "manufacturing",
    "恒生科技": "hongkong",
}


def select_sector_opportunities(
    sector_heat: list[dict],
    *,
    sector_flow_by_label: dict[str, dict] | None = None,
    focus_sectors: list[str] | None = None,
    max_total: int = 8,
    momentum_slots: int = 4,
    setup_slots: int = 4,
    max_per_group: int = 2,
) -> list[dict[str, Any]]:
    flow_by_label = sector_flow_by_label or {}
    focus = {str(label).strip() for label in (focus_sectors or []) if str(label).strip()}
    rows = [_score_row(row, flow_by_label.get(str(row.get("sector_label") or "").strip()), focus) for row in sector_heat]
    rows = [row for row in rows if row is not None]

    momentum = sorted(
        [row for row in rows if row["track"] == MOMENTUM_TRACK],
        key=lambda row: row["score"],
        reverse=True,
    )
    setup = sorted(
        [row for row in rows if row["track"] == SETUP_TRACK],
        key=lambda row: row["score"],
        reverse=True,
    )

    selected: list[dict[str, Any]] = []
    selected.extend(_take_with_group_limit(momentum, momentum_slots, selected, max_per_group))
    selected.extend(_take_with_group_limit(setup, setup_slots, selected, max_per_group))

    remaining = max_total - len(selected)
    if remaining > 0:
        pool = sorted(
            [row for row in rows if row["sector_label"] not in {item["sector_label"] for item in selected}],
            key=lambda row: row["score"],
            reverse=True,
        )
        selected.extend(_take_with_group_limit(pool, remaining, selected, max_per_group))
    return selected[:max_total]


def _score_row(row: dict, flow: dict | None, focus: set[str]) -> dict[str, Any] | None:
    label = str(row.get("sector_label") or "").strip()
    if not label:
        return None
    change_1d = _num(row.get("change_1d_percent"))
    change_5d = _num(row.get("change_5d_percent"))
    heat_score = _num(row.get("heat_score")) or 0.0
    flow = flow or {}
    pattern = str(flow.get("pattern_label") or "").strip()
    date_aligned = flow.get("date_aligned") is not False
    today_flow = _num(flow.get("today_main_force_net_yi"))
    flow_5d = _num(flow.get("cumulative_5d_net_yi"))

    penalties: list[str] = []
    evidence: list[str] = []
    if pattern in _DISTRIBUTION_PATTERNS:
        penalties.append("资金背离或持续流出")
    if flow and not date_aligned:
        penalties.append("资金流日期未对齐")
    if change_1d is not None and change_1d >= 4.0:
        penalties.append("单日涨幅过热")

    focus_bonus = 6.0 if label in focus else 0.0
    flow_bonus = _positive_score(today_flow, scale=2.0, cap=12.0) + _positive_score(flow_5d, scale=1.0, cap=12.0)
    if today_flow is not None and today_flow > 0:
        evidence.append("今日主力净流入")
    if flow_5d is not None and flow_5d > 0:
        evidence.append("5日主力净流入")

    momentum_score = (
        max(change_1d or 0.0, 0.0) * 5.0
        + max(change_5d or 0.0, 0.0) * 4.0
        + flow_bonus
        + heat_score * 0.15
        + focus_bonus
    )
    if pattern in _MOMENTUM_PATTERNS:
        momentum_score += 10.0
        evidence.append("价涨资金配合")
    if change_1d is not None and change_1d >= 4.0:
        momentum_score -= 12.0
    if pattern in _DISTRIBUTION_PATTERNS:
        momentum_score -= 30.0

    setup_score = (
        _setup_price_score(change_1d, change_5d)
        + flow_bonus * 1.15
        + heat_score * 0.08
        + focus_bonus
    )
    if pattern in _SETUP_PATTERNS:
        setup_score += 14.0
        evidence.append("资金拐点或吸筹形态")
    if pattern in _DISTRIBUTION_PATTERNS:
        setup_score -= 28.0

    if max(momentum_score, setup_score) <= 0:
        return None

    track = MOMENTUM_TRACK if momentum_score >= setup_score else SETUP_TRACK
    score = round(max(momentum_score, setup_score), 2)
    entry_hint = _entry_hint(track, change_1d, change_5d, pattern, penalties)
    confidence = _confidence(flow, date_aligned, penalties)

    return {
        "sector_label": label,
        "track": track,
        "score": score,
        "confidence": confidence,
        "entry_hint": entry_hint,
        "evidence": evidence[:5],
        "penalties": penalties[:5],
        "change_1d_percent": change_1d,
        "change_5d_percent": change_5d,
        "today_main_force_net_yi": today_flow,
        "cumulative_5d_net_yi": flow_5d,
        "pattern_label": pattern or None,
        "sector_group": _sector_group(label),
    }


def _take_with_group_limit(
    rows: list[dict[str, Any]],
    limit: int,
    already_selected: list[dict[str, Any]],
    max_per_group: int,
) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for item in [*already_selected, *picked]:
        group = str(item.get("sector_group") or item.get("sector_label"))
        counts[group] = counts.get(group, 0) + 1
    for row in rows:
        if len(picked) >= limit:
            break
        if row["sector_label"] in {item["sector_label"] for item in [*already_selected, *picked]}:
            continue
        group = str(row.get("sector_group") or row["sector_label"])
        if counts.get(group, 0) >= max_per_group:
            continue
        picked.append(row)
        counts[group] = counts.get(group, 0) + 1
    return picked


def _entry_hint(track: str, change_1d: float | None, change_5d: float | None, pattern: str, penalties: list[str]) -> str:
    if "资金背离或持续流出" in penalties:
        return "资金背离，暂不入池"
    if change_1d is not None and change_1d >= 4.0:
        return "高位谨慎"
    if track == MOMENTUM_TRACK and change_1d is not None and change_1d < 0 and (change_5d or 0) > 0:
        return "回调承接观察"
    if track == SETUP_TRACK:
        return "蓄势观察"
    return "可分批关注"


def _confidence(flow: dict, date_aligned: bool, penalties: list[str]) -> str:
    if not flow or not flow.get("available"):
        return "低"
    if not date_aligned:
        return "低"
    if penalties:
        return "中"
    return "中"


def _setup_price_score(change_1d: float | None, change_5d: float | None) -> float:
    c1 = change_1d or 0.0
    c5 = change_5d or 0.0
    score = 0.0
    if -2.5 <= c1 <= 1.5:
        score += 8.0
    if -4.0 <= c5 <= 2.0:
        score += 8.0
    if c1 > 3.0 or c5 > 6.0:
        score -= 12.0
    return score


def _positive_score(value: float | None, *, scale: float, cap: float) -> float:
    if value is None or value <= 0:
        return 0.0
    return min(cap, value / scale)


def _sector_group(label: str) -> str:
    return _SECTOR_GROUPS.get(label, label)


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests\test_discovery_sector_opportunity.py -q
```

Expected: `3 passed`.

---

### Task 2: Reverse Sector Lookup and Candidate Pool Integration

**Files:**
- Modify: `apps/api/app/database.py`
- Modify: `apps/api/app/db_migrations.py`
- Modify: `apps/api/app/services/discovery_candidate_pool.py`
- Test: `apps/api/tests/test_discovery_candidate_pool_opportunity.py`

- [ ] **Step 1: Write failing candidate-pool tests**

Add `apps/api/tests/test_discovery_candidate_pool_opportunity.py`:

```python
from __future__ import annotations

from app.services.discovery_candidate_pool import build_candidate_pool


def test_candidate_pool_uses_sector_primary_rows_before_name_matching(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_open_fund_rank_cached",
        lambda limit=300: [
            {"fund_code": "111111", "fund_name": "泛科技基金", "fund_scale_yi": 10, "return_3m_percent": 2},
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors", lambda: [])
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda labels, limit_per_sector=20: [
            {
                "fund_code": "020640",
                "sector_name": "半导体",
                "source": "precompute_benchmark",
                "confidence": 0.8,
                "fund_name": "广发半导体设备ETF联接C",
            }
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_new_fund_offerings", lambda limit=300: [])

    pool = build_candidate_pool(
        target_sectors=["半导体"],
        sector_opportunities=[{"sector_label": "半导体", "track": "momentum", "score": 80, "entry_hint": "可分批关注"}],
    )

    assert pool[0]["fund_code"] == "020640"
    assert pool[0]["selection_reason"] == "板块机会映射"
    assert pool[0]["opportunity_track"] == "momentum"
    assert pool[0]["entry_hint"] == "可分批关注"


def test_candidate_pool_dedupes_same_fund_family(monkeypatch):
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_open_fund_rank_cached", lambda limit=300: [])
    monkeypatch.setattr("app.services.discovery_candidate_pool.list_fund_primary_sectors", lambda: [])
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda labels, limit_per_sector=20: [
            {"fund_code": "020639", "sector_name": "半导体", "fund_name": "广发半导体设备ETF联接A"},
            {"fund_code": "020640", "sector_name": "半导体", "fund_name": "广发半导体设备ETF联接C"},
            {"fund_code": "021533", "sector_name": "半导体", "fund_name": "天弘半导体设备指数C"},
        ],
    )
    monkeypatch.setattr("app.services.discovery_candidate_pool.fetch_new_fund_offerings", lambda limit=300: [])

    pool = build_candidate_pool(target_sectors=["半导体"])

    codes = [item["fund_code"] for item in pool]
    assert len({"020639", "020640"} & set(codes)) == 1
    assert "021533" in codes
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests\test_discovery_candidate_pool_opportunity.py -q
```

Expected: fail because `list_fund_primary_sectors_by_sector_names` does not exist or `sector_opportunities` is not accepted.

- [ ] **Step 3: Add database reverse lookup**

In `apps/api/app/database.py`, add:

```python
def list_fund_primary_sectors_by_sector_names(
    sector_names: list[str],
    *,
    limit_per_sector: int = 20,
) -> list[dict[str, Any]]:
    normalized = []
    seen = set()
    for raw in sector_names:
        label = str(raw or "").strip()
        if label and label not in seen:
            seen.add(label)
            normalized.append(label)
    if not normalized:
        return []
    placeholders = ",".join("?" * len(normalized))
    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT fund_code, sector_name, intraday_index_name, source, confidence, detail, resolved_at
            FROM fund_primary_sectors_global
            WHERE sector_name IN ({placeholders})
            ORDER BY confidence DESC, resolved_at DESC
            """,
            tuple(normalized),
        ).fetchall()
    counts: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _row_to_dict(row)
        label = str(payload.get("sector_name") or "")
        if counts.get(label, 0) >= limit_per_sector:
            continue
        payload["updated_at"] = payload.get("resolved_at")
        result.append(payload)
        counts[label] = counts.get(label, 0) + 1
    return result
```

In `apps/api/app/db_migrations.py`, update `_migrate_fund_primary_sectors_global` to create:

```python
connection.execute(
    """
    CREATE INDEX IF NOT EXISTS idx_fund_primary_sectors_global_sector
    ON fund_primary_sectors_global (sector_name, confidence DESC, resolved_at DESC)
    """
)
```

- [ ] **Step 4: Integrate opportunity rows in candidate pool**

Modify `apps/api/app/services/discovery_candidate_pool.py`:

```python
from app.database import (
    get_fund_profile_by_code,
    list_fund_primary_sectors,
    list_fund_primary_sectors_by_sector_names,
)
```

Update `build_candidate_pool` signature:

```python
def build_candidate_pool(
    target_sectors: list[str],
    *,
    exclude_codes: set[str] | None = None,
    fund_type_preference: str = "any",
    selection_strategy: SelectionStrategy = "balanced",
    per_sector: int = _PER_SECTOR,
    pool_cap: int = _POOL_CAP,
    fetch_rank=None,
    fetch_new_funds=None,
    sector_opportunities: list[dict] | None = None,
) -> list[dict]:
```

Inside it, build:

```python
opportunity_by_sector = {
    str(item.get("sector_label") or "").strip(): item
    for item in (sector_opportunities or [])
    if str(item.get("sector_label") or "").strip()
}
global_primary_rows = list_fund_primary_sectors_by_sector_names(target_sectors, limit_per_sector=20)
primary_rows = list_fund_primary_sectors() + global_primary_rows
family_seen: set[str] = set()
```

Pass `opportunity_by_sector` and `family_seen` to `_candidates_for_sector`, annotate fixed entries with opportunity fields, and skip duplicate family keys:

```python
def _family_key(name: str) -> str:
    text = name.replace("ETF联接", "").replace("ETF链接", "")
    for suffix in ("A", "C", "A类", "C类"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.strip() or name.strip()
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests\test_discovery_candidate_pool_opportunity.py tests\test_discovery_candidate_pool_cache.py -q
```

Expected: all tests pass.

---

### Task 3: Stream/Pipeline Wiring and Payload Fields

**Files:**
- Modify: `apps/api/app/services/discovery_streaming.py`
- Modify: `apps/api/app/services/discovery_pipeline.py`
- Modify: `apps/api/app/services/discovery_facts.py`
- Modify: `apps/api/app/services/discovery_payload.py`
- Test: `apps/api/tests/test_discovery_streaming.py`
- Test: `apps/api/tests/test_discovery_payload.py`

- [ ] **Step 1: Write failing tests for opportunity payload and stage clarity**

Add to `apps/api/tests/test_discovery_payload.py`:

```python
def test_build_user_payload_includes_sector_opportunities():
    facts = _discovery_facts()
    facts["sector_opportunities"] = [
        {
            "sector_label": "半导体",
            "track": "momentum",
            "score": 81.5,
            "entry_hint": "可分批关注",
            "evidence": ["价涨资金配合"],
            "penalties": [],
        }
    ]
    payload = build_user_payload(
        discovery_facts=facts,
        profile=_profile(),
        focus_sectors=["半导体"],
    )
    opportunities = payload["discovery_facts"]["sector_opportunities"]
    assert opportunities[0]["sector_label"] == "半导体"
    assert opportunities[0]["track"] == "momentum"
    assert opportunities[0]["entry_hint"] == "可分批关注"
```

Add to `apps/api/tests/test_discovery_streaming.py`:

```python
def test_stream_discovery_emits_context_stage_before_model(monkeypatch):
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", _FAKE_DEEPSEEK_KEY)
    refresh_settings()
    _patch_pipeline(monkeypatch)
    monkeypatch.setattr(
        "app.services.discovery_streaming.select_sector_opportunities",
        lambda heat, **kwargs: [{"sector_label": "半导体", "track": "momentum", "score": 70}],
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.stream_chat_completion",
        lambda **kwargs: iter(['{"title":"t","summary":"s","recommendations":[],"caveats":[]}']),
    )
    monkeypatch.setattr(
        "app.services.discovery_streaming.build_discovery_report_from_parsed",
        lambda parsed, **kwargs: MagicMock(id="ctx-1", model_dump=lambda mode="json": {"id": "ctx-1"}),
    )

    events = list(stream_discovery(_request(), user_id=1))
    labels = [event.get("label", "") for event in events if event.get("type") == "stage"]
    assert any("整理荐基上下文" in label for label in labels)
    assert events[-1]["type"] == "done"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests\test_discovery_payload.py::test_build_user_payload_includes_sector_opportunities tests\test_discovery_streaming.py::test_stream_discovery_emits_context_stage_before_model -q
```

Expected: fail because payload/stage fields are not present.

- [ ] **Step 3: Wire scorer into streaming and async pipeline**

In both `discovery_streaming.py` and `discovery_pipeline.py`:

```python
from app.services.discovery_sector_opportunity import select_sector_opportunities
from app.services.sector_fund_flow_context import build_sector_fund_flow_context
```

After `sector_heat` and target sectors, build flow map for visible top heat rows and selected/focused sectors, then:

```python
sector_opportunities = select_sector_opportunities(
    sector_heat,
    sector_flow_by_label=sector_flow_by_label,
    focus_sectors=list(request.focus_sectors),
    max_total=8,
    momentum_slots=4,
    setup_slots=4,
)
target_sectors = [item["sector_label"] for item in sector_opportunities] or target_sectors
```

Pass `sector_opportunities=sector_opportunities` to `build_candidate_pool` and `build_discovery_facts`.

- [ ] **Step 4: Add facts and payload fields**

In `build_discovery_facts`, add parameter:

```python
sector_opportunities: list[dict] | None = None,
```

Add facts key:

```python
"sector_opportunities": list(sector_opportunities or []),
```

In `build_user_payload`, include:

```python
"sector_opportunities": _slim_sector_opportunities(discovery_facts.get("sector_opportunities") or []),
```

Add helper:

```python
def _slim_sector_opportunities(items: list[dict]) -> list[dict]:
    return [
        {
            "sector_label": item.get("sector_label"),
            "track": item.get("track"),
            "score": item.get("score"),
            "confidence": item.get("confidence"),
            "entry_hint": item.get("entry_hint"),
            "evidence": item.get("evidence") or [],
            "penalties": item.get("penalties") or [],
            "change_1d_percent": item.get("change_1d_percent"),
            "change_5d_percent": item.get("change_5d_percent"),
            "today_main_force_net_yi": item.get("today_main_force_net_yi"),
            "cumulative_5d_net_yi": item.get("cumulative_5d_net_yi"),
            "pattern_label": item.get("pattern_label"),
        }
        for item in items[:8]
    ]
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests\test_discovery_payload.py tests\test_discovery_streaming.py -q
```

Expected: all tests pass.

---

### Task 4: Budget Slow Discovery Facts

**Files:**
- Modify: `apps/api/app/services/discovery_facts.py`
- Test: `apps/api/tests/test_discovery_payload.py`

- [ ] **Step 1: Write failing timeout test**

Add to `apps/api/tests/test_discovery_payload.py`:

```python
def test_build_discovery_facts_budget_degrades_slow_signal(monkeypatch):
    import time

    monkeypatch.setattr("app.services.discovery_facts.SIGNAL_BACKTEST_TIMEOUT_SECONDS", 0.01)

    def slow_signal(*_args, **_kwargs):
        time.sleep(0.08)
        return {"has_data": True}

    monkeypatch.setattr("app.services.discovery_facts.build_signal_backtest_context", slow_signal)

    start = time.monotonic()
    facts = build_discovery_facts(
        holdings=[],
        profile=_profile(),
        target_sectors=["半导体"],
        sector_heat=[],
        candidate_pool=[],
        budget_enhancements=True,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.05
    assert facts["signal_backtest"]["has_data"] is False
    assert facts["signal_backtest"]["reason"] == "timeout"
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests\test_discovery_payload.py::test_build_discovery_facts_budget_degrades_slow_signal -q
```

Expected: fail because `budget_enhancements` is not accepted.

- [ ] **Step 3: Implement budget fallback**

In `discovery_facts.py`, add:

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

SIGNAL_BACKTEST_TIMEOUT_SECONDS = 5.0
```

Add helpers:

```python
def _signal_backtest_unavailable(reason: str) -> dict:
    return {
        "enabled": True,
        "has_data": False,
        "reason": reason,
        "message": "板块信号回测未在预算内完成，荐基已按价格与资金流事实继续。",
        "summary_lines": [],
        "sectors": [],
    }


def _budgeted_signal_backtest(target_sectors: list[str]) -> dict:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="discovery-facts-budget")
    future = executor.submit(lambda: build_signal_backtest_context(target_sectors))
    try:
        return future.result(timeout=SIGNAL_BACKTEST_TIMEOUT_SECONDS)
    except FutureTimeoutError:
        future.cancel()
        return _signal_backtest_unavailable("timeout")
    except Exception:
        return _signal_backtest_unavailable("error")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
```

Update `build_discovery_facts` with:

```python
budget_enhancements: bool = False,
```

Use:

```python
signal_backtest = (
    _budgeted_signal_backtest(target_sectors)
    if budget_enhancements
    else build_signal_backtest_context(target_sectors)
)
```

Pass `budget_enhancements=True` from streaming and async pipeline.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests\test_discovery_payload.py::test_build_discovery_facts_budget_degrades_slow_signal -q
```

Expected: pass.

---

### Task 5: Verification and Smoke

**Files:**
- Modify: `docs/PROJECT_CONTEXT.md`

- [ ] **Step 1: Update project context**

Add a dated entry near the top of `docs/PROJECT_CONTEXT.md`:

```markdown
- **荐基双轨候选池（2026-06-29）：** 荐基先用主题 1d/5d 与板块主力资金流合成 `sector_opportunities`，按「顺势机会 momentum」与「蓄势观察 setup」双轨均衡选 6~8 个方向；「回调承接」暂作为 `entry_hint` 而非独立取板块轨道。候选池优先按 `fund_primary_sectors_global` / 用户主关联板块反查基金，叠加家族去重、已持有过滤、类型偏好，再交给 LLM 精选。慢 `signal_backtest` 预算化，超时降级继续，避免卡在 AI 分析前上下文阶段。
```

- [ ] **Step 2: Run focused API tests**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests\test_discovery_sector_opportunity.py tests\test_discovery_candidate_pool_opportunity.py tests\test_discovery_candidate_pool_cache.py tests\test_discovery_payload.py tests\test_discovery_streaming.py tests\test_discovery_stream_endpoint.py -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run smoke discovery**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe scripts\smoke_run_discovery.py --label dual-track-fast --mode fast
```

Expected:
- output includes `skeleton`;
- output reaches `done`;
- `stage gaps` no longer spends roughly 60s+ inside signal backtest before LLM;
- report has non-empty `candidate_pool`.

- [ ] **Step 4: Inspect git diff**

Run:

```powershell
git diff --stat
git diff -- apps/api/app/services/discovery_sector_opportunity.py apps/api/app/services/discovery_candidate_pool.py apps/api/app/services/discovery_facts.py
```

Expected: diff only contains discovery candidate-pool, facts, payload, migration, database, tests, and context docs changes.

