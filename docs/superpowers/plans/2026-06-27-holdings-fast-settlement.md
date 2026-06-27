# Holdings Fast Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make holdings load quickly while automatically settling official NAV data after close, before open, and on non-trading days.

**Architecture:** Split the fix into two narrow paths. Sector refresh gets a fast benchmark mode that never launches per-fund benchmark subprocesses on cache/fast paths. Official NAV settlement becomes a separate idempotent service and endpoint that the web app calls in the background after holdings hydration.

**Tech Stack:** FastAPI, Pydantic models, pytest, React/Next.js, TypeScript, Vitest.

---

## File Structure

- Modify `apps/api/app/services/fund_primary_sector_service.py`: add benchmark fetch controls and miss cache.
- Modify `apps/api/app/services/sector_quote_service.py`: pass fast/accurate benchmark mode into sector resolution.
- Create `apps/api/app/services/official_nav_settlement.py`: load, settle, persist, and serialize official NAV settlement results.
- Modify `apps/api/app/main.py`: expose `POST /api/portfolio/settle-official-nav`.
- Modify `apps/web/src/lib/api.ts`: add settlement response type and API helper.
- Modify `apps/web/src/components/Dashboard.tsx`: call settlement helper after holdings hydration when needed.
- Add `apps/api/tests/test_holdings_fast_sector_resolution.py`: backend fast-sector regression tests.
- Add `apps/api/tests/test_official_nav_settlement.py`: backend settlement service/API tests.
- Add `apps/web/src/lib/api.settlement.test.ts`: frontend API helper test.
- Add or extend `apps/web/src/components/Dashboard.settlement.test.tsx`: frontend hydration trigger test if a Dashboard test harness exists cleanly; otherwise test the extracted helper from Task 4.

---

### Task 1: Fast Benchmark Sector Mode

**Files:**
- Modify: `apps/api/app/services/fund_primary_sector_service.py`
- Modify: `apps/api/app/services/sector_quote_service.py`
- Test: `apps/api/tests/test_holdings_fast_sector_resolution.py`

- [ ] **Step 1: Write failing direct fast-mode tests**

Create `apps/api/tests/test_holdings_fast_sector_resolution.py` with:

```python
from app.models import Holding
from app.services import fund_primary_sector_service as service


def test_refresh_benchmark_sectors_skips_uncached_fetch_in_fast_mode(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(service, "get_fund_primary_sector", lambda _code: None)
    monkeypatch.setattr(service, "get_fund_profile_by_code", lambda _code: None)
    monkeypatch.setattr(service, "save_fund_primary_sector", lambda **_kwargs: None)

    import app.services.fund_benchmark_sector as benchmark

    monkeypatch.setattr(
        benchmark,
        "fetch_fund_benchmark_text",
        lambda code: calls.append(code) or None,
    )

    holdings = [
        Holding(
            fund_code="021533",
            fund_name="天弘半导体材料设备指数C",
            holding_amount=1000,
            return_percent=1.0,
        )
    ]

    result = service.refresh_benchmark_sectors_for_holdings(
        holdings,
        fetch_missing_benchmark=False,
    )

    assert calls == []
    assert result[0].fund_code == "021533"


def test_refresh_benchmark_sectors_still_uses_cached_benchmark_in_fast_mode(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_fund_primary_sector",
        lambda _code: {
            "fund_code": "021533",
            "sector_name": "半导体材料",
            "intraday_index_name": "中证半导体材料设备主题指数",
            "source": "benchmark_index",
            "confidence": 0.82,
            "detail": "{}",
        },
    )

    holdings = [
        Holding(
            fund_code="021533",
            fund_name="天弘半导体材料设备指数C",
            holding_amount=1000,
            return_percent=1.0,
        )
    ]

    result = service.refresh_benchmark_sectors_for_holdings(
        holdings,
        fetch_missing_benchmark=False,
    )

    assert result[0].sector_name == "半导体材料"
    assert result[0].intraday_index_name == "中证半导体材料设备主题指数"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_holdings_fast_sector_resolution.py -q
```

Expected: first test fails with `TypeError` because `fetch_missing_benchmark` is not accepted.

- [ ] **Step 3: Implement fast-mode parameter and miss cache**

In `apps/api/app/services/fund_primary_sector_service.py`, add near `_HIGH_TRUST_SECTOR_SOURCES`:

```python
import time

_BENCHMARK_MISS_TTL_SECONDS = 3600.0
_BENCHMARK_MISS_CACHE: dict[str, float] = {}


def _benchmark_miss_cached(fund_code: str) -> bool:
    expires_at = _BENCHMARK_MISS_CACHE.get(fund_code)
    if expires_at is None:
        return False
    if time.monotonic() < expires_at:
        return True
    _BENCHMARK_MISS_CACHE.pop(fund_code, None)
    return False


def _record_benchmark_miss(fund_code: str) -> None:
    _BENCHMARK_MISS_CACHE[fund_code] = time.monotonic() + _BENCHMARK_MISS_TTL_SECONDS
```

Change `_resolve_from_benchmark_index` so it checks the miss cache before fetching and records a miss when no text or no mapping is found:

```python
def _resolve_from_benchmark_index(fund_code: str, *, fetch: bool = True) -> PrimarySectorRecord | None:
    from app.services.fund_benchmark_sector import fetch_fund_benchmark_text, resolve_sector_from_benchmark

    existing = get_fund_primary_sector(fund_code)
    if existing and str(existing.get("source") or "") == "benchmark_index":
        return _record_from_row(existing)

    if not fetch or _benchmark_miss_cached(fund_code):
        return None

    benchmark_text = fetch_fund_benchmark_text(fund_code)
    if not benchmark_text:
        _record_benchmark_miss(fund_code)
        return None
    resolved = resolve_sector_from_benchmark(benchmark_text)
    if resolved is None:
        _record_benchmark_miss(fund_code)
        return None
    ...
```

Change `refresh_benchmark_sectors_for_holdings` signature and call:

```python
def refresh_benchmark_sectors_for_holdings(
    holdings: list[Holding],
    *,
    fetch_missing_benchmark: bool = True,
) -> list[Holding]:
    refreshed: list[Holding] = []
    for holding in holdings:
        code = (holding.fund_code or "").strip()
        if not code or code == "000000":
            refreshed.append(holding)
            continue
        row = get_fund_primary_sector(code)
        if row and str(row.get("source") or "") in _HIGH_TRUST_SECTOR_SOURCES:
            refreshed.append(holding)
            continue
        if row and str(row.get("source") or "") == "benchmark_index":
            refreshed.append(apply_primary_sector_to_holding(holding, fetch_benchmark=False))
            continue
        refreshed.append(
            apply_primary_sector_to_holding(
                holding,
                fetch_benchmark=fetch_missing_benchmark,
            )
        )
    return refreshed
```

- [ ] **Step 4: Run direct tests and verify they pass**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_holdings_fast_sector_resolution.py -q
```

Expected: both tests pass.

- [ ] **Step 5: Write failing integration test for sector refresh budget**

Append to `apps/api/tests/test_holdings_fast_sector_resolution.py`:

```python
def test_sector_refresh_cache_only_skips_benchmark_fetch(monkeypatch):
    from app.services import sector_quote_service
    from app.services.sector_quote_provider import SpotBoardFetchResult

    calls: list[str] = []
    monkeypatch.setattr(service, "get_fund_primary_sector", lambda _code: None)
    monkeypatch.setattr(service, "get_fund_profile_by_code", lambda _code: None)
    monkeypatch.setattr(service, "save_fund_primary_sector", lambda **_kwargs: None)

    import app.services.fund_benchmark_sector as benchmark

    monkeypatch.setattr(
        benchmark,
        "fetch_fund_benchmark_text",
        lambda code: calls.append(code) or None,
    )
    monkeypatch.setattr(
        sector_quote_service,
        "load_spot_boards_from_cache_only",
        lambda: SpotBoardFetchResult(boards={"index": {}, "concept": {}, "industry": {}}, provider_path="cache_miss"),
    )

    holdings = [
        Holding(
            fund_code="021533",
            fund_name="天弘半导体材料设备指数C",
            holding_amount=1000,
            return_percent=1.0,
        )
    ]

    sector_quote_service.refresh_holdings_sector_quotes(holdings, cache_only=True)

    assert calls == []
```

- [ ] **Step 6: Run integration test and verify it fails**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_holdings_fast_sector_resolution.py::test_sector_refresh_cache_only_skips_benchmark_fetch -q
```

Expected: fails because `refresh_holdings_sector_quotes` still calls benchmark fetch before cache-only resolution.

- [ ] **Step 7: Wire fast mode into sector quote service**

In `apps/api/app/services/sector_quote_service.py`, replace:

```python
holdings = refresh_benchmark_sectors_for_holdings(holdings)
```

with:

```python
holdings = refresh_benchmark_sectors_for_holdings(
    holdings,
    fetch_missing_benchmark=not cache_only and timeout_seconds is None,
)
```

This keeps manual accurate refresh behavior because `budget == "accurate"` sets `timeout_seconds=None` in `main.py`.

- [ ] **Step 8: Run task tests**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_holdings_fast_sector_resolution.py -q
```

Expected: all tests pass.

- [ ] **Step 9: Commit Task 1**

Run:

```powershell
git add apps/api/app/services/fund_primary_sector_service.py apps/api/app/services/sector_quote_service.py apps/api/tests/test_holdings_fast_sector_resolution.py
git commit -m "fix(holdings): skip benchmark fetch on fast sector paths"
```

---

### Task 2: Official NAV Settlement Service

**Files:**
- Create: `apps/api/app/services/official_nav_settlement.py`
- Test: `apps/api/tests/test_official_nav_settlement.py`

- [ ] **Step 1: Write failing service tests**

Create `apps/api/tests/test_official_nav_settlement.py`:

```python
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Holding


def _non_trading_session():
    return {
        "timezone": "Asia/Shanghai",
        "local_datetime": "2026-06-27 10:00",
        "calendar_date": "2026-06-27",
        "effective_trade_date": "2026-06-26",
        "is_trading_day": False,
        "session_kind": "non_trading_day",
        "minutes_to_close": None,
        "decision_window": "non trading",
        "market_close_time": "15:00",
        "market_open_time": "09:30",
    }


def test_settle_official_nav_overlays_non_trading_day(monkeypatch):
    from app.services import official_nav_settlement as service

    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=1000,
            settled_holding_amount=1000,
            return_percent=2.0,
            sector_return_percent=0.5,
        )
    ]

    monkeypatch.setattr(service, "build_trading_session", _non_trading_session)
    monkeypatch.setattr(service, "load_persisted_holdings", lambda: (holdings, "snapshot", "2026-06-26", None))
    monkeypatch.setattr(service, "get_official_nav_return", lambda _code, _date: 1.23)
    monkeypatch.setattr(service, "sync_holding_amounts_from_shares", lambda rows, **_kwargs: rows)
    monkeypatch.setattr(service, "persist_holdings_after_sector_refresh", lambda rows, **_kwargs: rows)

    result = service.settle_official_nav_for_portfolio()

    assert result["ok"] is True
    assert result["settlement_date"] == "2026-06-26"
    assert result["matched"] == 1
    assert result["missing"] == 0
    settled = result["holdings"][0]
    assert settled["daily_return_percent"] == 1.23
    assert settled["daily_return_percent_source"] == "official_nav"
    assert settled["daily_profit"] == 12.3


def test_settle_official_nav_skips_intraday(monkeypatch):
    from app.services import official_nav_settlement as service

    monkeypatch.setattr(
        service,
        "build_trading_session",
        lambda: {
            **_non_trading_session(),
            "session_kind": "trading_day_intraday",
            "is_trading_day": True,
        },
    )

    result = service.settle_official_nav_for_portfolio()

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "intraday"
```

- [ ] **Step 2: Run service tests and verify they fail**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_official_nav_settlement.py -q
```

Expected: fails with `ImportError` because `official_nav_settlement` does not exist.

- [ ] **Step 3: Implement minimal settlement service**

Create `apps/api/app/services/official_nav_settlement.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Holding, PortfolioSummary
from app.services.holding_amount_sync import sync_holding_amounts_from_shares
from app.services.holding_client import serialize_holdings_for_client
from app.services.holding_estimates import (
    compute_daily_profit_from_rate,
    enrich_holdings_estimates,
    sum_daily_profit,
)
from app.services.portfolio_holdings_service import build_portfolio_holdings_response, load_persisted_holdings
from app.services.portfolio_persistence import persist_holdings_after_sector_refresh
from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred
from app.services.trading_session import build_trading_session
from app.services.fund_nav_service import get_official_nav_return

_SKIP_SESSIONS = {"trading_day_intraday", "trading_day_pre_close"}


def _active_holding(holding: Holding) -> bool:
    return bool(
        holding.fund_code
        and holding.fund_code != "000000"
        and (holding.settled_holding_amount or holding.holding_amount) > 0
    )


def _amount_for_daily_profit(holding: Holding) -> float:
    return holding.settled_holding_amount or holding.holding_amount


def settle_official_nav_for_holdings(
    holdings: list[Holding],
    *,
    settlement_date: str,
) -> tuple[list[Holding], dict[str, int]]:
    matched = 0
    missing = 0
    skipped_deferred = 0
    updated: list[Holding] = []

    for holding in holdings:
        if not _active_holding(holding):
            updated.append(holding)
            continue
        profile = get_profile_for_holding(holding)
        if is_profit_accrual_deferred(profile):
            skipped_deferred += 1
            updated.append(
                holding.model_copy(
                    update={
                        "daily_profit": 0.0,
                        "daily_return_percent": 0.0,
                        "daily_return_percent_source": "pending_accrual",
                    }
                )
            )
            continue
        nav_return = get_official_nav_return(holding.fund_code, settlement_date)
        if nav_return is None:
            missing += 1
            updated.append(holding)
            continue
        amount = _amount_for_daily_profit(holding)
        matched += 1
        updated.append(
            holding.model_copy(
                update={
                    "daily_return_percent": nav_return,
                    "daily_profit": compute_daily_profit_from_rate(
                        amount,
                        nav_return,
                        amount_includes_today=False,
                    ),
                    "daily_return_percent_source": "official_nav",
                }
            )
        )

    return enrich_holdings_estimates(updated), {
        "matched": matched,
        "missing": missing,
        "skipped_deferred": skipped_deferred,
    }


def settle_official_nav_for_portfolio() -> dict:
    session = build_trading_session()
    session_kind = str(session.get("session_kind") or "")
    settlement_date = str(session.get("effective_trade_date") or "")
    if session_kind in _SKIP_SESSIONS:
        return {
            "ok": True,
            "skipped": True,
            "reason": "intraday",
            "settlement_date": settlement_date,
            "session_kind": session_kind,
            "holdings": [],
            "portfolio_summary": None,
        }

    holdings, source, snapshot_date, refreshed_at = load_persisted_holdings()
    if not holdings:
        return {
            "ok": True,
            "settlement_date": settlement_date,
            "session_kind": session_kind,
            "matched": 0,
            "missing": 0,
            "skipped_deferred": 0,
            "holdings": [],
            "portfolio_summary": None,
            "refreshed_at": None,
        }

    synced = sync_holding_amounts_from_shares(holdings)
    settled, counts = settle_official_nav_for_holdings(
        synced,
        settlement_date=settlement_date,
    )
    fetched_at = datetime.now(timezone.utc)
    persisted = persist_holdings_after_sector_refresh(
        settled,
        fetched_at=fetched_at,
        with_official_nav=False,
    )
    payload = build_portfolio_holdings_response(
        persisted,
        source=source,
        snapshot_date=snapshot_date,
        refreshed_at=fetched_at,
    )
    return {
        "ok": True,
        "settlement_date": settlement_date,
        "session_kind": session_kind,
        **counts,
        "holdings": payload["holdings"],
        "portfolio_summary": payload["portfolio_summary"],
        "refreshed_at": payload["refreshed_at"],
    }
```

- [ ] **Step 4: Run service tests and verify they pass**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_official_nav_settlement.py -q
```

Expected: tests pass.

- [ ] **Step 5: Add deferred and missing NAV tests**

Append:

```python
def test_settle_official_nav_keeps_missing_estimate(monkeypatch):
    from app.services import official_nav_settlement as service

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=1000,
        settled_holding_amount=1000,
        sector_return_percent=0.5,
        daily_return_percent=0.5,
        daily_profit=5.0,
        daily_return_percent_source="sector_estimate",
    )
    monkeypatch.setattr(service, "get_profile_for_holding", lambda _holding: None)
    monkeypatch.setattr(service, "is_profit_accrual_deferred", lambda _profile: False)
    monkeypatch.setattr(service, "get_official_nav_return", lambda _code, _date: None)

    settled, counts = service.settle_official_nav_for_holdings([holding], settlement_date="2026-06-26")

    assert counts["matched"] == 0
    assert counts["missing"] == 1
    assert settled[0].daily_return_percent_source == "sector_estimate"
    assert settled[0].daily_profit == 5.0


def test_settle_official_nav_preserves_deferred_zero(monkeypatch):
    from app.services import official_nav_settlement as service

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=1000,
        settled_holding_amount=1000,
        sector_return_percent=0.5,
    )
    monkeypatch.setattr(service, "get_profile_for_holding", lambda _holding: object())
    monkeypatch.setattr(service, "is_profit_accrual_deferred", lambda _profile: True)
    monkeypatch.setattr(service, "get_official_nav_return", lambda _code, _date: 1.23)

    settled, counts = service.settle_official_nav_for_holdings([holding], settlement_date="2026-06-26")

    assert counts["skipped_deferred"] == 1
    assert settled[0].daily_return_percent_source == "pending_accrual"
    assert settled[0].daily_profit == 0.0
```

- [ ] **Step 6: Run all settlement tests**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_official_nav_settlement.py -q
```

Expected: all settlement tests pass.

- [ ] **Step 7: Commit Task 2**

Run:

```powershell
git add apps/api/app/services/official_nav_settlement.py apps/api/tests/test_official_nav_settlement.py
git commit -m "feat(holdings): add official nav settlement service"
```

---

### Task 3: Official NAV Settlement API Endpoint

**Files:**
- Modify: `apps/api/app/main.py`
- Test: `apps/api/tests/test_official_nav_settlement.py`

- [ ] **Step 1: Write failing endpoint test**

Append to `apps/api/tests/test_official_nav_settlement.py`:

```python
def test_settle_official_nav_endpoint_returns_service_payload(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(
        main,
        "settle_official_nav_for_portfolio",
        lambda: {
            "ok": True,
            "settlement_date": "2026-06-26",
            "session_kind": "non_trading_day",
            "matched": 1,
            "missing": 0,
            "skipped_deferred": 0,
            "holdings": [],
            "portfolio_summary": None,
            "refreshed_at": "2026-06-27T02:00:00+00:00",
        },
    )

    response = client.post("/api/portfolio/settle-official-nav")

    assert response.status_code == 200
    assert response.json()["matched"] == 1
    assert response.json()["settlement_date"] == "2026-06-26"
```

- [ ] **Step 2: Run endpoint test and verify it fails**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_official_nav_settlement.py::test_settle_official_nav_endpoint_returns_service_payload -q
```

Expected: fails with 404 because the endpoint is missing.

- [ ] **Step 3: Add endpoint**

In `apps/api/app/main.py`, add import near holdings service imports:

```python
from app.services.official_nav_settlement import settle_official_nav_for_portfolio
```

Add route near `/api/portfolio/holdings` and `/api/holdings/refresh-sector-quotes`:

```python
@app.post("/api/portfolio/settle-official-nav")
def settle_portfolio_official_nav() -> dict:
    payload = settle_official_nav_for_portfolio()
    if payload.get("ok") and payload.get("holdings"):
        save_cached_holdings_response(
            {
                "holdings": payload["holdings"],
                "portfolio_summary": payload.get("portfolio_summary"),
                "source": "snapshot",
                "snapshot_date": payload.get("settlement_date"),
                "refreshed_at": payload.get("refreshed_at"),
            }
        )
    return payload
```

- [ ] **Step 4: Run endpoint and service tests**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_official_nav_settlement.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

Run:

```powershell
git add apps/api/app/main.py apps/api/tests/test_official_nav_settlement.py
git commit -m "feat(api): expose holdings official nav settlement"
```

---

### Task 4: Frontend API Helper and Hydration Trigger

**Files:**
- Modify: `apps/web/src/lib/api.ts`
- Modify: `apps/web/src/components/Dashboard.tsx`
- Test: `apps/web/src/lib/api.settlement.test.ts`

- [ ] **Step 1: Write failing API helper test**

Create `apps/web/src/lib/api.settlement.test.ts`:

```typescript
// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("official NAV settlement API helper", () => {
  it("posts to the settlement endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          ok: true,
          settlement_date: "2026-06-26",
          session_kind: "non_trading_day",
          matched: 1,
          missing: 0,
          skipped_deferred: 0,
          holdings: [],
          portfolio_summary: null,
          refreshed_at: "2026-06-27T02:00:00Z",
        }),
        { status: 200 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { settleOfficialNav } = await import("@/lib/api");
    const result = await settleOfficialNav();

    expect(result.matched).toBe(1);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/portfolio/settle-official-nav"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
```

- [ ] **Step 2: Run API helper test and verify it fails**

Run:

```powershell
cd apps/web
npm run test -- api.settlement.test.ts
```

Expected: fails because `settleOfficialNav` is not exported.

- [ ] **Step 3: Add frontend API type and helper**

In `apps/web/src/lib/api.ts`, after `PortfolioHoldingsPayload`, add:

```typescript
export type OfficialNavSettlementPayload = {
  ok: boolean;
  skipped?: boolean;
  reason?: string;
  settlement_date?: string | null;
  session_kind?: TradingSession["session_kind"] | string | null;
  matched?: number;
  missing?: number;
  skipped_deferred?: number;
  holdings: Holding[];
  portfolio_summary?: PortfolioSummary | null;
  refreshed_at?: string | null;
};

export async function settleOfficialNav(): Promise<OfficialNavSettlementPayload> {
  const response = await apiFetch(`${API_BASE}/api/portfolio/settle-official-nav`, {
    method: "POST",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}
```

- [ ] **Step 4: Run API helper test and verify it passes**

Run:

```powershell
cd apps/web
npm run test -- api.settlement.test.ts
```

Expected: test passes.

- [ ] **Step 5: Add hydration trigger helper inside Dashboard**

In `apps/web/src/components/Dashboard.tsx`, import `settleOfficialNav`:

```typescript
import {
  ...
  settleOfficialNav,
  ...
} from "@/lib/api";
```

Add local helper functions near `loadPortfolioSummary`:

```typescript
function shouldSettleOfficialNav(holdings: Holding[]): boolean {
  const active = displayableHoldings(holdings);
  if (!active.length) {
    return false;
  }
  return active.some(
    (holding) =>
      holding.daily_return_percent_source !== "official_nav" &&
      holding.daily_return_percent_source !== "pending_accrual",
  );
}
```

Update `hydratePortfolio` after saving fetched holdings:

```typescript
        if (shouldSettleOfficialNav(payload.holdings)) {
          void settleOfficialNav()
            .then((settled) => {
              if (settled.skipped || !settled.holdings.length) {
                return;
              }
              setHoldings(settled.holdings);
              if (settled.portfolio_summary) {
                setPortfolioSummary(settled.portfolio_summary);
              }
              const settledRefreshedAt = settled.refreshed_at ?? refreshedAt;
              setHoldingsRefreshedAt(settledRefreshedAt);
              saveCachedPortfolioHoldings({
                holdings: settled.holdings,
                portfolio_summary: settled.portfolio_summary ?? payload.portfolio_summary ?? null,
                refreshed_at: settledRefreshedAt,
              });
            })
            .catch(() => undefined);
        }
```

This is safe during intraday because the backend returns `skipped=true`.

- [ ] **Step 6: Run TypeScript check for Dashboard changes**

Run:

```powershell
cd apps/web
npm run typecheck
```

Expected: typecheck passes.

- [ ] **Step 7: Commit Task 4**

Run:

```powershell
git add apps/web/src/lib/api.ts apps/web/src/lib/api.settlement.test.ts apps/web/src/components/Dashboard.tsx
git commit -m "feat(web): settle official nav after holdings hydration"
```

---

### Task 5: Verification and Documentation Update

**Files:**
- Modify: `docs/PROJECT_CONTEXT.md`
- Test: backend and frontend focused suites

- [ ] **Step 1: Update project context**

Add a new top entry to `docs/PROJECT_CONTEXT.md`:

```markdown
- **持仓首屏提速 + 非交易日官方净值结算（2026-06-27）：** 持仓板块刷新 fast/cache 路径不再逐只触发业绩基准 AkShare 子进程；已缓存的 benchmark_index 映射仍直接使用，未命中映射改由 accurate/后台路径补全。新增 `POST /api/portfolio/settle-official-nav`，在收盘后、开盘前、非交易日按 `effective_trade_date` 拉官方 NAV，写回 `daily_return_percent_source=official_nav`、组合 `daily_profit` 与日快照；前端持仓 hydration 后静默触发，周末也能显示上一有效交易日结算与「已更新」。单测 `test_holdings_fast_sector_resolution.py` / `test_official_nav_settlement.py` / `api.settlement.test.ts`。
```

- [ ] **Step 2: Run focused backend tests**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_holdings_fast_sector_resolution.py tests/test_official_nav_settlement.py tests/test_apply_holdings_fast_path.py tests/test_holding_amount_sync.py tests/test_sector_refresh_daily_clear.py tests/test_profit_accrual_defer.py -q
```

Expected: all focused backend tests pass.

- [ ] **Step 3: Run focused frontend tests**

Run:

```powershell
cd apps/web
npm run test -- api.settlement.test.ts holdingMetrics.test.ts holdingDisplay.test.ts portfolioHoldingsCache.test.ts
```

Expected: all focused frontend tests pass.

- [ ] **Step 4: Run typecheck**

Run:

```powershell
cd apps/web
npm run typecheck
```

Expected: typecheck passes.

- [ ] **Step 5: Run backend smoke subset**

Run:

```powershell
cd apps/api
.\.venv\Scripts\python.exe -m pytest tests/test_api.py::test_portfolio_holdings_empty -q
```

Expected: test passes. If this exact test name is absent, run `.\.venv\Scripts\python.exe -m pytest tests/test_api.py -q` and report the actual result.

- [ ] **Step 6: Commit documentation and final verification**

Run:

```powershell
git add docs/PROJECT_CONTEXT.md
git commit -m "docs: update holdings settlement context"
```

Then run:

```powershell
git status --short
```

Expected: only unrelated pre-existing worktree changes remain.

---

## Self-Review

- Spec coverage: Task 1 covers fast sector resolution; Tasks 2 and 3 cover official settlement; Task 4 covers the web trigger; Task 5 covers verification and project context.
- Type consistency: backend response fields match frontend `OfficialNavSettlementPayload`; `daily_return_percent_source` values match the existing display contract.
- Execution order: each production change has a failing test first, then implementation, then verification.
