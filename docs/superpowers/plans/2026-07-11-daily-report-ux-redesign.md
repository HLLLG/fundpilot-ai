# Lingxi Daily Report UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the complete Lingxi daily-report tab into an action-first, beginner-readable single-column experience while preserving expert evidence, report/chat compatibility, and fixing proven report-trust defects.

**Architecture:** Keep the existing `Report` API and SSE/chat flows unchanged. Add pure presentation selectors, split the oversized report component into focused summary/list/card/details/drawer units, let `Dashboard` explicitly drive the generation-control reading state, and repair AkShare diagnostic math at the source with a cache-version bump plus historical-report UI guards.

**Tech Stack:** Next.js 16.2.2, React 19.2.3, TypeScript 5.9.3, Tailwind CSS 4.1.18, Vitest/Testing Library, FastAPI/Pydantic, pytest, pandas, AkShare subprocess integration.

## Global Constraints

- Do not add runtime dependencies.
- Do not change the required fields of the `Report` API, database schema, SSE report stream, Markdown export, or report-chat endpoints.
- Preserve the current `ExtremeActionGate` behavior for “大幅减仓评估” and “清仓评估”.
- Group recommendations only for presentation; never rewrite or reprioritize backend investment actions.
- Treat diagnostic metrics as displayable only when one-year return is finite and in `[-100, 1000]`, and max drawdown is finite and in `[-100, 0]`.
- Keep desktop and mobile information order identical; 390×844 must have no horizontal overflow.
- A typical seven-fund report must be at least 50% shorter in its default collapsed state than the measured production baseline.
- Use the existing Lingxi “静谧蓝海·高级克制” tokens and Chinese font stack; do not introduce a separate visual theme.
- Follow TDD: every behavior change starts with a focused failing test, then minimal implementation, then focused and full verification.

---

## File Structure

### Backend

- Modify `apps/api/app/services/fund_data.py`: convert cumulative return percentages to growth indices before return/drawdown calculations.
- Modify `apps/api/app/services/fund_diagnostics_cache.py`: bump the diagnostic cache namespace from `v1` to `v2`.
- Modify `apps/api/app/services/decision_guard_shared.py`: humanize the proven leaked English enums for newly generated reports.
- Create `apps/api/tests/test_fund_data_return_frame.py`: deterministic return/drawdown parsing coverage.
- Modify `apps/api/tests/test_fund_diagnostics_rank_cache.py`: assert cache-version invalidation.
- Modify `apps/api/tests/test_decision_guard_shared.py`: assert backend humanization.

### Frontend presentation and components

- Create `apps/web/src/lib/reportPresentation.ts`: pure grouping, filtering, primary-reason, next-plan, confidence, and diagnostic-safety selectors.
- Create `apps/web/src/lib/reportPresentation.test.ts`: selector unit tests.
- Modify `apps/web/src/lib/decisionText.ts`: historical-report text fallbacks.
- Create `apps/web/src/lib/decisionText.test.ts`: fallback translation tests.
- Modify `apps/web/src/components/RiskControls.tsx`: full setup vs compact reading state and visible DCA label.
- Create `apps/web/src/components/RiskControls.test.tsx`: setup-state and accessibility coverage.
- Modify `apps/web/src/components/ReportChatPanel.tsx`: replace fixed sidebar sizing with an explicit drawer surface.
- Create `apps/web/src/components/ReportChatDrawer.tsx`: overlay, focus, escape, backdrop, and body-scroll behavior.
- Create `apps/web/src/components/ReportChatDrawer.test.tsx`: accessible drawer interaction tests.
- Create `apps/web/src/components/ReportSummaryHero.tsx`: report conclusion, key metrics, metadata/export disclosure.
- Create `apps/web/src/components/ReportSummaryHero.test.tsx`: summary and export behavior.
- Create `apps/web/src/components/FundRecommendationCard.tsx`: action summary plus two evidence layers and extreme-action gate.
- Create `apps/web/src/components/ReportRecommendationList.tsx`: needs-action/observe groups and empty compatibility state.
- Create `apps/web/src/components/ReportRecommendationList.test.tsx`: grouping, default collapse, news filtering, and evidence disclosure tests.
- Create `apps/web/src/components/ReportDetailsHub.tsx`: compact four-entry hub with lazy content mounting.
- Create `apps/web/src/components/ReportDetailsHub.test.tsx`: hub disclosure and lazy-mount tests.
- Modify `apps/web/src/components/ReportPanel.tsx`: orchestrate the new focused components and preserve stream/legacy parsing/export.
- Modify `apps/web/src/components/ReportPanel.test.tsx`: integration expectations for the redesigned report.
- Modify `apps/web/src/components/Dashboard.tsx`: pass report identity to generation controls and diagnostics into the report hub.
- Modify `apps/web/src/app/globals.css`: remove the report/sidebar grid and add restrained report/drawer responsive styles.
- Delete `apps/web/src/components/DiagnosticsAccordion.tsx` after its only consumer is migrated into `ReportDetailsHub`.

### Documentation

- Modify `docs/PROJECT_CONTEXT.md`: record the action-first report layout, diagnostic fix, new components, and verified test totals.

---

### Task 1: Correct Diagnostic Metrics and New-Report Terminology

**Files:**
- Create: `apps/api/tests/test_fund_data_return_frame.py`
- Modify: `apps/api/app/services/fund_data.py:363-405`
- Modify: `apps/api/app/services/fund_diagnostics_cache.py:10-35`
- Modify: `apps/api/tests/test_fund_diagnostics_rank_cache.py:1-27`
- Modify: `apps/api/app/services/decision_guard_shared.py:35-85`
- Modify: `apps/api/tests/test_decision_guard_shared.py`

**Interfaces:**
- Consumes: AkShare frames whose return column contains cumulative percentage values.
- Produces: `_parse_return_frame(frame) -> dict[str, float]` with `return_1y_percent` and `max_drawdown_1y_percent`; cache key `fund:diagnostics:v2:{fund_code}`; `humanize_evidence_text(text: str) -> str` with the new enum translations.

- [ ] **Step 1: Add failing diagnostic-math tests**

Create `apps/api/tests/test_fund_data_return_frame.py`:

```python
import math

import pandas as pd

from app.services.fund_data import _parse_return_frame


def _frame(values: list[object]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "净值日期": [f"2026-01-{index + 1:02d}" for index in range(len(values))],
            "累计收益率": values,
        }
    )


def test_parse_return_frame_treats_values_as_cumulative_percentages():
    result = _parse_return_frame(_frame([1.0, 21.0]))
    assert result["return_1y_percent"] == 19.8
    assert result["max_drawdown_1y_percent"] == 0.0


def test_parse_return_frame_computes_drawdown_on_growth_index():
    result = _parse_return_frame(_frame([0.0, 20.0, 10.0]))
    assert result["return_1y_percent"] == 10.0
    assert result["max_drawdown_1y_percent"] == -8.33


def test_parse_return_frame_handles_crossing_zero_cumulative_return():
    result = _parse_return_frame(_frame([-10.0, 10.0]))
    assert result["return_1y_percent"] == 22.22
    assert result["max_drawdown_1y_percent"] == 0.0


def test_parse_return_frame_skips_invalid_growth_indices_and_non_finite_values():
    result = _parse_return_frame(_frame([-100.0, math.nan, "bad", 0.0, 10.0]))
    assert result == {
        "return_1y_percent": 10.0,
        "max_drawdown_1y_percent": 0.0,
    }


def test_parse_return_frame_returns_empty_when_fewer_than_two_valid_points():
    assert _parse_return_frame(_frame([-100.0, "bad", 10.0])) == {}
```

- [ ] **Step 2: Update the cache and terminology tests so they fail**

Change the cache assertion in `test_fund_diagnostics_rank_cache.py`:

```python
assert diagnostics_cache_key("519674") == "fund:diagnostics:v2:519674"
```

Append to `test_decision_guard_shared.py`:

```python
def test_humanize_evidence_text_translates_report_enum_leaks():
    text = "机会absent；daily_return数据pending；track=momentum"
    assert humanize_evidence_text(text) == (
        "当前不构成机会；当日涨跌待确认；顺势观察"
    )
```

- [ ] **Step 3: Run the focused tests and confirm red state**

Run:

```powershell
cd apps/api
./.venv/Scripts/python.exe -m pytest tests/test_fund_data_return_frame.py tests/test_fund_diagnostics_rank_cache.py tests/test_decision_guard_shared.py -q
```

Expected: failures show the ratio-based return math, `v1` cache key, and untranslated English tokens.

- [ ] **Step 4: Implement percentage-to-growth-index parsing**

Add `import math` to the module import block, then replace the numeric-series block in `_parse_return_frame` with:

```python
    growth_indices: list[float] = []
    for value in frame[column].tail(260):
        try:
            cumulative_percent = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(cumulative_percent):
            continue
        growth_index = 1.0 + cumulative_percent / 100.0
        if growth_index > 0:
            growth_indices.append(growth_index)
    if len(growth_indices) < 2:
        return {}

    start = growth_indices[0]
    end = growth_indices[-1]
    return_1y = round((end / start - 1.0) * 100.0, 2)
    peak = growth_indices[0]
    max_drawdown = 0.0
    for point in growth_indices:
        peak = max(peak, point)
        drawdown = (point / peak - 1.0) * 100.0
        max_drawdown = min(max_drawdown, drawdown)

    return {
        "return_1y_percent": return_1y,
        "max_drawdown_1y_percent": round(max_drawdown, 2),
    }
```

- [ ] **Step 5: Invalidate bad cached metrics and humanize new reports**

Change in `fund_diagnostics_cache.py`:

```python
_CACHE_VERSION = "v2"
```

Add bounded regex replacements in `decision_guard_shared.py` before generic text replacements:

```python
result = re.sub(
    r"\bopportunity\s+absent\b",
    "当前不构成机会",
    result,
    flags=re.IGNORECASE,
)
result = re.sub(
    r"\bopportunity\s+present\b",
    "当前构成机会",
    result,
    flags=re.IGNORECASE,
)
result = re.sub(
    r"机会\s*absent\b",
    "当前不构成机会",
    result,
    flags=re.IGNORECASE,
)
result = re.sub(
    r"机会\s*present\b",
    "当前构成机会",
    result,
    flags=re.IGNORECASE,
)
result = re.sub(
    r"\bdaily_return(?:_percent)?\s*(?:数据)?\s*(?:is\s+)?pending\b",
    "当日涨跌待确认",
    result,
    flags=re.IGNORECASE,
)
```

Keep `track=momentum` and `track=setup` handled by the existing structured track replacement.

- [ ] **Step 6: Run focused and broader backend tests**

Run the focused command from Step 3, then:

```powershell
./.venv/Scripts/python.exe -m pytest tests/test_report_sector_opportunity.py tests/test_recommendation_guard_evidence.py tests/test_report_export_structured_fields.py -q
```

Expected: all selected tests pass and no return/drawdown value is produced below -100% drawdown.

- [ ] **Step 7: Commit backend correctness**

```powershell
git add apps/api/app/services/fund_data.py apps/api/app/services/fund_diagnostics_cache.py apps/api/app/services/decision_guard_shared.py apps/api/tests/test_fund_data_return_frame.py apps/api/tests/test_fund_diagnostics_rank_cache.py apps/api/tests/test_decision_guard_shared.py
git commit -m "fix: correct daily report diagnostic metrics"
```

---

### Task 2: Add Pure Daily-Report Presentation Selectors

**Files:**
- Create: `apps/web/src/lib/reportPresentation.ts`
- Create: `apps/web/src/lib/reportPresentation.test.ts`
- Modify: `apps/web/src/lib/decisionText.ts`
- Create: `apps/web/src/lib/decisionText.test.ts`

**Interfaces:**
- Consumes: `Report["fund_recommendations"][number]`, report snapshots, and raw evidence strings.
- Produces: `displayFundRecommendations`, `groupFundRecommendations`, `portfolioRecommendationLines`, `meaningfulNewsLines`, `keyReasonLines`, `selectPrimaryReason`, `selectNextTradingPlan`, `confidenceDisplayLabel`, `safeDiagnosticMetrics`, and historical-report fallback translations.

- [ ] **Step 1: Write failing selector tests**

Create `reportPresentation.test.ts` with this fixture and assertions:

```typescript
import { describe, expect, it } from "vitest";
import type { Report } from "@/lib/api";
import {
  confidenceDisplayLabel,
  displayFundRecommendations,
  groupFundRecommendations,
  keyReasonLines,
  meaningfulNewsLines,
  portfolioRecommendationLines,
  safeDiagnosticMetrics,
  selectNextTradingPlan,
  selectPrimaryReason,
} from "@/lib/reportPresentation";

type FundRec = Report["fund_recommendations"][number];

function rec(overrides: Partial<FundRec>): FundRec {
  return {
    fund_code: "000001",
    fund_name: "测试基金",
    action: "观察",
    points: ["保持观察"],
    ...overrides,
  };
}

describe("daily report presentation", () => {
  it("groups actionable recommendations and keeps pause ahead of watch", () => {
    const add = rec({ fund_code: "1", action: "分批加仓" });
    const watch = rec({ fund_code: "2", action: "观察" });
    const pause = rec({ fund_code: "3", action: "暂停追涨" });
    const reduce = rec({ fund_code: "4", action: "减仓评估" });
    expect(groupFundRecommendations([watch, add, pause, reduce])).toEqual({
      needsAction: [add, reduce],
      observing: [pause, watch],
    });
  });

  it("filters empty news placeholders", () => {
    expect(
      meaningfulNewsLines(["暂无明确利好", " 无 ", "真实政策利好", "真实政策利好"]),
    ).toEqual(["真实政策利好"]);
  });

  it("keeps portfolio lines while removing legacy per-fund strings", () => {
    const report = {
      fund_recommendations: [],
      recommendations: ["组合整体观望", "[000001 · 观察] 保持观察"],
    } as Report;
    expect(portfolioRecommendationLines(report)).toEqual(["组合整体观望"]);
  });

  it("parses legacy per-fund recommendation strings", () => {
    const report = {
      fund_recommendations: [],
      recommendations: ["[000001 · 观察] 保持观察", "[000001 · 观察] 等待企稳"],
    } as Report;
    expect(displayFundRecommendations(report)).toEqual([
      {
        fund_code: "000001",
        fund_name: "000001",
        action: "观察",
        points: ["保持观察", "等待企稳"],
      },
    ]);
  });

  it("selects position basis before non-guard points", () => {
    expect(
      selectPrimaryReason(
        rec({
          suggested_position_change_basis: "集中度超过上限",
          points: ["已按风控规则调整", "板块资金偏弱"],
        }),
      ),
    ).toBe("集中度超过上限");
  });

  it("extracts the next-trading-day conditional plan", () => {
    expect(
      selectNextTradingPlan(["资金偏弱", "下交易日：若再跌2%则减仓"]),
    ).toBe("下交易日：若再跌2%则减仓");
  });

  it("keeps only non-duplicated explanatory reasons", () => {
    expect(
      keyReasonLines(
        rec({
          points: ["已按风控规则调整", "资金偏弱", "下交易日：若再跌2%则减仓", "资金偏弱"],
        }),
      ),
    ).toEqual(["资金偏弱"]);
  });

  it("maps confidence into beginner-facing reference labels", () => {
    expect(confidenceDisplayLabel("高")).toBe("参考度：高");
    expect(confidenceDisplayLabel("中")).toBe("参考度：中");
    expect(confidenceDisplayLabel("低")).toBe("参考度：有限");
    expect(confidenceDisplayLabel(undefined)).toBeNull();
  });

  it("hides impossible diagnostic values but preserves normal hints", () => {
    expect(
      safeDiagnosticMetrics({ return_1y_percent: 8220.94, max_drawdown_1y_percent: -160.53 }),
    ).toEqual({ hints: [], invalid: true });
    expect(
      safeDiagnosticMetrics({ return_1y_percent: 12.3, max_drawdown_1y_percent: -18.6 }),
    ).toEqual({ hints: ["近1年 12.3%", "最大回撤 -18.6%"], invalid: false });
  });
});
```

Create `decisionText.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { translateEvidenceText } from "@/lib/decisionText";

describe("translateEvidenceText", () => {
  it("humanizes legacy report enum leaks", () => {
    expect(
      translateEvidenceText("半导体板块机会absent，daily_return数据pending，momentum分位19"),
    ).toBe("半导体板块当前不构成机会，当日涨跌待确认，动量分位19");
  });
});
```

- [ ] **Step 2: Run selector tests and confirm missing-module failures**

```powershell
cd apps/web
npm test -- src/lib/reportPresentation.test.ts src/lib/decisionText.test.ts
```

Expected: FAIL because `reportPresentation.ts` does not exist and the legacy enum text is untranslated.

- [ ] **Step 3: Implement the pure selectors**

Create `reportPresentation.ts`:

```typescript
import type { Report } from "@/lib/api";
import { actionTone } from "@/lib/actionStyles";
import { translateEvidenceText } from "@/lib/decisionText";

export type FundRecommendation = Report["fund_recommendations"][number];
type Snapshot = Report["snapshots"][number];

const EMPTY_NEWS = new Set(["", "无", "暂无", "暂无利好", "暂无利空", "暂无明确利好", "暂无明确利空"]);
const ACTION_TONES = new Set(["add", "reduce", "deep_reduce", "clear_all"]);
const GUARD_NOTE = /已按.*(?:风控|规则).*调整|对照本地规则/;
const NEXT_PLAN = /(?:下一交易日|下交易日|开盘)/;

export function meaningfulNewsLines(values?: string[]): string[] {
  const result: string[] = [];
  for (const raw of values ?? []) {
    const value = raw.trim().replace(/[。；;]+$/, "");
    if (EMPTY_NEWS.has(value) || result.includes(value)) continue;
    result.push(value);
  }
  return result;
}

export function displayFundRecommendations(report: Report): FundRecommendation[] {
  if (report.fund_recommendations.length > 0) return report.fund_recommendations;
  const byCode = new Map<string, FundRecommendation>();
  for (const line of report.recommendations) {
    const match = line.match(/^\[(\d{6})\s*[·｜|]\s*([^\]]+)\]\s*(.*)$/);
    if (!match) continue;
    const [, fundCode, action, rest] = match;
    const point = rest.trim();
    const existing = byCode.get(fundCode);
    if (!existing) {
      byCode.set(fundCode, {
        fund_code: fundCode,
        fund_name: fundCode,
        action: action.trim(),
        points: point ? [point] : [],
      });
    } else if (point && !existing.points.includes(point)) {
      existing.points.push(point);
    }
  }
  return [...byCode.values()];
}

export function portfolioRecommendationLines(report: Report): string[] {
  if (report.fund_recommendations.length > 0) return report.recommendations;
  return report.recommendations.filter((line) => !/^\[\d{6}\s*[·｜|]/.test(line.trim()));
}

export function groupFundRecommendations(items: FundRecommendation[]) {
  const needsAction: FundRecommendation[] = [];
  const pauses: FundRecommendation[] = [];
  const watches: FundRecommendation[] = [];
  for (const item of items) {
    const tone = actionTone(item.action);
    const hasPositionChange =
      item.suggested_position_change_percent != null &&
      item.suggested_position_change_percent !== 0;
    if (ACTION_TONES.has(tone) || hasPositionChange) needsAction.push(item);
    else if (tone === "pause") pauses.push(item);
    else watches.push(item);
  }
  return { needsAction, observing: [...pauses, ...watches] };
}

export function selectPrimaryReason(item: FundRecommendation): string {
  const candidate =
    item.suggested_position_change_basis?.trim() ||
    item.amount_note?.trim() ||
    item.points.find((point) => point.trim() && !GUARD_NOTE.test(point)) ||
    item.points[0] ||
    "暂无需要立即操作的新增信号";
  return translateEvidenceText(candidate);
}

export function selectNextTradingPlan(points: string[]): string | null {
  const match = points.find((point) => NEXT_PLAN.test(point));
  return match ? translateEvidenceText(match) : null;
}

export function keyReasonLines(item: FundRecommendation): string[] {
  const result: string[] = [];
  for (const point of item.points) {
    if (GUARD_NOTE.test(point) || NEXT_PLAN.test(point)) continue;
    const value = translateEvidenceText(point.trim());
    if (value && !result.includes(value)) result.push(value);
    if (result.length === 3) break;
  }
  return result;
}

export function confidenceDisplayLabel(value?: string): string | null {
  if (!value) return null;
  if (value.includes("高")) return "参考度：高";
  if (value.includes("中")) return "参考度：中";
  return "参考度：有限";
}

export function safeDiagnosticMetrics(
  snapshot: Pick<Snapshot, "return_1y_percent" | "max_drawdown_1y_percent">,
): { hints: string[]; invalid: boolean } {
  const hints: string[] = [];
  let invalid = false;
  const yearly = snapshot.return_1y_percent;
  if (yearly != null) {
    if (Number.isFinite(yearly) && yearly >= -100 && yearly <= 1000) hints.push(`近1年 ${yearly}%`);
    else invalid = true;
  }
  const drawdown = snapshot.max_drawdown_1y_percent;
  if (drawdown != null) {
    if (Number.isFinite(drawdown) && drawdown >= -100 && drawdown <= 0) hints.push(`最大回撤 ${drawdown}%`);
    else invalid = true;
  }
  return { hints, invalid };
}
```

- [ ] **Step 4: Add bounded frontend fallback translations**

Add these replacements near the start of `translateEvidenceText`:

```typescript
.replace(/\bopportunity\s+absent\b/gi, "当前不构成机会")
.replace(/\bopportunity\s+present\b/gi, "当前构成机会")
.replace(/机会\s*absent\b/gi, "当前不构成机会")
.replace(/机会\s*present\b/gi, "当前构成机会")
.replace(/\bdaily_return(?:_percent)?\s*(?:数据)?\s*(?:is\s+)?pending\b/gi, "当日涨跌待确认")
.replace(/\bmomentum(?=分位|因子|\b)/gi, "动量")
```

- [ ] **Step 5: Run selector tests, then the current report tests**

```powershell
npm test -- src/lib/reportPresentation.test.ts src/lib/decisionText.test.ts
npm test -- src/components/ReportPanel.test.tsx src/components/DiscoveryReportPanel.test.tsx
```

Expected: all tests pass; discovery translation behavior remains compatible.

- [ ] **Step 6: Commit presentation selectors**

```powershell
git add apps/web/src/lib/reportPresentation.ts apps/web/src/lib/reportPresentation.test.ts apps/web/src/lib/decisionText.ts apps/web/src/lib/decisionText.test.ts
git commit -m "feat: add daily report presentation selectors"
```

---

### Task 3: Add Compact Reading State to Generation Controls

**Files:**
- Create: `apps/web/src/components/RiskControls.test.tsx`
- Modify: `apps/web/src/components/RiskControls.tsx`

**Interfaces:**
- Consumes: new prop `readingModeKey?: string | null`; a report id means compact-by-default, `null` means full setup.
- Produces: an accessible compact summary with “调整设置” and “重新生成”, while retaining all existing full controls when expanded.

- [ ] **Step 1: Write failing component tests**

Start `RiskControls.test.tsx` with:

```typescript
// @vitest-environment jsdom
import type { ComponentProps } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { RiskControls } from "@/components/RiskControls";

vi.mock("@/components/AnalysisModeToggle", () => ({
  AnalysisModeToggle: () => <div data-testid="analysis-mode-toggle" />,
}));
vi.mock("@/components/InvestmentPresetSelector", () => ({
  InvestmentPresetSelector: () => <div data-testid="investment-preset-selector" />,
}));
vi.mock("@/components/RolePromptEditor", () => ({
  RolePromptEditor: () => <div data-testid="role-prompt-editor" />,
}));

afterEach(() => cleanup());

function props(): ComponentProps<typeof RiskControls> {
  return {
    profile: {
      style: "长期持有",
      horizon: "半年至一年",
      max_drawdown_percent: 8,
      concentration_limit_percent: 35,
      expected_investment_amount: 30_000,
      prefer_dca: true,
      avoid_chasing: true,
      decision_style: "conservative",
    },
    analysisMode: "deep",
    rolePrompt: "默认角色",
    isRolePromptCustom: false,
    onAnalysisModeChange: vi.fn(),
    onChange: vi.fn(),
    onRolePromptChange: vi.fn(),
    onRolePromptReset: vi.fn(),
    onAnalyze: vi.fn(),
    isBusy: false,
    ocrWarningCount: 0,
    hasBlockingErrors: false,
  };
}
```

Add these assertions:

```typescript
it("shows full generation controls when there is no completed report", () => {
  render(<RiskControls {...props()} readingModeKey={null} />);
  expect(screen.getByText("AI 角色设定")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "生成今日操作建议" })).toBeInTheDocument();
});

it("collapses to a reading summary when a report exists", () => {
  render(<RiskControls {...props()} readingModeKey="report-1" />);
  expect(screen.getByText("本次生成设置")).toBeInTheDocument();
  expect(screen.queryByText("AI 角色设定")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新生成" })).toBeInTheDocument();
});

it("opens settings and collapses again for a new report id", () => {
  const view = render(<RiskControls {...props()} readingModeKey="report-1" />);
  fireEvent.click(screen.getByRole("button", { name: "调整设置" }));
  expect(screen.getByText("AI 角色设定")).toBeInTheDocument();
  view.rerender(<RiskControls {...props()} readingModeKey="report-2" />);
  expect(screen.queryByText("AI 角色设定")).not.toBeInTheDocument();
});

it("shows a clickable label for the DCA preference", () => {
  render(<RiskControls {...props()} readingModeKey={null} />);
  fireEvent.click(screen.getByRole("button", { name: "高级设置" }));
  expect(screen.getByRole("checkbox", { name: "偏好定投" })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the focused test and confirm red state**

```powershell
npm test -- src/components/RiskControls.test.tsx
```

Expected: FAIL because `readingModeKey` and the visible DCA label do not exist.

- [ ] **Step 3: Implement report-key-driven collapse**

Add this exact property to `RiskControlsProps`, destructure it with the default `null`, and add the state/effect below the existing state:

```typescript
readingModeKey?: string | null;

const [settingsOpen, setSettingsOpen] = useState(readingModeKey == null);

useEffect(() => {
  setSettingsOpen(readingModeKey == null);
}, [readingModeKey]);
```

Import `useEffect`. Immediately before the component's current return, add this complete early return; leave the current full return in place after it:

```tsx
if (readingModeKey && !settingsOpen) {
  return (
    <section className="report-control-card section-card min-w-0 overflow-hidden">
      <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="text-sm font-black text-slate-950">本次生成设置</div>
          <p className="mt-1 text-xs text-slate-500">
            {analysisMode === "deep" ? "深度模式" : "快速模式"} · {profileSummary(profile)}
          </p>
          {ocrWarningCount > 0 ? (
            <p className="mt-1 text-xs font-semibold text-amber-800">
              识别待核对 {ocrWarningCount} 处{hasBlockingErrors ? "，请先处理严重项。" : "。"}
            </p>
          ) : null}
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => setSettingsOpen(true)} className="btn-secondary min-h-11">
            调整设置
          </button>
          <button
            type="button"
            onClick={onAnalyze}
            disabled={isBusy || hasBlockingErrors}
            className="btn-primary min-h-11"
          >
            {isBusy ? "正在生成..." : hasBlockingErrors ? "请先处理严重项" : "重新生成"}
          </button>
        </div>
      </div>
    </section>
  );
}
```

Keep OCR blocking/warning semantics identical in both branches.

- [ ] **Step 4: Restore the DCA label**

Change the currently blank label body to:

```tsx
<label className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50/50 px-3 py-2.5 text-sm font-semibold text-slate-700">
  偏好定投
  <input
    type="checkbox"
    checked={profile.prefer_dca}
    onChange={(event) => onChange({ ...profile, prefer_dca: event.target.checked })}
    className="h-4 w-4 accent-blue-600"
  />
</label>
```

- [ ] **Step 5: Run focused tests and static checks**

```powershell
npm test -- src/components/RiskControls.test.tsx
npm run typecheck
```

Expected: component tests and TypeScript pass.

- [ ] **Step 6: Commit compact generation controls**

```powershell
git add apps/web/src/components/RiskControls.tsx apps/web/src/components/RiskControls.test.tsx
git commit -m "feat: compact report generation settings"
```

---

### Task 4: Replace the Permanent Chat Column with an Accessible Drawer

**Files:**
- Modify: `apps/web/src/components/ReportChatPanel.tsx`
- Create: `apps/web/src/components/ReportChatDrawer.tsx`
- Create: `apps/web/src/components/ReportChatDrawer.test.tsx`

**Interfaces:**
- Consumes: `reportId`, `reportTitle`.
- Produces: `ReportChatDrawer` with its own trigger/open state and `ReportChatPanel` surface `variant="drawer"`.

- [ ] **Step 1: Write failing drawer behavior tests**

Start the test file with:

```typescript
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { ReportChatDrawer } from "@/components/ReportChatDrawer";

vi.mock("@/components/ReportChatPanel", () => ({
  ReportChatPanel: () => <input aria-label="聊天输入" />,
}));

afterEach(() => {
  cleanup();
  document.body.style.overflow = "";
});
```

Then test:

```typescript
it("opens an accessible report chat dialog", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  expect(screen.getByRole("dialog", { name: "追问这份日报" })).toBeInTheDocument();
  expect(document.body.style.overflow).toBe("hidden");
});

it("closes on Escape and restores focus to the trigger", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  const trigger = screen.getByRole("button", { name: "追问这份日报" });
  fireEvent.click(trigger);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe("");
});

it("closes from the explicit close control", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  fireEvent.click(screen.getByRole("button", { name: "关闭追问助手" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("closes when the backdrop is pressed", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  fireEvent.mouseDown(screen.getByTestId("report-chat-backdrop"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("wraps keyboard focus inside the open drawer", () => {
  render(<ReportChatDrawer reportId="report-1" reportTitle="日报" />);
  fireEvent.click(screen.getByRole("button", { name: "追问这份日报" }));
  const close = screen.getByRole("button", { name: "关闭追问助手" });
  expect(close).toHaveFocus();
  fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
  expect(screen.getByRole("textbox", { name: "聊天输入" })).toHaveFocus();
});
```

- [ ] **Step 2: Run the drawer test and confirm missing-component failure**

```powershell
npm test -- src/components/ReportChatDrawer.test.tsx
```

Expected: FAIL because `ReportChatDrawer` does not exist.

- [ ] **Step 3: Refactor the chat panel surface**

Replace `compact`/`inline` with:

```typescript
type ReportChatPanelProps = {
  reportId: string;
  reportTitle?: string;
  variant?: "default" | "drawer";
};
```

Replace the current height ternary with:

```typescript
const surfaceClass = variant === "drawer"
  ? "h-full min-h-0 rounded-none border-0 bg-slate-50/90"
  : "h-[min(72vh,720px)] min-h-[520px] rounded-2xl border border-[var(--line)] bg-slate-50/90";
```

Use it on the root:

```tsx
<div className={`flex flex-col ${surfaceClass}`} data-testid="report-chat-panel">
```

Keep export visible for both variants and do not change chat fetch/stream functions.

- [ ] **Step 4: Implement the drawer shell and focus loop**

Create `ReportChatDrawer.tsx` with internal `open`, `triggerRef`, `dialogRef`, and `closeRef`. On open:

```typescript
useEffect(() => {
  if (!open) return;
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";
  closeRef.current?.focus();
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") setOpen(false);
    if (event.key !== "Tab" || !dialogRef.current) return;
    const focusable = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  document.addEventListener("keydown", onKeyDown);
  return () => {
    document.removeEventListener("keydown", onKeyDown);
    document.body.style.overflow = previousOverflow;
    triggerRef.current?.focus();
  };
}, [open]);
```

Render the trigger, fixed backdrop, and a right-aligned `sm:w-[420px]` desktop/full-screen mobile panel with:

```tsx
return (
  <>
    <button
      ref={triggerRef}
      type="button"
      onClick={() => setOpen(true)}
      className="fixed bottom-[calc(5rem+env(safe-area-inset-bottom))] right-4 z-30 min-h-11 rounded-full bg-[var(--brand-strong)] px-4 text-sm font-black text-white shadow-lg lg:bottom-6"
    >
      追问这份日报
    </button>
    {open ? (
      <div
        data-testid="report-chat-backdrop"
        className="report-chat-backdrop fixed inset-0 z-50 flex justify-end bg-slate-950/35"
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) setOpen(false);
        }}
      >
        <section
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="report-chat-drawer-title"
          className="report-chat-drawer flex h-[100dvh] w-full flex-col bg-white shadow-2xl sm:w-[420px]"
        >
          <header className="flex min-h-14 items-center justify-between border-b border-slate-200 px-4">
            <h2 id="report-chat-drawer-title" className="text-base font-black text-slate-950">追问这份日报</h2>
            <button ref={closeRef} type="button" onClick={() => setOpen(false)} aria-label="关闭追问助手" className="min-h-11 min-w-11">×</button>
          </header>
          <div className="min-h-0 flex-1">
            <ReportChatPanel reportId={reportId} reportTitle={reportTitle} variant="drawer" />
          </div>
        </section>
      </div>
    ) : null}
  </>
);
```

- [ ] **Step 5: Run drawer and report-chat tests plus typecheck**

```powershell
npm test -- src/components/ReportChatDrawer.test.tsx
npm run typecheck
```

Expected: drawer behavior and TypeScript pass; chat APIs are unchanged.

- [ ] **Step 6: Commit the drawer**

```powershell
git add apps/web/src/components/ReportChatPanel.tsx apps/web/src/components/ReportChatDrawer.tsx apps/web/src/components/ReportChatDrawer.test.tsx
git commit -m "feat: move report chat into accessible drawer"
```

---

### Task 5: Build the Report Summary Hero

**Files:**
- Create: `apps/web/src/components/ReportSummaryHero.tsx`
- Create: `apps/web/src/components/ReportSummaryHero.test.tsx`

**Interfaces:**
- Consumes: `report: Report`, `needsActionCount: number`, `isExporting: boolean`, `onExport: () => void`.
- Produces: beginner-first report conclusion with three KPI tiles and export/metadata disclosure.

- [ ] **Step 1: Write failing summary tests**

Create a complete local fixture and assert:

```typescript
// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import type { Report } from "@/lib/api";
import { ReportSummaryHero } from "@/components/ReportSummaryHero";

function sampleReport(): Report {
  return {
    id: "report-1",
    created_at: "2026-07-11T10:00:00Z",
    title: "持仓盘点日报",
    summary: "今日观望为主。",
    risk: {
      level: "medium",
      suggested_action: "watch",
      weighted_return_percent: 3.71,
      alerts: [],
    },
    holdings: [],
    snapshots: [],
    market_context: [],
    market_news: [],
    topic_briefs: [],
    fund_recommendations: [],
    recommendations: ["组合整体保持观察"],
    caveats: [],
    provider: "deepseek-v4-pro",
  };
}

const onExport = vi.fn();

render(
  <ReportSummaryHero
    report={sampleReport()}
    needsActionCount={1}
    isExporting={false}
    onExport={onExport}
  />,
);
expect(screen.getByRole("heading", { name: "持仓盘点日报" })).toBeInTheDocument();
expect(screen.getByText("今日观望为主。")).toBeInTheDocument();
expect(screen.getByText("3.71%")).toBeInTheDocument();
expect(screen.getByText("中等")).toBeInTheDocument();
expect(screen.getByText("1 只")).toBeInTheDocument();
fireEvent.click(screen.getByRole("button", { name: "导出 Markdown" }));
expect(onExport).toHaveBeenCalledOnce();
expect(screen.queryByText("deepseek-v4-pro")).not.toBeInTheDocument();
fireEvent.click(screen.getByRole("button", { name: "报告信息" }));
expect(screen.getByText("deepseek-v4-pro")).toBeInTheDocument();
expect(screen.queryByText("组合整体保持观察")).not.toBeInTheDocument();
fireEvent.click(screen.getByRole("button", { name: "组合说明" }));
expect(screen.getByText("组合整体保持观察")).toBeInTheDocument();
```

- [ ] **Step 2: Run test and confirm missing-component failure**

```powershell
npm test -- src/components/ReportSummaryHero.test.tsx
```

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the summary hero**

Define these exact maps in the new component, call `portfolioRecommendationLines(report)`, render provider only inside a “报告信息” disclosure, and render the returned portfolio lines only inside a separate “组合说明” disclosure:

```typescript
const riskTone = { low: "green", medium: "amber", high: "red" } as const;
const riskLabel = { low: "较低", medium: "中等", high: "较高" } as const;
const actionLabel = {
  watch: "观察",
  pause_add: "暂停加仓",
  staggered_add: "分批加仓",
  risk_review: "减仓/风控复核",
} as const;
```

The main structure is:

```tsx
const [metadataOpen, setMetadataOpen] = useState(false);
const [portfolioOpen, setPortfolioOpen] = useState(false);
const portfolioLines = portfolioRecommendationLines(report);

<section className="report-panel p-4 sm:p-5">
  <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
    <div>
      <div className="mb-2 flex flex-wrap gap-2">
        <StatusPill tone={riskTone[report.risk.level]}>风险 {riskLabel[report.risk.level]}</StatusPill>
        <StatusPill tone="dark">{actionLabel[report.risk.suggested_action]}</StatusPill>
      </div>
      <h2 className="font-display text-2xl font-extrabold text-slate-950">{report.title}</h2>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">{report.summary}</p>
    </div>
    <div className="grid grid-cols-3 gap-2">
      <Metric label="组合收益" value={`${report.risk.weighted_return_percent}%`} emphasis />
      <Metric label="组合风险" value={riskLabel[report.risk.level]} />
      <Metric label="需要处理" value={`${needsActionCount} 只`} />
    </div>
  </div>
  <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-3">
    <div className="flex gap-3">
      {portfolioLines.length ? (
        <button type="button" aria-expanded={portfolioOpen} onClick={() => setPortfolioOpen((value) => !value)}>组合说明</button>
      ) : null}
      <button type="button" aria-expanded={metadataOpen} onClick={() => setMetadataOpen((value) => !value)}>报告信息</button>
    </div>
    <button type="button" onClick={onExport} disabled={isExporting}>导出 Markdown</button>
  </div>
  {portfolioOpen ? <ul>{portfolioLines.map((line) => <li key={line}>{line}</li>)}</ul> : null}
  {metadataOpen ? <div>{report.provider} · {report.created_at}</div> : null}
</section>
```

Keep value labels visible; do not rely on color alone.

- [ ] **Step 4: Run the summary tests and typecheck**

```powershell
npm test -- src/components/ReportSummaryHero.test.tsx
npm run typecheck
```

Expected: PASS.

- [ ] **Step 5: Commit the summary hero**

```powershell
git add apps/web/src/components/ReportSummaryHero.tsx apps/web/src/components/ReportSummaryHero.test.tsx
git commit -m "feat: add action-first report summary"
```

---

### Task 6: Build Layered Recommendation Cards and Groups

**Files:**
- Create: `apps/web/src/components/FundRecommendationCard.tsx`
- Create: `apps/web/src/components/ReportRecommendationList.tsx`
- Create: `apps/web/src/components/ReportRecommendationList.test.tsx`
- Modify: `apps/web/src/components/DecisionEvidenceGrid.tsx`

**Interfaces:**
- Consumes: recommendation, snapshot, holding evidence, sector opportunity, and divergence backtest already available in `ReportPanel`.
- Produces: grouped list with action summaries, “为什么这样建议”, “专业依据”, and a safe diagnostic warning.

- [ ] **Step 1: Write failing list and disclosure tests**

Create these complete fixtures at the top of `ReportRecommendationList.test.tsx`:

```typescript
// @vitest-environment jsdom
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import type { Report } from "@/lib/api";
import { ReportRecommendationList } from "@/components/ReportRecommendationList";

type FundRec = Report["fund_recommendations"][number];

function buildReport(
  recommendations: FundRec[],
  snapshots: Report["snapshots"] = [],
): Report {
  return {
    id: "report-1",
    created_at: "2026-07-11T10:00:00Z",
    title: "测试日报",
    summary: "测试摘要",
    risk: {
      level: "medium",
      suggested_action: "watch",
      weighted_return_percent: 3.71,
      alerts: [],
    },
    holdings: [],
    snapshots,
    market_context: [],
    market_news: [],
    topic_briefs: [],
    fund_recommendations: recommendations,
    recommendations: [],
    caveats: [],
    provider: "test",
  };
}

function recommendation(overrides: Partial<FundRec>): FundRec {
  return {
    fund_code: "000001",
    fund_name: "测试基金",
    action: "观察",
    points: ["保持观察"],
    ...overrides,
  };
}

function reportWithReduceAndWatch(): Report {
  return buildReport([
    recommendation({
      fund_code: "000001",
      fund_name: "测试减仓基金",
      action: "减仓评估",
      amount_note: "建议降至约 10,500 元",
      points: ["集中度超过上限", "下交易日：若再跌2%则减仓"],
      risks: ["集中度风险"],
    }),
    recommendation({
      fund_code: "000002",
      fund_name: "测试观察基金",
      action: "观察",
      points: ["冲高回落，不追涨"],
    }),
  ]);
}

function reportWithPlaceholderNews(): Report {
  return buildReport([
    recommendation({
      action: "减仓评估",
      news_bullish: ["暂无明确利好", "真实政策利好"],
      news_bearish: ["暂无明确利空"],
      points: ["集中度超过上限"],
    }),
  ]);
}

function reportWithInvalidDiagnostics(): Report {
  return buildReport(
    [recommendation({ action: "减仓评估", points: ["集中度超过上限"] })],
    [
      {
        fund_code: "000001",
        fund_name: "测试基金",
        source: "test",
        return_1y_percent: 8220.94,
        max_drawdown_1y_percent: -160.53,
      },
    ],
  );
}

function reportWithExtremeAction(): Report {
  return buildReport([
    recommendation({ action: "清仓评估", points: ["多重强风险共振"] }),
  ]);
}
```

Then cover these exact behaviors:

```typescript
it("renders actionable cards before collapsed observation rows", () => {
  render(<ReportRecommendationList report={reportWithReduceAndWatch()} />);
  expect(screen.getByRole("heading", { name: "需要处理" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "继续观察" })).toBeInTheDocument();
  expect(screen.getByText("建议降至约 10,500 元")).toBeInTheDocument();
  expect(screen.queryByText("完整量化证据")).not.toBeInTheDocument();
});

it("keeps observation detail collapsed until the row is opened", () => {
  render(<ReportRecommendationList report={reportWithReduceAndWatch()} />);
  fireEvent.click(screen.getByRole("button", { name: /展开 测试观察基金/ }));
  expect(screen.getByRole("button", { name: "为什么这样建议" })).toBeInTheDocument();
});

it("filters placeholder news and reveals meaningful news in the why layer", () => {
  render(<ReportRecommendationList report={reportWithPlaceholderNews()} />);
  fireEvent.click(screen.getByRole("button", { name: "为什么这样建议" }));
  expect(screen.queryByText("暂无明确利空")).not.toBeInTheDocument();
  expect(screen.getByText("真实政策利好")).toBeInTheDocument();
});

it("hides impossible diagnostics and explains the omission in professional evidence", () => {
  render(<ReportRecommendationList report={reportWithInvalidDiagnostics()} />);
  fireEvent.click(screen.getByRole("button", { name: "专业依据" }));
  expect(screen.queryByText("8220.94%")).not.toBeInTheDocument();
  expect(screen.getByText("指标数据异常，已隐藏")).toBeInTheDocument();
});

it("keeps extreme actions behind the existing confirmation gate", () => {
  render(<ReportRecommendationList report={reportWithExtremeAction()} />);
  expect(screen.getByTestId("extreme-action-gate")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "为什么这样建议" })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run the focused tests and confirm missing components**

```powershell
npm test -- src/components/ReportRecommendationList.test.tsx
```

Expected: FAIL because the new components do not exist.

- [ ] **Step 3: Move the existing card primitives without changing semantics**

Move `PositionChangeBadge`, `ExtremeActionGate`, `FundDiagnosticHint`, `navHintForFund`, and the evidence/sector lookups from `ReportPanel.tsx` into `FundRecommendationCard.tsx`. Update `FundDiagnosticHint` to accept the already matched snapshot, show type/management fee plus `safeDiagnosticMetrics(snapshot).hints`, and never render raw unsafe return/drawdown values. Import `meaningfulNewsLines`, `keyReasonLines`, `selectPrimaryReason`, `selectNextTradingPlan`, `confidenceDisplayLabel`, and `safeDiagnosticMetrics`.

Use local booleans `summaryOpen`, `whyOpen`, and `professionalOpen`; initialize `summaryOpen` from `defaultExpanded`. Give every disclosure a deterministic id based on `fund_code` and wire `aria-expanded`/`aria-controls`.

Use this public component contract:

```typescript
type FundRecommendationCardProps = {
  item: Report["fund_recommendations"][number];
  report: Report;
  defaultExpanded: boolean;
};
```

At the start of `FundRecommendationCard`, initialize:

```typescript
const [summaryOpen, setSummaryOpen] = useState(defaultExpanded);
const [whyOpen, setWhyOpen] = useState(false);
const [professionalOpen, setProfessionalOpen] = useState(false);
const snapshot = report.snapshots.find((entry) => entry.fund_code === item.fund_code);
const holdingFacts = holdingFactsRow(item.fund_code, report);
const evidence = holdingFacts?.evidence ?? null;
const sectorOpportunity = holdingFacts?.sector_opportunity ?? null;
const divergenceBacktest = holdingFacts?.flow_divergence_backtest ?? null;
```

Close the function after rendering the card body from Step 4, and wrap that body with `ExtremeActionGate` when `isExtremeAction(item.action)` is true.

- [ ] **Step 4: Implement the action summary and two evidence layers**

Before rendering, derive:

```typescript
const primaryReason = selectPrimaryReason(item);
const nextPlan = selectNextTradingPlan(item.points);
const bullish = meaningfulNewsLines(item.news_bullish);
const bearish = meaningfulNewsLines(item.news_bearish);
const reasons = keyReasonLines(item);
const diagnostic = safeDiagnosticMetrics(snapshot ?? {});
const referenceLabel = confidenceDisplayLabel(item.confidence);
```

Define these local helpers:

```tsx
function Disclosure({
  id,
  title,
  open,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={id}
        className="flex min-h-11 w-full items-center justify-between text-left text-sm font-black text-slate-800"
      >
        {title}<ChevronDown size={16} className={open ? "rotate-180" : ""} />
      </button>
      {open ? <div id={id} className="pt-3">{children}</div> : null}
    </div>
  );
}

function NewsBlock({ title, tone, items }: { title: string; tone: "positive" | "negative"; items: string[] }) {
  const classes = tone === "positive" ? "bg-emerald-50 text-emerald-900" : "bg-rose-50 text-rose-900";
  return (
    <div className={`mt-3 rounded-xl px-3 py-2 ${classes}`}>
      <div className="text-xs font-black">{title}</div>
      <ul className="mt-1 space-y-1 text-xs leading-5">
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  );
}
```

The card body must use this concrete structure:

```tsx
<div className="rounded-2xl border border-slate-200 bg-white">
  <button
    type="button"
    onClick={() => setSummaryOpen((value) => !value)}
    aria-expanded={summaryOpen}
    aria-controls={`${item.fund_code}-summary`}
    aria-label={`${summaryOpen ? "收起" : "展开"} ${item.fund_name}`}
    className="flex min-h-11 w-full flex-col gap-2 px-4 py-3 text-left"
  >
    <span className="flex w-full flex-wrap items-center gap-2">
      <strong className="text-sm text-slate-950">{item.fund_name}</strong>
      <span className="text-xs text-slate-400">{item.fund_code}</span>
      {referenceLabel ? <span className="text-xs text-slate-500">{referenceLabel}</span> : null}
      <span className={`ml-auto rounded-full border px-2 py-0.5 text-xs font-bold ${actionBadgeClass(item.action)}`}>
        {item.action}
      </span>
    </span>
    <span className="text-xs leading-5 text-slate-600">{primaryReason}</span>
  </button>
  {summaryOpen ? (
    <div id={`${item.fund_code}-summary`} className="border-t border-slate-100 px-4 pb-4">
      {item.suggested_position_change_percent != null ? (
        <PositionChangeBadge
          percent={item.suggested_position_change_percent}
          basis={item.suggested_position_change_basis}
        />
      ) : item.amount_note ? (
        <p className="mt-3 rounded-xl bg-blue-50 px-3 py-2 text-sm font-bold text-blue-800">{item.amount_note}</p>
      ) : null}
      {nextPlan ? <p className="mt-3 text-sm leading-6 text-amber-900">{nextPlan}</p> : null}
      {item.risks?.[0] ? <p className="mt-3 text-xs leading-5 text-rose-700">主要风险：{translateEvidenceText(item.risks[0])}</p> : null}
      <Disclosure
        id={`${item.fund_code}-why`}
        title="为什么这样建议"
        open={whyOpen}
        onToggle={() => setWhyOpen((value) => !value)}
      >
        <ul className="space-y-2 text-sm leading-6 text-slate-700">
          {reasons.map((point) => <li key={point}>{point}</li>)}
        </ul>
        {bullish.length ? <NewsBlock title="有效利好" tone="positive" items={bullish} /> : null}
        {bearish.length ? <NewsBlock title="有效利空 / 风险" tone="negative" items={bearish} /> : null}
        {item.risks && item.risks.length > 1 ? (
          <ul className="mt-3 space-y-1 text-xs text-rose-700">
            {item.risks.slice(1).map((risk) => <li key={risk}>{translateEvidenceText(risk)}</li>)}
          </ul>
        ) : null}
      </Disclosure>
      <Disclosure
        id={`${item.fund_code}-professional`}
        title="专业依据"
        open={professionalOpen}
        onToggle={() => setProfessionalOpen((value) => !value)}
      >
        {navHint ? <p className="text-xs text-slate-500">{navHint}</p> : null}
        {snapshot ? <FundDiagnosticHint snapshot={snapshot} /> : null}
        {diagnostic.hints.length ? <p className="mt-2 text-xs text-slate-600">{diagnostic.hints.join(" · ")}</p> : null}
        {diagnostic.invalid ? <p className="mt-2 text-xs text-amber-800">指标数据异常，已隐藏</p> : null}
        {sectorOpportunity ? <SectorOpportunityCard item={sectorOpportunity} divergenceBacktest={divergenceBacktest} /> : null}
        {evidence ? <p className="mt-3 text-xs leading-5 text-slate-600">完整量化证据：{evidence.summary}</p> : null}
        {item.decision_path ? <p className="mt-3 text-sm leading-6 text-blue-950">{translateEvidenceText(item.decision_path)}</p> : null}
        <DecisionEvidenceGrid
          className="mt-3"
          sectorEvidence={item.sector_evidence}
          fundEvidence={item.fund_evidence}
          validationNotes={item.validation_notes}
        />
      </Disclosure>
    </div>
  ) : null}
</div>
```

Actionable cards receive `defaultExpanded`; observation cards do not. The card background stays white with a tone-colored left border rather than tinting the entire evidence area.

- [ ] **Step 5: Implement the grouped list**

`ReportRecommendationList` imports `displayFundRecommendations` and `groupFundRecommendations` from `reportPresentation.ts`. Give it this public contract:

```typescript
type ReportRecommendationListProps = {
  report: Report;
  recommendations?: Report["fund_recommendations"];
};
```

Render headings with counts, actionable cards first, then observation cards. If both groups are empty, render an honest “这份历史日报没有可解析的逐基金建议” state.

```tsx
const items = recommendations ?? displayFundRecommendations(report);
const { needsAction, observing } = groupFundRecommendations(items);
if (!needsAction.length && !observing.length) {
  return <p className="report-panel p-5 text-sm text-slate-600">这份历史日报没有可解析的逐基金建议</p>;
}
return (
  <section className="report-panel p-4 sm:p-5">
    {needsAction.length ? (
      <div>
        <h3 className="text-base font-black text-slate-950">需要处理</h3>
        <p className="mt-1 text-xs text-slate-500">{needsAction.length} 只基金存在明确仓位动作</p>
        <div className="mt-3 space-y-3">
          {needsAction.map((item) => <FundRecommendationCard key={item.fund_code} item={item} report={report} defaultExpanded />)}
        </div>
      </div>
    ) : null}
    {observing.length ? (
      <div className={needsAction.length ? "mt-6" : ""}>
        <h3 className="text-base font-black text-slate-950">继续观察</h3>
        <p className="mt-1 text-xs text-slate-500">{observing.length} 只基金暂无立即交易动作</p>
        <div className="mt-3 space-y-2">
          {observing.map((item) => <FundRecommendationCard key={item.fund_code} item={item} report={report} defaultExpanded={false} />)}
        </div>
      </div>
    ) : null}
  </section>
);
```

- [ ] **Step 6: Make the evidence grid disclosure-friendly**

Keep `DecisionEvidenceGrid` content unchanged, but remove the always-on top margin assumption so the parent professional panel controls spacing. Add optional `className?: string` and merge it on the root.

```typescript
type DecisionEvidenceGridProps = {
  sectorEvidence?: string[];
  fundEvidence?: string[];
  validationNotes?: string[];
  className?: string;
};
```

Replace the current root opening tag exactly:

```tsx
<div className={`grid gap-2 md:grid-cols-3 ${className ?? ""}`}>
```

Keep the current `groups.map` children and closing tag unchanged.

- [ ] **Step 7: Run list, legacy panel, and discovery tests**

```powershell
npm test -- src/components/ReportRecommendationList.test.tsx src/components/ReportPanel.test.tsx src/components/DiscoveryReportPanel.test.tsx
npm run typecheck
```

Expected: all pass; the shared discovery evidence grid remains unchanged.

- [ ] **Step 8: Commit layered recommendations**

```powershell
git add apps/web/src/components/FundRecommendationCard.tsx apps/web/src/components/ReportRecommendationList.tsx apps/web/src/components/ReportRecommendationList.test.tsx apps/web/src/components/DecisionEvidenceGrid.tsx apps/web/src/components/ReportPanel.tsx
git commit -m "feat: layer daily report recommendation evidence"
```

---

### Task 7: Build the More-Details Hub and Integrate the Full Report

**Files:**
- Create: `apps/web/src/components/ReportDetailsHub.tsx`
- Create: `apps/web/src/components/ReportDetailsHub.test.tsx`
- Modify: `apps/web/src/components/ReportPanel.tsx`
- Modify: `apps/web/src/components/ReportPanel.test.tsx`
- Modify: `apps/web/src/components/Dashboard.tsx:1320-1370`
- Modify: `apps/web/src/app/globals.css:878-915`
- Delete: `apps/web/src/components/DiagnosticsAccordion.tsx`

**Interfaces:**
- Consumes: report-specific detail content plus `diagnostics?: () => React.ReactNode` from `Dashboard`.
- Produces: final single-column `ReportPanel` and four-entry lazy details hub.

- [ ] **Step 1: Write failing hub tests**

Create this fixture in `ReportDetailsHub.test.tsx` before the test cases:

```typescript
// @vitest-environment jsdom
import type { ComponentProps } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import type { Report } from "@/lib/api";
import { ReportDetailsHub } from "@/components/ReportDetailsHub";

vi.mock("@/components/ReportNewsBriefPanel", () => ({
  ReportNewsBriefPanel: () => <div data-testid="news-panel" />,
}));
vi.mock("@/components/RebalanceSimulationPanel", () => ({
  RebalanceSimulationPanel: () => <div data-testid="rebalance-panel" />,
}));
vi.mock("@/components/ReportOutcomesPanel", () => ({
  ReportOutcomesPanel: () => <div data-testid="outcomes-panel" />,
}));

function sampleReport(): Report {
  return {
    id: "report-1",
    created_at: "2026-07-11T10:00:00Z",
    title: "测试日报",
    summary: "测试摘要",
    risk: { level: "medium", suggested_action: "watch", weighted_return_percent: 3.71, alerts: [] },
    holdings: [],
    snapshots: [],
    market_context: [],
    market_news: [],
    topic_briefs: [
      { topic: "人工智能", summary: "主题摘要", points: [], news_count: 1, provider: "test" },
    ],
    fund_recommendations: [],
    recommendations: [],
    caveats: [],
    provider: "test",
    analysis_facts: {
      sector_rotation: {
        available: true,
        market_top: [{ sector_label: "医药", confidence: "中", score: 60 }],
      },
    },
  };
}

function props(): ComponentProps<typeof ReportDetailsHub> {
  return {
    report: sampleReport(),
    diagnostics: () => <div data-testid="diagnostics-content">诊断内容</div>,
  };
}
```

Then add the test cases:

```typescript
it("shows four compact entries without mounting tool content", () => {
  render(<ReportDetailsHub {...props()} />);
  expect(screen.getByRole("button", { name: "主题要闻摘要" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "板块轮动参考" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "调仓示意模拟" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "建议复盘与投研诊断" })).toBeInTheDocument();
  expect(screen.queryByTestId("diagnostics-content")).not.toBeInTheDocument();
});

it("mounts only the selected detail panel", () => {
  render(<ReportDetailsHub {...props()} />);
  fireEvent.click(screen.getByRole("button", { name: "建议复盘与投研诊断" }));
  expect(screen.getByTestId("diagnostics-content")).toBeInTheDocument();
  expect(screen.queryByTestId("news-content")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Add failing full-report integration expectations**

Update `ReportPanel.test.tsx` to assert:

```typescript
expect(screen.getByRole("heading", { name: "需要处理" })).toBeInTheDocument();
expect(screen.getByRole("heading", { name: "继续观察" })).toBeInTheDocument();
expect(screen.getByRole("button", { name: "追问这份日报" })).toBeInTheDocument();
expect(screen.queryByTestId("report-chat-panel")).not.toBeInTheDocument();
expect(screen.queryByText("逐基金操作建议与依据；宽屏时右侧可追问")).not.toBeInTheDocument();
```

- [ ] **Step 3: Run hub and integration tests and confirm red state**

```powershell
npm test -- src/components/ReportDetailsHub.test.tsx src/components/ReportPanel.test.tsx
```

Expected: FAIL because the hub is missing and the old permanent chat/grid still renders.

- [ ] **Step 4: Implement the four-entry hub**

Use `openTool: "news" | "rotation" | "rebalance" | "review" | null`. Each button gets `aria-expanded`, `aria-controls`, at least `min-h-11`, an icon, title, and one-line hint. Render only the active panel. The review panel mounts both `ReportOutcomesPanel` and the passed `diagnostics` node.

Use this public contract and tool definition:

```typescript
type ReportTool = "news" | "rotation" | "rebalance" | "review";

type ReportDetailsHubProps = {
  report: Report;
  diagnostics?: () => React.ReactNode;
};

const TOOLS: Array<{ id: ReportTool; title: string; hint: string }> = [
  { id: "news", title: "主题要闻摘要", hint: "查看有效市场信息" },
  { id: "rotation", title: "板块轮动参考", hint: "查看未持有的强势方向" },
  { id: "rebalance", title: "调仓示意模拟", hint: "预览仓位变化，不执行交易" },
  { id: "review", title: "建议复盘与投研诊断", hint: "核对历史结果和辅助信号" },
];
```

Render the entry grid and selected content with:

```tsx
const rotation = sectorRotationFacts(report);
const availableTools = TOOLS.filter((tool) => {
  if (tool.id === "news") return Boolean(report.topic_briefs?.length || report.market_news.length);
  if (tool.id === "rotation") return Boolean(rotation?.market_top.length);
  return true;
});

<section className="report-panel p-4 sm:p-5">
  <h3 className="text-base font-black text-slate-950">更多内容与工具</h3>
  <div className="mt-3 grid gap-2 sm:grid-cols-2">
    {availableTools.map((tool) => (
      <button
        key={tool.id}
        type="button"
        onClick={() => setOpenTool((value) => value === tool.id ? null : tool.id)}
        aria-expanded={openTool === tool.id}
        aria-controls={`report-tool-${tool.id}`}
        className="flex min-h-11 items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-3 text-left"
      >
        <span><strong className="block text-sm text-slate-900">{tool.title}</strong><span className="text-xs text-slate-500">{tool.hint}</span></span>
        <ChevronDown size={16} className={openTool === tool.id ? "rotate-180" : ""} />
      </button>
    ))}
  </div>
  {openTool === "news" ? (
    <div id="report-tool-news" data-testid="news-content" className="mt-4">
      <ReportNewsBriefPanel briefs={report.topic_briefs ?? []} marketNews={report.market_news} />
    </div>
  ) : null}
  {openTool === "rotation" ? (
    <div id="report-tool-rotation" className="mt-4 grid gap-2 sm:grid-cols-2">
      {rotation?.market_top.map((item) => <SectorOpportunityCard key={item.sector_label} item={item} />)}
    </div>
  ) : null}
  {openTool === "rebalance" ? (
    <div id="report-tool-rebalance" className="mt-4"><RebalanceSimulationPanel reportId={report.id} embedded /></div>
  ) : null}
  {openTool === "review" ? (
    <div id="report-tool-review" className="mt-4 space-y-4">
      <ReportOutcomesPanel reportId={report.id} embedded />
      {diagnostics ? diagnostics() : null}
    </div>
  ) : null}
</section>
```

- [ ] **Step 5: Reduce ReportPanel to orchestration**

Keep stream and empty states. For a completed report:

```tsx
const fundRecommendations = displayFundRecommendations(report);
const groups = groupFundRecommendations(fundRecommendations);

return (
  <section className="report-shell min-w-0 space-y-4 animate-fade-up" data-testid="report-ready">
    <ReportSummaryHero
      report={report}
      needsActionCount={groups.needsAction.length}
      isExporting={isExporting}
      onExport={() => void handleExportMarkdown()}
    />
    <ReportRecommendationList report={report} recommendations={fundRecommendations} />
    <ReportDetailsHub report={report} diagnostics={diagnostics} />
    <ReportChatDrawer reportId={report.id} reportTitle={report.title} />
  </section>
);
```

Add `diagnostics?: () => React.ReactNode` to `ReportPanelProps`. Do not change `ReportSkeleton`.

- [ ] **Step 6: Wire Dashboard and compact controls**

Insert this exact property into the existing `RiskControls` element:

```tsx
readingModeKey={report?.id ?? null}
```

Move the five existing diagnostic children into a `diagnostics` fragment passed to `ReportPanel`. Remove the standalone `DiagnosticsAccordion` import/render. The diagnostics fragment must only mount when the review tool is open; pass it as a factory callback if React element creation triggers child effects before mounting:

```tsx
diagnostics={() => (
  <div className="grid gap-4" data-testid="diagnostics-content">
    <MarketBreadthGauge compact />
    <ShadowEscalationDigestCard />
    <NewsPreviewPanel holdings={displayableHoldings(holdings)} profile={profile} />
    <RecommendationAccuracyPanel />
    <SectorSignalBacktestPanel
      sectorLabels={[
        ...new Set(
          displayableHoldings(holdings)
            .map((item) => item.sector_name?.trim())
            .filter((name): name is string => Boolean(name)),
        ),
      ]}
    />
  </div>
)}
```

- [ ] **Step 7: Replace the old report grid CSS**

Delete `.report-decision-grid` and `.report-chat-sticky`. Keep `.report-panel`, and add only focused classes that cannot be expressed clearly in Tailwind:

```css
.report-shell {
  width: 100%;
}

.report-chat-backdrop {
  animation: report-drawer-fade 160ms ease-out both;
}

.report-chat-drawer {
  animation: report-drawer-in 220ms cubic-bezier(0.22, 0.61, 0.36, 1) both;
}

@media (prefers-reduced-motion: reduce) {
  .report-chat-backdrop,
  .report-chat-drawer {
    animation: none;
  }
}
```

Define `report-drawer-fade` and `report-drawer-in` directly above these rules.

- [ ] **Step 8: Run all redesigned component tests and static checks**

```powershell
npm test -- src/components/RiskControls.test.tsx src/components/ReportChatDrawer.test.tsx src/components/ReportSummaryHero.test.tsx src/components/ReportRecommendationList.test.tsx src/components/ReportDetailsHub.test.tsx src/components/ReportPanel.test.tsx
npm run typecheck
npm run lint
```

Expected: all pass with zero lint warnings.

- [ ] **Step 9: Commit the integrated report page**

```powershell
git add apps/web/src/components/ReportDetailsHub.tsx apps/web/src/components/ReportDetailsHub.test.tsx apps/web/src/components/ReportPanel.tsx apps/web/src/components/ReportPanel.test.tsx apps/web/src/components/Dashboard.tsx apps/web/src/app/globals.css apps/web/src/components/DiagnosticsAccordion.tsx
git commit -m "feat: redesign daily report reading flow"
```

---

### Task 8: Full Verification, Real-Browser QA, and Project Documentation

**Files:**
- Modify: `docs/PROJECT_CONTEXT.md`
- Modify only if verification exposes a defect: files from Tasks 1–7, always with a new failing regression test first.

**Interfaces:**
- Consumes: all completed tasks.
- Produces: verified production build, regression-safe backend, responsive browser evidence, and updated project context.

- [ ] **Step 1: Run the complete frontend gate**

```powershell
cd apps/web
npm test
npm run typecheck
npm run lint
npm run build
```

Expected: all Vitest suites pass, TypeScript exits 0, ESLint has zero warnings, and Next.js production build succeeds.

- [ ] **Step 2: Run the complete backend gate**

```powershell
cd ../api
./.venv/Scripts/python.exe -m pytest tests -q -n auto --dist loadscope
```

Expected: all backend tests pass. If the environment prevents the full run, record the exact blocker and run every directly affected test from Task 1 plus `tests/test_api.py`.

- [ ] **Step 3: Run the existing API E2E smoke**

With the local API/web environment configured as documented:

```powershell
cd ../web
npm run test:e2e
```

Expected: the offline analysis/report persistence smoke passes. Do not weaken the existing test to accommodate the redesign.

- [ ] **Step 4: Verify the real page in Chrome at desktop size**

Use the Chrome control skill on the signed-in Lingxi page. Generate or open a seven-fund report and verify:

- generation settings are compact after a completed report;
- summary plus first actionable card appears within the first viewport after the controls;
- report body uses full width with no permanent chat column;
- only action summaries are visible by default;
- “为什么这样建议” and “专业依据” reveal the correct layers;
- placeholder news and invalid 8220.94% / -160.53% values do not render;
- chat drawer opens, streams or loads history, closes, and preserves scroll position;
- no new console errors or warnings.

- [ ] **Step 5: Verify responsive breakpoints**

Using the browser viewport capability, test at 768×1024 and 390×844. At each size collect bounding boxes/read-only layout values and verify:

```text
document.documentElement.scrollWidth === document.documentElement.clientWidth
```

At 390×844 also verify the chat trigger and full-screen drawer do not overlap the bottom navigation, all primary targets are at least 44px high, and closing the drawer returns to the prior reading position. Reset the viewport override afterward.

- [ ] **Step 6: Measure the default-height reduction**

On the same seven-fund report, read `document.documentElement.scrollHeight` before expanding any report card. Compare with the recorded 9154px mobile and 6330px desktop baselines. Expected: at least 50% reduction at the corresponding viewport, or a documented explanation and targeted follow-up fix before completion.

- [ ] **Step 7: Update project context with actual verified totals**

Add a dated update entry to `docs/PROJECT_CONTEXT.md` describing:

- action-first single-column report;
- three information layers;
- compact post-report controls;
- on-demand chat drawer;
- compact details hub;
- cumulative-return math fix and diagnostic cache `v2`;
- internal-text and placeholder-news cleanup;
- actual frontend/backend test totals and Chrome viewport results from Steps 1–6.

Update the component directory summary to include the new focused report files and remove `DiagnosticsAccordion` if deleted.

- [ ] **Step 8: Run final diff and documentation checks**

```powershell
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; only intended source, tests, and documentation are changed.

- [ ] **Step 9: Commit verification and documentation fixes**

```powershell
git add docs/PROJECT_CONTEXT.md
git add apps/web apps/api
git commit -m "docs: record daily report UX verification"
```

If Steps 1–6 required no source fixes, this commit contains documentation only. Never create an empty commit.

---

## Completion Checklist

- [ ] Every task commit is focused and reviewable.
- [ ] All spec requirements map to Tasks 1–8.
- [ ] No new dependency or API migration was introduced.
- [ ] Full automated gates pass or exact environment blockers are documented.
- [ ] Chrome desktop, tablet, and mobile verification passes.
- [ ] The user receives the real page for final acceptance only after all autonomous verification is complete.
