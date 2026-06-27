# Holdings Fast Sector Resolution and Official NAV Settlement

**Date:** 2026-06-27
**Status:** Design approved for planning

## Problem

The holdings tab has two coupled symptoms:

1. First launch and holdings sector refresh can take too long.
2. After the official NAV for the last effective trading day is published, holdings may still show estimated data instead of an "updated" official settlement. On non-trading days, the UI should show the previous effective trading day's settlement data rather than waiting for the next market session.

The current date for this investigation is Saturday, 2026-06-27. The app's trading calendar resolves this to `session_kind=non_trading_day` and `effective_trade_date=2026-06-26`.

## Evidence

Code-path evidence:

- `sector_quote_service.refresh_holdings_sector_quotes()` always calls `refresh_benchmark_sectors_for_holdings()` before quote resolution.
- `refresh_benchmark_sectors_for_holdings()` calls `apply_primary_sector_to_holding(..., fetch_benchmark=True)` per uncached fund, which can call `fetch_fund_benchmark_text()`.
- `fetch_fund_benchmark_text()` runs an AkShare subprocess with a 45 second timeout.
- A local diagnostic with five uncached funds triggered five benchmark fetch calls.
- `portfolio_persistence.enrich_loaded_holdings(..., with_network=False)` intentionally avoids official NAV network calls during normal holdings restoration.
- `portfolio_sector_refresh.refresh_portfolio_sectors_for_user()` persists with `with_official_nav=False`.
- `/api/sector-quotes/status` disables auto refresh outside intraday/pre-close sessions, so the web app does not poll on weekends.

Market/product evidence:

- Eastmoney/Tiantian Fund's NAV table states open-fund NAV data is updated on trading days from 16:00 to 23:00.
- Alipay's收益分析 compliance page describes statistics as of the most recent update date.

This supports the product rule: non-trading days should display the latest published trading-day settlement once available.

## Goals

- Keep holdings first paint fast and predictable.
- Avoid per-fund benchmark AkShare subprocesses on GET `/api/portfolio/holdings` and fast refresh paths.
- Preserve the improved benchmark-index mapping for index-like funds, but move misses off the blocking path.
- Add an official NAV settlement path that can run after close, before open, and on non-trading days.
- Persist official settlement into the daily snapshot and portfolio summary so subsequent loads show "已更新".
- Keep intraday semantics unchanged: during trading hours, sector estimates remain estimates and official NAV must not be shown prematurely.
- Preserve `profit_accrual_deferred` behavior: new purchases awaiting share confirmation must not accrue official daily profit.

## Non-Goals

- Do not rebuild the entire sector taxonomy.
- Do not change OCR parsing semantics.
- Do not change report/discovery pipelines.
- Do not force every holdings GET to pull all official NAV values synchronously.
- Do not change the display contract for `settled_holding_amount`, `display_holding_amount`, or `daily_return_percent_source`.

## Recommended Approach

Implement two independent paths.

### 1. Fast Sector Resolution

Add a non-blocking mode to benchmark sector resolution.

Fast/cache paths should:

- Read existing `fund_primary_sectors` records.
- Use `benchmark_index` records if already cached.
- Use high-trust user/OCR/manual mappings when present.
- Use existing fallback inference if no cached benchmark exists.
- Not call `fetch_fund_benchmark_text()` inline.

Accurate/manual paths may:

- Fetch benchmark text inline for a single explicit mapping refresh.
- Queue or run background benchmark hydration for uncached funds after returning the initial response.

To avoid repeated misses:

- Add a short-lived benchmark-resolution miss cache keyed by `fund_code`.
- Cache positive benchmark records in `fund_primary_sectors`, as today.
- Cache negative misses in memory only, so future app versions can still discover mappings after TTL.

### 2. Official NAV Settlement Refresh

Add a light settlement service independent of sector quote refresh.

The service should:

- Determine `effective_trade_date` from `trading_session`.
- Skip intraday and pre-close sessions.
- For each active holding, call `get_official_nav_return(fund_code, effective_trade_date)`.
- If official NAV exists and the holding is not `profit_accrual_deferred`, set:
  - `daily_return_percent`
  - `daily_profit`
  - `daily_return_percent_source = "official_nav"`
- Recompute `holding_amount`/`settled_holding_amount` from shares when `holding_amount_sync` has enough data and official NAV is available.
- Persist updated holdings through the same daily snapshot and portfolio summary path used by sector refresh.
- Return a compact result with matched count, missing count, settlement date, and serialized holdings.

This should be exposed as a small endpoint, for example:

`POST /api/portfolio/settle-official-nav`

The endpoint should be safe to call repeatedly. Existing NAV caches (`TTL_HIT=24h`, `TTL_MISS=5min`) already reduce repeated source calls.

### 3. Web Trigger

After `hydratePortfolio()` finishes:

- If holdings exist, the session is not intraday/pre-close, and not all active holdings have `daily_return_percent_source === "official_nav"`, call the settlement endpoint in the background.
- If the endpoint returns updated holdings, replace local holdings and portfolio summary.
- Save the updated payload to `portfolioHoldingsCache`.

The existing manual refresh button can continue to call `refresh(true, "accurate")`; it is no longer the only path to official settlement.

## Data Flow

Initial holdings load:

```text
GET /api/portfolio/holdings
  -> load snapshot/profiles
  -> enrich without network
  -> apply server sector spot cache only
  -> serialize fast response
  -> schedule intraday/detail warmup
```

Background settlement:

```text
web hydratePortfolio completes
  -> POST /api/portfolio/settle-official-nav
      -> load persisted holdings
      -> sync amounts from shares
      -> overlay official NAV for effective_trade_date
      -> enrich computed display fields
      -> persist daily snapshot + summary
      -> serialize holdings
  -> web replaces holdings and summary
```

Sector benchmark hydration:

```text
refresh-sector-quotes fast/cache path
  -> use cached/high-trust sector records
  -> no benchmark subprocess
  -> return quickly
  -> optional background hydration for uncached candidates
```

## API Contract

`POST /api/portfolio/settle-official-nav`

Response:

```json
{
  "ok": true,
  "settlement_date": "2026-06-26",
  "session_kind": "non_trading_day",
  "matched": 3,
  "missing": 1,
  "skipped_deferred": 1,
  "holdings": [],
  "portfolio_summary": {},
  "refreshed_at": "2026-06-27T..."
}
```

If called intraday/pre-close:

```json
{
  "ok": true,
  "skipped": true,
  "reason": "intraday",
  "holdings": [],
  "portfolio_summary": null
}
```

## Error Handling

- Per-fund NAV failures should not fail the whole request.
- If no official NAV is available yet, keep existing estimates and return `missing > 0`.
- If persistence fails, return an HTTP 500 so the UI keeps the current local state.
- Background settlement errors in the web app should be silent except for developer console or existing message channel only when the user explicitly clicked a refresh-like control.

## Testing

Backend tests:

- Fast sector refresh does not call benchmark fetch when `cache_only=True`.
- Fast sector refresh does not call benchmark fetch when a budgeted fast request is used.
- Cached `benchmark_index` records are still applied without fetching.
- Explicit accurate/manual benchmark refresh can still fetch.
- Official settlement endpoint overlays NAV on non-trading days.
- Official settlement skips intraday/pre-close.
- Official settlement persists `daily_return_percent_source="official_nav"` and recomputes portfolio summary.
- Deferred holdings remain zero/pending and are counted as skipped.
- Missing official NAV leaves existing estimate intact.

Frontend tests:

- `hydratePortfolio()` triggers official settlement after non-intraday load when holdings are not all official.
- It does not trigger settlement during intraday/pre-close.
- It saves settled holdings to `portfolioHoldingsCache`.
- "已更新" badge appears when returned holdings have official source.

Manual verification:

- Start API and web locally.
- Open holdings tab on a non-trading day.
- Confirm initial paint is fast.
- Confirm background settlement changes daily column to official NAV and shows "已更新".
- Confirm manual accurate refresh still works.

## Open Decisions

None. The user approved the recommended approach on 2026-06-27.

## Self-Review

- No unresolved markers remain.
- Scope is limited to holdings sector resolution and official NAV settlement.
- The design keeps existing display contracts intact.
- Intraday and deferred-accrual behavior are explicitly preserved.
- Testing covers both root causes and the web trigger.
