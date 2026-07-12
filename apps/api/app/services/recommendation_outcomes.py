from __future__ import annotations

from typing import Any, Iterable

from app.services.akshare_subprocess import fetch_fund_nav_history
from app.services.benchmark_fee_evaluation import BenchmarkFetcher, default_benchmark_fetcher
from app.services.recommendation_forward_evaluation import (
    DEFAULT_HORIZONS,
    NavFetcher,
    evaluate_report_recommendations,
    normalize_horizons,
)
from app.services.trade_calendar_cache import get_trade_date_set


def build_recommendation_outcomes(
    current: dict[str, Any],
    previous: dict[str, Any] | None = None,
    *,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    fetch_nav: NavFetcher = fetch_fund_nav_history,
    trade_dates: frozenset[str] | None = None,
    fetch_benchmark: BenchmarkFetcher | None = default_benchmark_fetcher,
    formal_v2_only: bool = False,
    legacy_reference_only: bool = False,
) -> dict[str, Any]:
    """Evaluate this report's recommendations against exact forward NAV dates.

    ``previous`` remains in the signature for API compatibility only. It is never
    used as a performance label: adjacent reports can contain changed holdings,
    cash flows, and unrelated same-day return estimates.
    """
    result = evaluate_report_recommendations(
        current,
        horizons=horizons,
        fetch_nav=fetch_nav,
        trade_dates=trade_dates if trade_dates is not None else get_trade_date_set(),
        fetch_benchmark=fetch_benchmark,
        formal_v2_only=formal_v2_only,
        legacy_reference_only=legacy_reference_only,
    )
    if previous is not None:
        result["legacy_previous_report_id"] = previous.get("id")
    return result


def build_weekly_recommendation_outcomes(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    *,
    baseline_days: int = 7,
    fetch_nav: NavFetcher = fetch_fund_nav_history,
    trade_dates: frozenset[str] | None = None,
    fetch_benchmark: BenchmarkFetcher | None = default_benchmark_fetcher,
) -> dict[str, Any]:
    """Compatibility wrapper for the historical ``outcomes-weekly`` endpoint.

    The requested number now means an exact T+N NAV horizon. ``baseline`` is
    retained only so existing callers do not break; no prior report is used.
    """
    horizon = normalize_horizons((baseline_days,))[0]
    result = build_recommendation_outcomes(
        current,
        baseline,
        horizons=(horizon,),
        fetch_nav=fetch_nav,
        trade_dates=trade_dates,
        fetch_benchmark=fetch_benchmark,
    )
    stats = result["by_horizon"][f"T+{horizon}"]
    result.update(
        {
            "baseline_days": horizon,
            "baseline_report_id": baseline.get("id") if baseline else None,
            "baseline_created_at": baseline.get("created_at") if baseline else None,
            "summary": result.get("message"),
            "hit_count": stats["hit_count"],
            "miss_count": stats["miss_count"],
            # Legacy shape, but the obsolete adjacent-day reversal heuristic is
            # deliberately no longer populated from unrelated report returns.
            "reversal_stats": {
                "reversal_count": 0,
                "up_then_down_count": 0,
                "up_then_down_conservative_aligned": 0,
                "up_then_down_aggressive_miss": 0,
                "summary_line": "旧相邻日报涨后回吐口径已停用；请查看精确 T+N 净值结果。",
            },
        }
    )
    return result
