# Daily Report Factor IC Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Distinguish missing, stale, and usable Factor IC evidence, propagate that state into each generated report, and prove the complete IC-to-report chain against an isolated local SQLite database.

**Architecture:** Load the latest IC snapshot once into a cached context containing `state`, status metadata, and usable factor rows. Missing or stale snapshots remain visible as status but contribute no factor reliability. The report snapshot stores that state, the prompt and professional evidence use honest language, and a successful internal publication clears both IC and one-hour factor-facts caches. Full IC generation and publishing are verified only against loopback and an explicitly isolated SQLite database.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLite, pytest, React 19, TypeScript, Vitest, DeepSeek API, Eastmoney/AkShare network data.

---

## File map

- Modify `apps/api/app/services/factor_ic_snapshot.py`: load a three-state IC context without duplicate storage reads.
- Modify `apps/api/app/services/factor_confidence.py`: cache context, exclude missing/stale factors, preserve status-specific bases.
- Modify `apps/api/app/services/portfolio_snapshot.py`: persist `factor_scores.ic_status` and expose cache clearing.
- Modify `apps/api/app/main.py`: clear both caches after IC publication.
- Modify `apps/api/app/services/analysis_prompt.py`: prohibit describing unavailable IC as weak evidence.
- Modify `apps/api/app/services/analysis_facts.py`: carry the same instruction inside immutable facts.
- Modify `apps/api/app/services/recommendation_guard.py`: distinguish absent IC coverage from genuinely low composite evidence.
- Modify `apps/web/src/lib/api.ts`: type the report IC evidence state.
- Modify `apps/web/src/components/FactorIcStatusBadge.tsx`: clearer unavailable wording.
- Modify `apps/web/src/components/FundRecommendationCard.tsx`: show missing/stale IC in the collapsed professional layer.
- Modify `docs/deploy/lighthouse-cicd.md`: document diagnosis and explicitly separate local proof from authorized production repair.
- Add or modify the focused tests named below.

### Task 1: Model missing, stale, and usable IC contexts

**Files:**
- Modify: `apps/api/app/services/factor_ic_snapshot.py:275-360`
- Modify: `apps/api/app/services/factor_confidence.py:1-86`
- Test: `apps/api/tests/test_factor_ic_snapshot.py`
- Test: `apps/api/tests/test_factor_confidence.py`

- [ ] **Step 1: Write failing three-state context tests**

Add to `test_factor_ic_snapshot.py`:

```python
from app.services.factor_ic_snapshot import load_factor_ic_context


def test_ic_context_marks_missing_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.factor_ic_snapshot.read_latest_database_snapshot",
        lambda _factory=None: None,
    )
    context = load_factor_ic_context(
        local_path=tmp_path / "missing.json",
        stale_after_days=30,
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )
    assert context["state"] == "unavailable"
    assert context["status"]["available"] is False
    assert context["summary"] is None


def test_ic_context_marks_expired_stale(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.factor_ic_snapshot.read_latest_database_snapshot",
        lambda _factory=None: None,
    )
    path = tmp_path / "summary.json"
    payload = valid_payload("2026-05-01T08:00:00+00:00")["summary"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    context = load_factor_ic_context(
        local_path=path,
        stale_after_days=30,
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert context["state"] == "stale"
    assert context["status"]["stale"] is True
    assert context["summary"] == payload


def test_ic_context_marks_fresh_available(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.factor_ic_snapshot.read_latest_database_snapshot",
        lambda _factory=None: None,
    )
    path = tmp_path / "summary.json"
    payload = valid_payload("2026-07-11T08:00:00+00:00")["summary"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    context = load_factor_ic_context(
        local_path=path,
        stale_after_days=30,
        now=datetime(2026, 7, 11, 9, tzinfo=timezone.utc),
    )

    assert context["state"] == "available"
    assert context["status"]["stale"] is False
    assert context["summary"] == payload
```

Add to `test_factor_confidence.py`:

```python
def test_stale_ic_context_excludes_factor_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        fc,
        "load_factor_ic_context",
        lambda **_kwargs: {
            "state": "stale",
            "status": {"available": True, "stale": True, "run_date": "2026-05-01"},
            "summary": {
                "factors": [
                    {"factor": "momentum", "mean_ic": 0.08, "significant": True}
                ]
            },
        },
    )
    fc.clear_ic_summary_cache()

    context = fc.load_ic_context()

    assert context["state"] == "stale"
    assert context["factors"] == {}
    assert fc.factor_reliability(
        context["factors"], missing_basis="IC 回测已过期，暂不参与"
    )["momentum"]["basis"] == "IC 回测已过期，暂不参与"
```

Replace `test_load_ic_summary_cache_expires_after_five_minutes` with:

```python
def test_load_ic_summary_cache_expires_after_five_minutes(monkeypatch) -> None:
    responses = [
        {
            "state": "available",
            "status": {"available": True, "stale": False},
            "summary": {
                "factors": [{"factor": "momentum", "mean_ic": 0.01}]
            },
        },
        {
            "state": "available",
            "status": {"available": True, "stale": False},
            "summary": {
                "factors": [{"factor": "momentum", "mean_ic": 0.02}]
            },
        },
    ]
    times = iter([0.0, 100.0, 301.0])
    monkeypatch.setattr(fc.time, "time", lambda: next(times))
    monkeypatch.setattr(
        fc,
        "load_factor_ic_context",
        lambda **_kwargs: responses.pop(0),
    )
    fc.clear_ic_summary_cache()

    first = fc.load_ic_summary()
    cached = fc.load_ic_summary()
    refreshed = fc.load_ic_summary()

    assert first["momentum"]["mean_ic"] == 0.01
    assert cached is first
    assert refreshed["momentum"]["mean_ic"] == 0.02
```

- [ ] **Step 2: Run and confirm failure**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_factor_ic_snapshot.py tests/test_factor_confidence.py -q
```

Expected: FAIL because `load_factor_ic_context`, `load_ic_context`, and `missing_basis` do not exist.

- [ ] **Step 3: Implement one-read context and stale exclusion**

In `factor_ic_snapshot.py`, define:

```python
FactorIcEvidenceState = Literal["unavailable", "stale", "available"]


def load_factor_ic_context(
    *,
    stale_after_days: int | None = None,
    now: datetime | None = None,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> dict[str, Any]:
    threshold = (
        stale_after_days
        if stale_after_days is not None
        else get_settings().factor_ic_stale_after_days
    )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    raw, source, metadata = load_factor_ic_summary(
        local_path=local_path,
        connection_factory=connection_factory,
    )
    status = _build_factor_ic_status_from_loaded(
        raw,
        source=source,
        metadata=metadata,
        threshold=threshold,
        current=current,
    )
    if not status["available"]:
        state: FactorIcEvidenceState = "unavailable"
    elif status["stale"]:
        state = "stale"
    else:
        state = "available"
    return {"state": state, "status": status, "summary": raw}
```

Add the following helper, moving the current `build_factor_ic_status()` validation and metadata projection into it without changing returned keys:

```python
def _build_factor_ic_status_from_loaded(
    raw: dict[str, Any] | None,
    *,
    source: str,
    metadata: dict[str, Any],
    threshold: int,
    current: datetime,
) -> dict[str, Any]:
    if not raw or not raw.get("run_date") or raw.get("available") is False:
        return _unavailable_status(threshold)
    params = raw.get("params")
    if params is not None and not isinstance(params, dict):
        return _unavailable_status(threshold)
    factors = raw.get("factors")
    if factors is not None and not isinstance(factors, list):
        return _unavailable_status(threshold)
    generated_at = raw.get("generated_at") or f"{raw['run_date']}T00:00:00+00:00"
    try:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return _unavailable_status(threshold)
    generated_utc = generated.astimezone(timezone.utc)
    age_days = max(0, (current.date() - generated_utc.date()).days)
    factor_rows = factors or []
    factor_periods = {
        str(row.get("factor")): row.get("n_periods")
        for row in factor_rows
        if isinstance(row, dict) and row.get("factor")
    }
    source_commit = str(metadata.get("source_commit") or "")[:7] or None
    return {
        "available": True,
        "run_date": str(raw["run_date"]),
        "generated_at": generated.isoformat(),
        "published_at": metadata.get("published_at"),
        "age_days": age_days,
        "stale": age_days >= threshold,
        "stale_after_days": threshold,
        "source": source,
        "target_universe_size": (params or {}).get("universe_size"),
        "universe_size": raw.get("universe_size"),
        "universe_mode": (params or {}).get("universe_mode"),
        "rebalance_count": raw.get("rebalance_count"),
        "factor_periods": factor_periods,
        "source_commit": source_commit,
    }
```

Replace `build_factor_ic_status()` with:

```python
def build_factor_ic_status(
    *,
    stale_after_days: int | None = None,
    now: datetime | None = None,
    local_path: Path | None = None,
    connection_factory: Callable | None = None,
) -> dict[str, Any]:
    return load_factor_ic_context(
        stale_after_days=stale_after_days,
        now=now,
        local_path=local_path,
        connection_factory=connection_factory,
    )["status"]
```

Import `get_settings` inside `load_factor_ic_context()` so the current module import graph remains acyclic.

In `factor_confidence.py`, cache a full context:

```python
_SUMMARY_CACHE: dict[str, tuple[float, dict]] = {}


def _factor_rows(summary: dict | None) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for stats in (summary or {}).get("factors") or []:
        if isinstance(stats, dict) and stats.get("factor"):
            rows[str(stats["factor"])] = stats
    return rows


def load_ic_context() -> dict:
    now = time.time()
    cached = _SUMMARY_CACHE.get("default")
    if cached and now - cached[0] < SUMMARY_TTL_SECONDS:
        return cached[1]
    loaded = load_factor_ic_context(local_path=Path(SUMMARY_PATH))
    state = str(loaded["state"])
    context = {
        "state": state,
        "status": loaded["status"],
        "factors": _factor_rows(loaded["summary"]) if state == "available" else {},
    }
    _SUMMARY_CACHE["default"] = (now, context)
    return context


def load_ic_summary() -> dict[str, dict]:
    return load_ic_context()["factors"]
```

Extend `factor_confidence()` and `factor_reliability()` with `missing_basis: str = "无回测数据"`; use that string only for missing mapped factors, while the size factor retains “规模因子未回测，仅供参考”.

- [ ] **Step 4: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_factor_ic_snapshot.py tests/test_factor_confidence.py -q
git add app/services/factor_ic_snapshot.py app/services/factor_confidence.py tests/test_factor_ic_snapshot.py tests/test_factor_confidence.py
git commit -m "fix: classify factor IC evidence state"
```

Expected: PASS.

### Task 2: Snapshot IC state into reports and clear both caches after publish

**Files:**
- Modify: `apps/api/app/services/portfolio_snapshot.py:315-383`
- Modify: `apps/api/app/main.py:141-255`
- Create: `apps/api/tests/test_portfolio_snapshot_factor_ic_state.py`
- Modify: `apps/api/tests/test_factor_ic_publish_endpoint.py`

- [ ] **Step 1: Write failing propagation and cache-clear tests**

Create `test_portfolio_snapshot_factor_ic_state.py`:

```python
from app.models import Holding
from app.services import portfolio_snapshot as snapshot


def setup_function() -> None:
    snapshot._FACTOR_FACTS_CACHE.clear()


def _holding() -> Holding:
    return Holding(
        fund_code="000001",
        fund_name="测试基金",
        holding_amount=1000,
        sector_name="半导体",
    )


def _factor_payload(*_args, **_kwargs) -> dict:
    return {"available": True, "universe_size": 300, "funds": []}


def test_factor_scores_snapshot_preserves_unavailable_ic_state(monkeypatch) -> None:
    monkeypatch.setattr(snapshot, "build_factor_scores_payload", _factor_payload)
    monkeypatch.setattr(
        "app.services.factor_confidence.load_ic_context",
        lambda: {
            "state": "unavailable",
            "status": {"available": False, "source": "unavailable"},
            "factors": {},
        },
    )

    result = snapshot.build_factor_scores_for_facts([_holding()])

    assert result["ic_status"]["state"] == "unavailable"
    assert result["factor_reliability"]["momentum"]["basis"] == "IC 回测未接入"


def test_factor_scores_snapshot_preserves_stale_ic_state(monkeypatch) -> None:
    monkeypatch.setattr(snapshot, "build_factor_scores_payload", _factor_payload)
    monkeypatch.setattr(
        "app.services.factor_confidence.load_ic_context",
        lambda: {
            "state": "stale",
            "status": {"available": True, "stale": True, "run_date": "2026-05-01"},
            "factors": {},
        },
    )

    result = snapshot.build_factor_scores_for_facts([_holding()])

    assert result["ic_status"]["state"] == "stale"
    assert result["factor_reliability"]["momentum"]["basis"] == "IC 回测已过期，暂不参与"
```

Add this fresh-state test:

```python
def test_factor_scores_snapshot_uses_fresh_ic(monkeypatch) -> None:
    monkeypatch.setattr(snapshot, "build_factor_scores_payload", _factor_payload)
    monkeypatch.setattr(
        "app.services.factor_confidence.load_ic_context",
        lambda: {
            "state": "available",
            "status": {
                "available": True,
                "stale": False,
                "run_date": "2026-07-11",
                "source": "database",
            },
            "factors": {
                "momentum": {"mean_ic": 0.04, "significant": True}
            },
        },
    )

    result = snapshot.build_factor_scores_for_facts([_holding()])

    assert result["ic_status"]["state"] == "available"
    assert result["factor_reliability"]["momentum"]["level"] == "高"
    assert "IC +0.040" in result["factor_reliability"]["momentum"]["basis"]
```

Add to `test_factor_ic_publish_endpoint.py`:

```python
def test_publish_clears_both_ic_caches(monkeypatch, tmp_path) -> None:
    _configure_publish(monkeypatch, tmp_path)
    cleared = {"ic": 0, "facts": 0}
    monkeypatch.setattr(
        "app.main.clear_ic_summary_cache",
        lambda: cleared.__setitem__("ic", cleared["ic"] + 1),
    )
    monkeypatch.setattr(
        "app.main.clear_factor_facts_cache",
        lambda: cleared.__setitem__("facts", cleared["facts"] + 1),
        raising=False,
    )

    response = TestClient(app).post(
        PUBLISH_PATH,
        headers={"X-Factor-IC-Publish-Token": TOKEN},
        json=valid_payload(),
    )

    assert response.status_code == 200
    assert cleared == {"ic": 1, "facts": 1}
```

- [ ] **Step 2: Run and confirm failure**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_portfolio_snapshot_factor_ic_state.py tests/test_factor_ic_publish_endpoint.py -q
```

Expected: FAIL because report factor scores do not contain `ic_status` and publication only clears one cache.

- [ ] **Step 3: Implement snapshot propagation and two-level invalidation**

Change `_compact_factor_scores()` to accept `ic_status` and return it:

```python
def _compact_factor_scores(payload: dict, reliability: dict, ic_status: dict) -> dict:
    return {
        "available": bool(payload.get("available")),
        "universe_size": payload.get("universe_size", 0),
        "ic_status": ic_status,
        "factor_reliability": reliability,
        "holdings": holdings,
    }
```

Add:

```python
def clear_factor_facts_cache() -> None:
    _FACTOR_FACTS_CACHE.clear()
```

In `build_factor_scores_for_facts()` load one context, preserving the existing `ic_factors` injection contract, then map its state to the exact missing basis:

```python
    context = (
        {
            "state": "available" if ic_factors else "unavailable",
            "status": {
                "available": bool(ic_factors),
                "stale": False,
                "source": "injected",
            },
            "factors": ic_factors or {},
        }
        if ic_factors is not None
        else load_ic_context()
    )
    state = context["state"]
    missing_basis = {
        "unavailable": "IC 回测未接入",
        "stale": "IC 回测已过期，暂不参与",
        "available": "无回测数据",
    }[state]
    reliability = factor_reliability(
        context["factors"], missing_basis=missing_basis
    )
    ic_status = {**context["status"], "state": state}
    compact = _compact_factor_scores(payload, reliability, ic_status)
```

In `main.py`, import `clear_factor_facts_cache` and call it immediately after `clear_ic_summary_cache()` following a successful publish.

- [ ] **Step 4: Run tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_portfolio_snapshot_factor_ic_state.py tests/test_factor_ic_publish_endpoint.py tests/test_portfolio_snapshot_factor_concurrency.py -q
git add app/services/portfolio_snapshot.py app/main.py tests/test_portfolio_snapshot_factor_ic_state.py tests/test_factor_ic_publish_endpoint.py
git commit -m "fix: refresh and snapshot factor IC context"
```

Expected: PASS.

### Task 3: Use honest IC language in prompts, guards, and the report UI

**Files:**
- Modify: `apps/api/app/services/analysis_prompt.py:50-66`
- Modify: `apps/api/app/services/analysis_facts.py:502-539`
- Modify: `apps/api/app/services/recommendation_guard.py:365-389`
- Create: `apps/api/tests/test_analysis_prompt_ic_status.py`
- Test: `apps/api/tests/test_recommendation_guard_evidence.py`
- Modify: `apps/web/src/lib/api.ts:434-459`
- Modify: `apps/web/src/components/FactorIcStatusBadge.tsx:43-78`
- Modify: `apps/web/src/components/FactorIcStatusBadge.test.tsx`
- Modify: `apps/web/src/components/FundRecommendationCard.tsx:19-343`
- Modify: `apps/web/src/components/ReportRecommendationList.test.tsx`

- [ ] **Step 1: Write failing backend language tests**

Create `apps/api/tests/test_analysis_prompt_ic_status.py`:

```python
from app.services.analysis_prompt import DEFAULT_ROLE_PROMPT, IC_EVIDENCE_INSTRUCTION


def test_default_prompt_distinguishes_missing_ic_from_weak_evidence() -> None:
    assert IC_EVIDENCE_INSTRUCTION in DEFAULT_ROLE_PROMPT
    assert "ic_status.state" in DEFAULT_ROLE_PROMPT
    assert "不得称为量化背书弱" in DEFAULT_ROLE_PROMPT
    assert "IC 未参与" in DEFAULT_ROLE_PROMPT
```

Add to `test_recommendation_guard_evidence.py`:

```python
from app.services.recommendation_guard import _weak_evidence_reasons
```

```python
def test_missing_factor_component_is_not_described_as_weak_ic() -> None:
    reasons = _weak_evidence_reasons(
        None,
        {
            "composite": {"level": "低", "score": 1},
            "components": [
                {"source": "risk", "level": "低", "basis": "组合风险样本有限"}
            ],
        },
    )
    assert "IC 回测未覆盖，现有量化证据置信偏低" in reasons
    assert "量化证据背书弱" not in reasons
```

- [ ] **Step 2: Write failing frontend missing/stale/fresh tests**

Change the unavailable badge expectation to `IC 回测数据未接入`.

In `ReportRecommendationList.test.tsx`, extend `buildReport()` with this exact diff:

```diff
 function buildReport(
   recommendations: FundRec[],
   snapshots: Report["snapshots"] = [],
+  analysisFacts?: Record<string, unknown>,
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
+    analysis_facts: analysisFacts,
   };
 }
```

Add the three explicit tests:

```tsx
function reportWithIcState(
  state: "unavailable" | "stale" | "available",
): Report {
  return buildReport(
    [recommendation({ action: "减仓评估", points: ["集中度超过上限"] })],
    [],
    {
      factor_scores: {
        ic_status: {
          state,
          available: state !== "unavailable",
          stale: state === "stale",
          run_date: state === "stale" ? "2026-05-01" : "2026-07-11",
          source: state === "unavailable" ? "unavailable" : "database",
        },
      },
    },
  );
}

it("explains that missing IC did not participate", () => {
  render(<ReportRecommendationList report={reportWithIcState("unavailable")} />);
  fireEvent.click(screen.getByRole("button", { name: "专业依据" }));
  expect(screen.getByText("量化回测未接入")).toBeInTheDocument();
  expect(
    screen.getByText("当前建议主要依据持仓风险、行情与新闻；IC 不参与本次结论。"),
  ).toBeInTheDocument();
});

it("marks stale IC as excluded and shows its date", () => {
  render(<ReportRecommendationList report={reportWithIcState("stale")} />);
  fireEvent.click(screen.getByRole("button", { name: "专业依据" }));
  expect(
    screen.getByText("IC 回测已过期（2026-05-01），本次已降级为不参与"),
  ).toBeInTheDocument();
});

it("does not add a warning for fresh IC", () => {
  render(<ReportRecommendationList report={reportWithIcState("available")} />);
  fireEvent.click(screen.getByRole("button", { name: "专业依据" }));
  expect(screen.queryByText("量化回测未接入")).not.toBeInTheDocument();
  expect(screen.queryByText(/IC 回测已过期/)).not.toBeInTheDocument();
});
```

- [ ] **Step 3: Run tests and confirm failure**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_analysis_prompt_ic_status.py tests/test_recommendation_guard_evidence.py -q
cd ..\web
npm test -- src/components/FactorIcStatusBadge.test.tsx src/components/ReportRecommendationList.test.tsx
```

Expected: FAIL on the new language and UI notices.

- [ ] **Step 4: Implement backend language rules**

Define this shared constant in `analysis_prompt.py`:

```python
IC_EVIDENCE_INSTRUCTION = (
    "factor_scores.ic_status.state 表示因子 IC 是否参与：available 才可按"
    " factor_reliability 评价强弱；unavailable 必须写‘IC 回测未接入，IC 未参与本次结论’；"
    "stale 必须写‘IC 回测已过期，IC 未参与本次结论’。后两者不得称为量化背书弱。"
)
```

Convert `DEFAULT_ROLE_PROMPT` to an f-string and replace its existing factor-reliability bullet with `- {IC_EVIDENCE_INSTRUCTION}`. In `analysis_facts.py`, import `IC_EVIDENCE_INSTRUCTION` and concatenate it into the immutable `instruction` string immediately before the composite-evidence guidance. This gives the LLM role prompt and persisted facts one source of truth.

Change `_weak_evidence_reasons()`:

```python
    if evidence:
        composite = evidence.get("composite") or {}
        level = str(composite.get("level") or "")
        if level in {"低", "不足"}:
            sources = {
                str(component.get("source") or "")
                for component in evidence.get("components") or []
                if isinstance(component, dict)
            }
            reasons.append(
                "量化证据背书弱"
                if "factor" in sources
                else "IC 回测未覆盖，现有量化证据置信偏低"
            )
```

- [ ] **Step 5: Implement typed professional-evidence notices**

In `api.ts`, add:

```ts
export type FactorIcEvidenceStatus = {
  state: "unavailable" | "stale" | "available";
  available: boolean;
  stale?: boolean;
  run_date?: string;
  source?: "database" | "local_file" | "unavailable";
};
```

In `FactorIcStatusBadge`, change only the unavailable line to:

```tsx
return <StatusLine tone="muted">IC 回测数据未接入</StatusLine>;
```

In `FundRecommendationCard.tsx`, add `FactorIcEvidenceStatus` to the type import from `@/lib/api`, then add:

```tsx
function reportIcStatus(report: Report): FactorIcEvidenceStatus | null {
  const facts = report.analysis_facts as {
    factor_scores?: { ic_status?: FactorIcEvidenceStatus };
  } | undefined;
  return facts?.factor_scores?.ic_status ?? null;
}

function FactorIcNotice({ status }: { status: FactorIcEvidenceStatus | null }) {
  if (!status || status.state === "available") return null;
  if (status.state === "stale") {
    return (
      <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
        IC 回测已过期{status.run_date ? `（${status.run_date}）` : ""}，本次已降级为不参与
      </div>
    );
  }
  return (
    <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700">
      <strong className="block text-slate-900">量化回测未接入</strong>
      当前建议主要依据持仓风险、行情与新闻；IC 不参与本次结论。
    </div>
  );
}
```

Compute `const icStatus = reportIcStatus(report);` and render `<FactorIcNotice status={icStatus} />` inside the open “专业依据” section before the sector opportunity.

- [ ] **Step 6: Run tests and commit**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_analysis_prompt_ic_status.py tests/test_recommendation_guard_evidence.py -q
cd ..\web
npm test -- src/components/FactorIcStatusBadge.test.tsx src/components/ReportRecommendationList.test.tsx src/components/ReportPanel.test.tsx
npm run typecheck
npm run lint
git add ../api/app/services/analysis_prompt.py ../api/app/services/analysis_facts.py ../api/app/services/recommendation_guard.py ../api/tests/test_analysis_prompt_ic_status.py ../api/tests/test_recommendation_guard_evidence.py src/lib/api.ts src/components/FactorIcStatusBadge.tsx src/components/FactorIcStatusBadge.test.tsx src/components/FundRecommendationCard.tsx src/components/ReportRecommendationList.test.tsx
git commit -m "fix: explain unavailable IC in daily reports"
```

Expected: PASS.

### Task 4: Document the safe Lighthouse recovery boundary

**Files:**
- Modify: `docs/deploy/lighthouse-cicd.md:173-210`

- [ ] **Step 1: Add the diagnosis and authorization checklist**

Append under “启用 Factor IC 定时发布”:

```markdown
### IC 迁移诊断

- GitHub workflow 成功不代表当前 Lighthouse 已收到快照；先核对该次运行提交中的
  `FACTOR_IC_PUBLISH_URL`。
- `POST /api/internal/factor-ic-snapshots` 返回 503“因子 IC 发布未配置”表示
  Lighthouse 缺少 `FUND_AI_FACTOR_IC_PUBLISH_TOKEN`，此时禁止启用定时发布。
- 生产恢复必须使用 HTTPS、匹配的独立 Token 和 production Environment；不得改成
  HTTP，也不得直接开放或连接 MySQL 进行人工写入。
- 本地验收产物只能发布到任务专用 SQLite。它证明代码链路可用，不代表生产迁移完成。
```

- [ ] **Step 2: Verify docs and commit**

```powershell
git diff --check
git add docs/deploy/lighthouse-cicd.md
git commit -m "docs: clarify Lighthouse factor IC recovery"
```

Expected: no whitespace errors.

### Task 5: Generate and publish a real IC snapshot to isolated SQLite

**Files:**
- Generated and ignored: `.superpowers/sdd/factor-ic-live/report.txt`
- Generated and ignored: `.superpowers/sdd/factor-ic-live/summary.json`
- Runtime database only: `.superpowers/sdd/qa-daily-report-final.db`

- [ ] **Step 1: Prove the environment cannot select production MySQL**

In a fresh PowerShell window set:

```powershell
$env:FUND_AI_DATABASE_URL='sqlite-local-only'
$env:FUND_AI_DB_PATH='D:\code\HL_Project\fundpilot-ai\.superpowers\sdd\qa-daily-report-final.db'
$env:FUND_AI_DB_FALLBACK_SQLITE='false'
$env:FUND_AI_FACTOR_IC_PUBLISH_TOKEN='local-ic-qa-20260711-only'
```

Run:

```powershell
cd D:\code\HL_Project\fundpilot-ai\apps\api
.\.venv\Scripts\python.exe -c "from app.config import get_settings; s=get_settings(); assert not s.uses_mysql; print(s.database_url, s.db_path)"
```

Expected: output contains `sqlite-local-only` and the task database path; assertion passes.

- [ ] **Step 2: Generate the full real IC summary with workflow parameters**

```powershell
.\.venv\Scripts\python.exe scripts/run_factor_ic.py --universe-mode sampled --sample-pool-size 500 --universe-size 300 --nav-days 750 --rebalance-step 21 --forward-days 20 --factor-lookback 250 --max-workers 8 --out-dir ..\..\.superpowers\sdd\factor-ic-live
```

Expected: exit code 0; `summary.json` has `available=true`, `universe_size >= 240`, `rebalance_count >= 12`, and four factor rows.

- [ ] **Step 3: Start an isolated loopback API on port 8011**

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8011
```

Expected: `/health` responds 200. Keep this process running in the task terminal; do not use a production hostname.

- [ ] **Step 4: Publish through the internal API using only the local token**

In a second PowerShell window set the same SQLite variables, then:

```powershell
$env:FACTOR_IC_PUBLISH_URL='http://127.0.0.1:8011/api/internal/factor-ic-snapshots'
$env:FACTOR_IC_PUBLISH_TOKEN='local-ic-qa-20260711-only'
$env:GITHUB_SHA=(git -C D:\code\HL_Project\fundpilot-ai rev-parse HEAD)
$env:GITHUB_RUN_ID='local-20260711'
cd D:\code\HL_Project\fundpilot-ai\apps\api
.\.venv\Scripts\python.exe scripts/publish_factor_ic.py ..\..\.superpowers\sdd\factor-ic-live\summary.json
```

Expected: `factor IC publish result: created` or `duplicate`; never a production URL.

- [ ] **Step 5: Validate storage through the authenticated loopback API**

Register or log into a local-only QA user, then call the status endpoint:

```powershell
$register = @{
  userAccount = 'ic-qa-20260711@local.test'
  password = 'LocalIcQa-20260711!'
  username = 'IC QA'
} | ConvertTo-Json
try {
  $auth = Invoke-RestMethod -Uri 'http://127.0.0.1:8011/api/auth/register' -Method Post -ContentType 'application/json' -Body $register
} catch {
  $login = @{ userAccount = 'ic-qa-20260711@local.test'; password = 'LocalIcQa-20260711!' } | ConvertTo-Json
  $auth = Invoke-RestMethod -Uri 'http://127.0.0.1:8011/api/auth/login' -Method Post -ContentType 'application/json' -Body $login
}
$headers = @{ Authorization = "Bearer $($auth.accessToken)" }
$status = Invoke-RestMethod -Uri 'http://127.0.0.1:8011/api/diagnostics/factor-ic-status' -Headers $headers
if (-not $status.available -or $status.stale -or $status.source -ne 'database' -or $status.universe_size -lt 240) {
  throw "unexpected factor IC status: $($status | ConvertTo-Json -Compress)"
}
$status | ConvertTo-Json -Depth 5
```

Expected: `available=true`, `stale=false`, `source=database`, and `universe_size >= 240`.

- [ ] **Step 6: Generate a real DeepSeek report through the same loopback API**

Use the token from Step 5:

```powershell
$analysis = @{
  holdings = @(
    @{
      fund_code = '008586'
      fund_name = '华夏人工智能ETF联接C'
      holding_amount = 14305
      holding_return_percent = 7.47
      sector_name = '人工智能'
      sector_return_percent = -2.97
      sector_return_percent_source = 'realtime'
    },
    @{
      fund_code = '021627'
      fund_name = '华富半导体产业混合发起式C'
      holding_amount = 2000
      holding_return_percent = 0
      sector_name = '半导体'
      sector_return_percent = -6.72
      sector_return_percent_source = 'realtime'
    }
  )
  profile = @{
    style = '稳健'
    horizon = '半年到一年'
    max_drawdown_percent = 8
    concentration_limit_percent = 35
    expected_investment_amount = 30000
    prefer_dca = $true
    avoid_chasing = $true
    decision_style = 'conservative'
    investment_preset = 'conservative_hold'
  }
  analysis_mode = 'deep'
  ocr_text = $null
} | ConvertTo-Json -Depth 8
$created = Invoke-RestMethod -Uri 'http://127.0.0.1:8011/api/analyze/async' -Method Post -Headers $headers -ContentType 'application/json' -Body $analysis
do {
  Start-Sleep -Seconds 2
  $job = Invoke-RestMethod -Uri "http://127.0.0.1:8011/api/jobs/$($created.job_id)" -Headers $headers
  if ($job.status -eq 'failed') { throw $job.error }
} while ($job.status -notin @('completed', 'failed'))
$ic = $job.report.analysis_facts.factor_scores.ic_status
$bases = @($job.report.analysis_facts.factor_scores.factor_reliability.PSObject.Properties.Value.basis)
$aiHolding = @($job.report.analysis_facts.holdings | Where-Object { $_.fund_code -eq '008586' })[0]
if ($ic.state -ne 'available') { throw "report did not consume fresh IC" }
if (-not ($bases | Where-Object { $_ -ne '无回测数据' })) { throw "report has no usable IC basis" }
if ($job.report.provider -ne 'deepseek-v4-pro') { throw "unexpected provider: $($job.report.provider)" }
if ($null -eq $aiHolding.sector_opportunity.today_main_force_net_yi) { throw "today main-force evidence is missing" }
$job.report.id
```

Expected: the job completes, consumes `ic_status.state=available`, contains at least one non-missing IC basis, reports `provider=deepseek-v4-pro`, and preserves a real same-day main-force value for `008586`.

- [ ] **Step 7: Open the isolated report in Chrome**

Start a dedicated web dev server:

```powershell
cd D:\code\HL_Project\fundpilot-ai\apps\web
$env:NEXT_PUBLIC_API_BASE_URL='http://127.0.0.1:8011'
npm run dev -- --port 3011
```

In Chrome, open `http://127.0.0.1:3011`, log in with the local QA account from Step 5, open “日报”, and select the report id printed by Step 6. Perform this exact checklist:

1. At a 1440×900 viewport, expand the `008586` professional evidence and confirm “今日主力” is numeric. If the real history source did not provide five complete points, confirm the other cell says “5日历史暂缺” without “— 亿”.
2. Confirm neither “量化回测未接入” nor an expired warning appears.
3. Click “追问这份日报”; confirm the side panel has complementary semantics, the dark backdrop is absent, and the report page scroll position changes when scrolling over the report column while the chat panel stays visible.
4. Switch to a 1024px viewport, reopen chat, and confirm it is a modal dialog that locks report scrolling.
5. Switch to 390×844, confirm the bottom modal avoids the bottom navigation and the page has no horizontal overflow.
6. Close chat in each mode and confirm focus returns to “追问这份日报”.
7. Read browser console errors and warnings; there must be no new application error or hydration warning.

No generated IC file or SQLite file is staged or committed.

### Task 6: Run the complete IC regression suite

**Files:**
- No production files changed in this task.

- [ ] **Step 1: Run all IC and report-language backend tests**

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_factor_ic_snapshot.py tests/test_factor_confidence.py tests/test_factor_ic_status_endpoint.py tests/test_factor_ic_publish_endpoint.py tests/test_portfolio_snapshot_factor_ic_state.py tests/test_portfolio_snapshot_factor_concurrency.py tests/test_analysis_prompt_ic_status.py tests/test_recommendation_guard_evidence.py -q
```

Expected: PASS.

- [ ] **Step 2: Run related frontend checks**

```powershell
cd ..\web
npm test -- src/components/FactorIcStatusBadge.test.tsx src/components/ReportRecommendationList.test.tsx src/components/ReportPanel.test.tsx
npm run typecheck
npm run lint
```

Expected: PASS.

- [ ] **Step 3: Verify production boundaries and patch hygiene**

```powershell
cd ..\..
git diff --check
git status --short
git diff -- .github/workflows/factor-ic-refresh.yml docker-compose.production.yml
```

Expected: no whitespace errors; the final command has no output because production workflow/compose configuration was not changed.
