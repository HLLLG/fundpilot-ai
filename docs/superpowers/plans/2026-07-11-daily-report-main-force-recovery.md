# Daily Report Main-Force Evidence Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve real same-day board main-force data when history is missing, require a complete five-trading-day window for the 5-day metric, and make daily reports display honest availability states.

**Architecture:** Merge the cached Eastmoney history series and the already-fetched theme-board same-day snapshot before deciding availability. Carry independent `today_available` and `five_day_available` flags through opportunity scoring, and let one report-scoped opportunity context provide the flow map used by both holdings facts and rotation scoring. The UI formats each metric independently and never appends “亿” to a missing value.

**Tech Stack:** Python 3.12, FastAPI service layer, pytest, React 19, TypeScript, Vitest, Testing Library.

---

## File map

- Modify `apps/api/app/services/sector_fund_flow_context.py`: merge/clean flow points, compute complete 5-day windows, expose availability flags.
- Modify `apps/api/app/services/sector_opportunity_scoring.py`: gate today and 5-day flow independently and preserve status fields.
- Modify `apps/api/app/services/report_sector_opportunity.py`: build one prioritized report flow map and return it to the caller.
- Modify `apps/api/app/services/analysis_facts.py`: consume the report-scoped flow map instead of starting a duplicate holding flow task.
- Modify `apps/api/app/services/analysis_payload.py`: preserve the three status fields in the LLM-safe trimmed payload.
- Modify `apps/web/src/lib/api.ts`: type the new optional flow status fields.
- Modify `apps/web/src/components/SectorOpportunityCard.tsx`: render numeric and missing states separately.
- Create `apps/web/src/components/SectorOpportunityCard.test.tsx`: focused rendering contract.
- Modify the existing backend tests listed in the tasks below.

### Task 1: Merge live and historical flow before deciding availability

**Files:**
- Modify: `apps/api/app/services/sector_fund_flow_context.py:13-305`
- Test: `apps/api/tests/test_sector_fund_flow_context.py`

- [ ] **Step 1: Write failing empty-history, deduplication, and complete-window tests**

Add these helpers and tests to `apps/api/tests/test_sector_fund_flow_context.py`:

```python
import math


def _live_snapshot(code: str, value: float) -> dict:
    return {
        "items": [
            {
                "sector_label": "人工智能",
                "flow_source_code": code,
                "main_force_net_yi": value,
                "flow_tiers": {"large_net_yi": value},
            }
        ]
    }


def test_empty_history_keeps_live_today_but_not_five_day(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK0800", "人工智能"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.get_cached_board_flow_series",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: _live_snapshot("BK0800", -134.84),
    )

    result = build_sector_fund_flow_context(
        "人工智能", sector_return_percent=-2.97, trade_date="2026-07-10"
    )

    assert result["available"] is True
    assert result["today_available"] is True
    assert result["five_day_available"] is False
    assert result["history_point_count"] == 1
    assert result["today_main_force_net_yi"] == -134.84
    assert result["cumulative_5d_net_yi"] is None


def test_four_history_points_plus_live_today_forms_complete_five_day(monkeypatch) -> None:
    history = [
        {"date": "2026-07-06", "main_force_net_yi": 1.0, "flow_tiers": {}},
        {"date": "2026-07-07", "main_force_net_yi": 2.0, "flow_tiers": {}},
        {"date": "2026-07-08", "main_force_net_yi": 3.0, "flow_tiers": {}},
        {"date": "2026-07-09", "main_force_net_yi": 4.0, "flow_tiers": {}},
    ]
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK0800", "人工智能"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.get_cached_board_flow_series",
        lambda *_args, **_kwargs: list(history),
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: _live_snapshot("BK0800", 5.0),
    )

    result = build_sector_fund_flow_context("人工智能", trade_date="2026-07-10")

    assert result["five_day_available"] is True
    assert result["history_point_count"] == 5
    assert result["cumulative_5d_net_yi"] == 15.0


def test_five_day_rejects_duplicate_future_and_non_finite_points(monkeypatch) -> None:
    history = [
        {"date": "2026-07-07", "main_force_net_yi": 1.0},
        {"date": "2026-07-08", "main_force_net_yi": 2.0},
        {"date": "2026-07-08", "main_force_net_yi": 20.0},
        {"date": "2026-07-09", "main_force_net_yi": math.inf},
        {"date": "2026-07-11", "main_force_net_yi": 99.0},
    ]
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK0800", "人工智能"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.get_cached_board_flow_series",
        lambda *_args, **_kwargs: list(history),
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: _live_snapshot("BK0800", 5.0),
    )

    result = build_sector_fund_flow_context("人工智能", trade_date="2026-07-10")

    assert result["history_point_count"] == 3
    assert result["five_day_available"] is False
    assert result["cumulative_5d_net_yi"] is None


def test_live_today_overrides_same_date_history(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.resolve_board_flow_code_for_sector",
        lambda _label: ("BK0800", "人工智能"),
    )
    monkeypatch.setattr(
        "app.services.sector_fund_flow_context.get_cached_board_flow_series",
        lambda *_args, **_kwargs: [
            {"date": "2026-07-10", "main_force_net_yi": -1.0, "flow_tiers": {}}
        ],
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.get_theme_board_snapshot_cache_only",
        lambda: _live_snapshot("BK0800", -134.84),
    )

    result = build_sector_fund_flow_context("人工智能", trade_date="2026-07-10")

    assert result["today_main_force_net_yi"] == -134.84
    assert result["history_point_count"] == 1
```

Replace the old two-point assertion in `test_missing_today_row_is_spliced_from_live_theme_board_snapshot` with four historical dates plus the live date, so the test continues to assert that live data participates in a genuine five-point window.

- [ ] **Step 2: Run the focused tests and confirm the regression**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_sector_fund_flow_context.py -q
```

Expected: the four new tests fail because empty history returns unavailable, duplicate/invalid points are not normalized, and a partial window is currently summed.

- [ ] **Step 3: Implement point normalization and complete-window semantics**

In `sector_fund_flow_context.py`, add:

```python
import math


def _normalize_flow_points(
    points: list[dict[str, Any]], trade_date: str
) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, Any]] = {}
    for point in points:
        day = str(point.get("date") or "")[:10]
        value = point.get("main_force_net_yi")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not day or day > trade_date or not math.isfinite(numeric):
            continue
        by_date[day] = {**point, "date": day, "main_force_net_yi": numeric}
    return [by_date[day] for day in sorted(by_date)]


def _complete_window_sum(
    points: list[dict[str, Any]], days: int
) -> tuple[float | None, int]:
    window = points[-days:]
    if len(window) < days:
        return None, len(points)
    return round(sum(float(point["main_force_net_yi"]) for point in window), 2), len(points)
```

Replace `_ensure_today_point()` so the same-request theme snapshot replaces an existing same-date history point instead of being skipped:

```python
def _ensure_today_point(
    series: list[dict[str, Any]],
    board_code: str,
    trade_date: str,
) -> list[dict[str, Any]]:
    live = _live_today_flow_from_theme_board(board_code)
    if live is None:
        return series
    without_today = [
        point for point in series if str(point.get("date") or "") != trade_date
    ]
    return [*without_today, {"date": trade_date, **live}]
```

Change `build_sector_fund_flow_context()` so `_ensure_today_point()` is called before the empty-series return and the final values follow this exact contract:

```python
    history = _load_flow_series(board_code, target_trade_date)
    merged = _ensure_today_point(history, board_code, target_trade_date)
    series = _normalize_flow_points(merged, target_trade_date)
    if not series:
        return {
            "available": False,
            "today_available": False,
            "five_day_available": False,
            "history_point_count": 0,
            "sector_label": resolved_label or label,
            "board_code": board_code,
            "message": "暂无板块资金流",
        }

    point = _pick_flow_point(series, target_trade_date)
    flow_date = str(point.get("date") or "")
    today_flow = point.get("main_force_net_yi")
    date_aligned = flow_date == target_trade_date
    today_available = date_aligned and today_flow is not None
    cumulative_5d, point_count = _complete_window_sum(series, 5)
    five_day_available = date_aligned and cumulative_5d is not None
    if not five_day_available:
        cumulative_5d = None
```

Preserve the 20-day compatibility calculation with these lines:

```python
    recent_20d = _slice_tail(series, 20)
    cumulative_20d = _sum_main_force(recent_20d)
```

Return `available` and the three new status fields together with the existing result fields:

```python
        "available": today_available or five_day_available,
        "today_available": today_available,
        "five_day_available": five_day_available,
        "history_point_count": point_count,
```

- [ ] **Step 4: Run tests and commit the data contract**

Run the focused test command again. Expected: PASS.

Commit:

```powershell
git add apps/api/app/services/sector_fund_flow_context.py apps/api/tests/test_sector_fund_flow_context.py
git commit -m "fix: preserve complete board flow evidence"
```

### Task 2: Gate today and five-day evidence independently

**Files:**
- Modify: `apps/api/app/services/sector_opportunity_scoring.py:94-330`
- Modify: `apps/api/app/services/analysis_payload.py:260-295`
- Test: `apps/api/tests/test_sector_opportunity_flow_date_alignment.py`
- Test: `apps/api/tests/test_analysis_payload_sector_opportunity_trim.py`

- [ ] **Step 1: Add failing independent-availability tests**

Append to `test_sector_opportunity_flow_date_alignment.py`:

```python
def test_today_and_five_day_availability_are_independent() -> None:
    flow = {
        "available": True,
        "date_aligned": True,
        "today_available": True,
        "five_day_available": False,
        "history_point_count": 1,
        "today_main_force_net_yi": -134.84,
        "cumulative_5d_net_yi": None,
        "pattern_label": "weak_outflow",
    }

    result = describe_sector_opportunity(_heat_row("人工智能"), flow, focus=set())

    assert result["today_main_force_net_yi"] == -134.84
    assert result["cumulative_5d_net_yi"] is None
    assert result["today_available"] is True
    assert result["five_day_available"] is False
    assert result["history_point_count"] == 1
```

In `test_analysis_payload_sector_opportunity_trim.py`, include the three flags in `_facts_with_sector_fund_flow()` and assert they survive fast and deep trimming.

- [ ] **Step 2: Verify tests fail**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_sector_opportunity_flow_date_alignment.py tests/test_analysis_payload_sector_opportunity_trim.py -q
```

Expected: FAIL because the opportunity row and trimmed payload do not yet expose the flags.

- [ ] **Step 3: Implement backward-compatible independent gates**

In `_compute_opportunity_row()` use:

```python
    legacy_available = bool(flow.get("available")) and date_aligned
    today_available = bool(flow.get("today_available", legacy_available)) and date_aligned
    five_day_available = bool(flow.get("five_day_available", legacy_available)) and date_aligned
    today_flow = _num(flow.get("today_main_force_net_yi")) if today_available else None
    flow_5d = _num(flow.get("cumulative_5d_net_yi")) if five_day_available else None
    history_point_count = int(flow.get("history_point_count") or 0)
```

Return the flags with the opportunity row. In the `analysis_payload.py` flow whitelist, add:

```python
"today_available",
"five_day_available",
"history_point_count",
```

- [ ] **Step 4: Run tests and commit**

Expected: both focused files PASS.

```powershell
git add apps/api/app/services/sector_opportunity_scoring.py apps/api/app/services/analysis_payload.py apps/api/tests/test_sector_opportunity_flow_date_alignment.py apps/api/tests/test_analysis_payload_sector_opportunity_trim.py
git commit -m "fix: separate daily and historical flow status"
```

### Task 3: Reuse one prioritized flow map per report

**Files:**
- Modify: `apps/api/app/services/sector_opportunity_scoring.py:94-132`
- Modify: `apps/api/app/services/report_sector_opportunity.py:33-148`
- Modify: `apps/api/app/services/analysis_facts.py:337-419`
- Test: `apps/api/tests/test_report_sector_opportunity.py`
- Test: `apps/api/tests/test_analysis_payload_bundle.py`

- [ ] **Step 1: Write failing priority and reuse tests**

Add to `test_report_sector_opportunity.py`:

```python
def test_report_scoped_flow_map_fetches_each_critical_sector_once(monkeypatch) -> None:
    calls: list[tuple[list[str], str | None]] = []
    heat = [
        _heat_row("人工智能", change_1d=-2.0, change_5d=-3.0, heat_score=80.0),
        _heat_row("银行", change_1d=1.0, change_5d=2.0, heat_score=70.0),
    ]

    def fake_flow_map(_heat, labels, *, trade_date=None, **_kwargs):
        calls.append((list(labels), trade_date))
        return {
            label: {
                "available": True,
                "date_aligned": True,
                "today_available": True,
                "five_day_available": False,
                "history_point_count": 1,
                "today_main_force_net_yi": 1.0,
                "cumulative_5d_net_yi": None,
            }
            for label in labels
        }

    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_flow_map_for_opportunities",
        fake_flow_map,
    )
    monkeypatch.setattr(
        "app.services.report_sector_opportunity.build_sector_divergence_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )

    result = build_holding_sector_opportunity_context(
        [_holding("人工智能")],
        fetch_sector_heat=lambda: heat,
        trade_date="2026-07-10",
    )

    assert len(calls) == 1
    assert calls[0][0][0] == "人工智能"
    assert calls[0][1] == "2026-07-10"
    assert result["sector_flow_by_label"]["人工智能"]["today_main_force_net_yi"] == 1.0
```

Add this test to `test_analysis_payload_bundle.py`:

```python
def test_prepare_analysis_bundle_reuses_opportunity_flow_map(monkeypatch) -> None:
    request = _minimal_request()
    risk = _minimal_risk()
    snapshots = [
        FundSnapshot(
            fund_code="519674",
            fund_name="银河创新成长",
            latest_nav=1.0,
            source="test",
        )
    ]
    shared_flow = {
        "available": True,
        "today_available": True,
        "five_day_available": False,
        "history_point_count": 1,
        "today_main_force_net_yi": -12.5,
        "cumulative_5d_net_yi": None,
    }

    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: ({}, None, None, None),
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_signal_backtest_context",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.resolve_signal_guard_policy",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts._build_sector_intraday_map",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_market_flow_context",
        lambda *_args, **_kwargs: {"available": False},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_market_breadth_signal",
        lambda *_args, **_kwargs: {"available": False},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_holding_sector_opportunity_context",
        lambda *_args, **_kwargs: {
            "available": True,
            "held": {},
            "market_top": [],
            "divergence_backtest": {},
            "sector_flow_by_label": {"半导体": shared_flow},
        },
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.build_sector_fund_flow_map",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("duplicate flow fetch")
        ),
        raising=False,
    )

    bundle = prepare_analysis_bundle(
        request, risk, snapshots, [], budget_enhancements=True
    )

    assert bundle.facts["holdings"][0]["sector_fund_flow"] is shared_flow
```

- [ ] **Step 2: Run and confirm failure**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_report_sector_opportunity.py tests/test_analysis_payload_bundle.py -q
```

Expected: FAIL because no `trade_date` parameter or `sector_flow_by_label` result exists and analysis facts still starts a second flow task.

- [ ] **Step 3: Implement the single report flow source**

Add `trade_date` to `build_sector_flow_map_for_opportunities()` and pass it into `build_sector_fund_flow_context()`.

Change the report context signature to:

```python
def build_holding_sector_opportunity_context(
    holdings: list[Holding],
    *,
    trade_date: str | None = None,
    fetch_sector_heat=None,
) -> dict[str, Any]:
```

Do not return early on a heat-fetch error. Preserve `heat_reason`, use an empty market heat list, and still request held labels. Build labels exactly as:

```python
    held_labels = _unique_labels(
        normalize_sector_label(holding.sector_name) for holding in holdings
    )
    top_by_heat = sorted(
        sector_heat,
        key=lambda row: _num(row.get("heat_score")) or float("-inf"),
        reverse=True,
    )
    top_labels = [
        str(row.get("sector_label") or "").strip()
        for row in top_by_heat[:MARKET_TOP_CANDIDATE_LIMIT]
        if str(row.get("sector_label") or "").strip()
    ]
    flow_labels = _unique_labels([*held_labels, *top_labels])
    flow_by_label = build_sector_flow_map_for_opportunities(
        sector_heat,
        flow_labels,
        trade_date=trade_date,
        total_timeout_seconds=SECTOR_FLOW_BUDGET_SECONDS,
    )
```

Keep flow and divergence work parallel, keep held labels first, and return:

```python
    return {
        "available": bool(heat_by_label),
        "reason": heat_reason,
        "held": held,
        "market_top": market_top,
        "divergence_backtest": divergence_by_label,
        "sector_flow_by_label": flow_by_label,
    }
```

In `analysis_facts.py`, remove `flow_future` and both standalone `build_sector_fund_flow_map()` calls. Pass `effective_trade_date` to the opportunity context and derive:

```python
    sector_flow_map = sector_opportunity.get("sector_flow_by_label") or _sector_flow_timeout_map(
        holdings
    )
```

Use this map for `holdings[].sector_fund_flow`; do not expose the internal map under `sector_rotation`.

Update the three budget tests in `test_analysis_payload_bundle.py` that currently monkeypatch `build_sector_fund_flow_map`. Remove that monkeypatch and make their opportunity stubs return the flow map. For example, replace the slow opportunity stub with:

```python
    def slow_sector_opportunity(*_args, **_kwargs):
        time.sleep(_SLOW_SLEEP_SECONDS)
        return {
            "available": True,
            "held": {},
            "market_top": [],
            "divergence_backtest": {},
            "sector_flow_by_label": {
                "半导体": {"available": True, "reason": "flow"}
            },
        }
```

For the timeout test, the opportunity timeout fallback must produce `_sector_flow_timeout_map(holdings)` when deriving `sector_flow_map`, preserving the existing `available=False, reason="timeout"` assertion.

- [ ] **Step 4: Run tests and commit**

Expected: focused tests PASS and each report uses one flow fetch path.

```powershell
git add apps/api/app/services/sector_opportunity_scoring.py apps/api/app/services/report_sector_opportunity.py apps/api/app/services/analysis_facts.py apps/api/tests/test_report_sector_opportunity.py apps/api/tests/test_analysis_payload_bundle.py
git commit -m "perf: reuse report board flow context"
```

### Task 4: Render honest metric states in React

**Files:**
- Modify: `apps/web/src/lib/api.ts:578-593`
- Modify: `apps/web/src/components/SectorOpportunityCard.tsx:1-86`
- Create: `apps/web/src/components/SectorOpportunityCard.test.tsx`

- [ ] **Step 1: Write the failing rendering contract**

Create `SectorOpportunityCard.test.tsx`:

```tsx
// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { expect, it } from "vitest";

import { SectorOpportunityCard } from "@/components/SectorOpportunityCard";

it("renders real today and five-day main-force values", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "半导体",
        today_available: true,
        five_day_available: true,
        history_point_count: 5,
        today_main_force_net_yi: -248.78,
        cumulative_5d_net_yi: -162.81,
      }}
    />,
  );
  expect(screen.getByText("-248.78 亿")).toBeInTheDocument();
  expect(screen.getByText("-162.81 亿")).toBeInTheDocument();
});

it("labels missing today and historical data without a fake unit", () => {
  render(
    <SectorOpportunityCard
      item={{
        sector_label: "人工智能",
        today_available: false,
        five_day_available: false,
        history_point_count: 0,
        today_main_force_net_yi: null,
        cumulative_5d_net_yi: null,
      }}
    />,
  );
  expect(screen.getByText("今日数据暂缺")).toBeInTheDocument();
  expect(screen.getByText("5日历史暂缺")).toBeInTheDocument();
  expect(screen.queryByText("— 亿")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run and confirm failure**

```powershell
cd apps/web
npm test -- src/components/SectorOpportunityCard.test.tsx
```

Expected: FAIL because the component currently formats both nulls as `— 亿`.

- [ ] **Step 3: Implement typed status formatting**

Add to `SectorOpportunity`:

```ts
today_available?: boolean;
five_day_available?: boolean;
history_point_count?: number;
```

In `SectorOpportunityCard.tsx`, add:

```tsx
function flowMetric(
  value: number | null | undefined,
  available: boolean | undefined,
  missingLabel: string,
) {
  if (available === false || value == null) return missingLabel;
  return `${formatMetric(value)} 亿`;
}
```

Render:

```tsx
<Metric
  label="今日主力"
  value={flowMetric(item.today_main_force_net_yi, item.today_available, "今日数据暂缺")}
/>
<Metric
  label="5日主力"
  value={flowMetric(item.cumulative_5d_net_yi, item.five_day_available, "5日历史暂缺")}
/>
```

- [ ] **Step 4: Run tests, typecheck, and commit**

```powershell
npm test -- src/components/SectorOpportunityCard.test.tsx src/components/ReportPanel.test.tsx src/components/DiscoveryReportPanel.test.tsx
npm run typecheck
npm run lint
git add src/lib/api.ts src/components/SectorOpportunityCard.tsx src/components/SectorOpportunityCard.test.tsx
git commit -m "fix: explain missing board flow metrics"
```

Expected: tests, typecheck, and lint PASS.

### Task 5: Run the subsystem regression suite

**Files:**
- No production files changed in this task.

- [ ] **Step 1: Run all directly related backend tests**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_sector_fund_flow_context.py tests/test_sector_opportunity_flow_date_alignment.py tests/test_report_sector_opportunity.py tests/test_analysis_payload_sector_opportunity_trim.py tests/test_analysis_payload_bundle.py -q
```

Expected: PASS.

- [ ] **Step 2: Run all directly related web checks**

```powershell
cd ..\web
npm test -- src/components/SectorOpportunityCard.test.tsx src/components/ReportPanel.test.tsx src/components/DiscoveryReportPanel.test.tsx
npm run typecheck
npm run lint
```

Expected: PASS with no new warnings.

- [ ] **Step 3: Verify patch hygiene**

```powershell
cd ..\..
git diff --check
git status --short
```

Expected: no whitespace errors; only the files assigned to this plan are changed.
