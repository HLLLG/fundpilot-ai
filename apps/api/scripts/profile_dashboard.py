"""Profile build_dashboard_payload step timings (run from apps/api)."""
from __future__ import annotations

import time
from contextlib import contextmanager

from app.services.fund_profile import FundProfileService
from app.database import get_portfolio_summary, list_portfolio_daily_snapshots


@contextmanager
def timed(label: str, results: dict[str, float]):
    t0 = time.perf_counter()
    yield
    results[label] = round(time.perf_counter() - t0, 3)


def profile_range(profit_range: str) -> None:
    from app.services.portfolio_snapshot import build_dashboard_payload

    profiles = FundProfileService().list_profiles()
    summary = get_portfolio_summary()
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    with timed("build_dashboard_payload", timings):
        build_dashboard_payload(
            summary=summary,
            profiles=profiles,
            profit_range=profit_range,  # type: ignore[arg-type]
            calendar_year=2026,
            calendar_month=6,
        )
    timings["total"] = round(time.perf_counter() - t0, 3)
    print(f"\n=== range={profit_range} ===")
    for key, value in timings.items():
        print(f"  {key}: {value}s")


def profile_internals() -> None:
    from app.services.portfolio_holdings_service import load_dashboard_holdings
    from app.services.portfolio_snapshot import (
        build_risk_metrics_payload,
        _dashboard_summary_payload,
    )
    from app.services.portfolio_snapshot import build_portfolio_trend_context
    from app.services.portfolio_profit_analysis import (
        build_calendar_month,
        build_profit_trend,
        build_daily_top5,
        summarize_trend_footer,
    )
    from app.services.index_daily_client import fetch_index_daily_history
    from app.models import FundProfile, Holding

    timings: dict[str, float] = {}

    with timed("list_profiles", timings):
        profiles = FundProfileService().list_profiles()
    with timed("get_portfolio_summary", timings):
        summary = get_portfolio_summary()
    with timed("list_snapshots_400", timings):
        history_rows = list_portfolio_daily_snapshots(limit=400)
    with timed("load_dashboard_holdings", timings):
        live_holdings, *_ = load_dashboard_holdings()
    profiles_by_code = {p.fund_code: p for p in profiles if isinstance(p, FundProfile)}
    holdings_models = (
        [Holding.model_validate(item) for item in (history_rows[0].get("holdings") or [])]
        if history_rows
        else []
    )

    with timed("fetch_index_000001_400", timings):
        fetch_index_daily_history("000001", trading_days=400)
    with timed("fetch_index_000300_400", timings):
        fetch_index_daily_history("000300", trading_days=400)

    with timed("build_profit_trend_today", timings):
        build_profit_trend(
            profit_range="today",
            snapshots=history_rows,
            holdings=live_holdings,
            profiles_by_code=profiles_by_code,
            intraday_cache_only=True,
        )
    with timed("build_profit_trend_week", timings):
        build_profit_trend(
            profit_range="week",
            snapshots=history_rows,
            holdings=live_holdings,
            profiles_by_code=profiles_by_code,
            intraday_cache_only=True,
        )
    with timed("build_calendar_month", timings):
        build_calendar_month(
            year=2026,
            month=6,
            snapshots=history_rows,
            holdings=live_holdings,
        )
    with timed("build_risk_metrics", timings):
        build_risk_metrics_payload(history_rows, holdings_models)
    with timed("build_daily_top5", timings):
        build_daily_top5(live_holdings)

    print("\n=== step breakdown ===")
    for key, value in timings.items():
        print(f"  {key}: {value}s")
    print(f"  snapshot_count: {len(history_rows)}")
    print(f"  holdings_count: {len(live_holdings)}")


if __name__ == "__main__":
    from app.request_context import reset_request_user_id, set_request_user_id

    token = set_request_user_id(1)
    try:
        profile_internals()
        for r in ("today", "week", "month"):
            profile_range(r)
    finally:
        reset_request_user_id(token)
