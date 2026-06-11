from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.models import Holding
from app.services.prompt_tuning import resolve_accuracy_tuning
from app.services.sector_signal_context import (
    build_signal_backtest_context,
    sector_labels_from_holdings,
)


def resolve_signal_guard_policy(
    holdings: list[Holding] | None = None,
    *,
    sector_labels: list[str] | None = None,
    lookback_reports: int | None = None,
    backtest_days: int | None = None,
    fetch_series=None,
) -> dict[str, Any]:
    """合并日报复盘 + 板块回测，输出守卫收紧/放松策略。"""
    settings = get_settings()
    labels = sector_labels or (sector_labels_from_holdings(holdings or []) if holdings else [])
    reports_window = lookback_reports or settings.tactical_prompt_tuning_lookback_reports
    days = backtest_days or settings.sector_signal_backtest_days
    min_triggers = settings.sector_signal_backtest_min_triggers

    accuracy = resolve_accuracy_tuning(lookback_reports=reports_window)
    backtest = build_signal_backtest_context(
        labels,
        lookback_days=days,
        fetch_series=fetch_series,
    )

    reversal = (backtest.get("by_rule") or {}).get("reversal_down") or {}
    pullback = (backtest.get("by_rule") or {}).get("intraday_pullback") or {}

    enforce_reversal = True
    enforce_pullback = True
    tighten_tactical = bool(accuracy.get("tighten_tactical"))
    reasons: list[str] = []
    hints: list[str] = list(accuracy.get("hints") or [])

    rev_triggers = int(reversal.get("trigger_count") or 0)
    rev_hit = reversal.get("hit_rate_percent")
    if rev_triggers >= min_triggers and rev_hit is not None:
        if rev_hit < 52:
            enforce_reversal = False
            reasons.append(
                f"板块涨后回吐规则近 {days} 日命中率 {rev_hit}%（{rev_triggers} 次），"
                "低于随机基准，守卫已放松该信号。"
            )
        elif rev_hit >= 58:
            tighten_tactical = True
            reasons.append(
                f"板块涨后回吐规则近 {days} 日命中率 {rev_hit}%（{rev_triggers} 次），"
                "高于随机基准，战术模式将更严格限制追涨。"
            )
            hints.append(
                "板块历史回测：涨后回吐后 T+1 偏弱命中率较高，回吐/冲高回落场景禁止加仓。"
            )

    pull_triggers = int(pullback.get("trigger_count") or 0)
    pull_hit = pullback.get("hit_rate_percent")
    if pull_triggers >= min_triggers and pull_hit is not None:
        if pull_hit < 50:
            enforce_pullback = False
            reasons.append(
                f"冲高回落代理规则近 {days} 日命中率 {pull_hit}%（{pull_triggers} 次），"
                "守卫已放松该信号。"
            )
        elif pull_hit >= 58:
            enforce_pullback = True
            hints.append(
                "板块历史回测：冲高回落后 T+1 延续调整命中率较高，盘中冲高回落宜观察。"
            )

    reason = accuracy.get("reason")
    if reasons:
        reason = " ".join([part for part in [reason, *reasons] if part])

    return {
        "tighten_tactical": tighten_tactical,
        "enforce_reversal_block": enforce_reversal,
        "enforce_pullback_block": enforce_pullback,
        "reason": reason,
        "hints": list(dict.fromkeys(hints)),
        "stats": {
            "accuracy": accuracy.get("stats") or {},
            "backtest": {
                "lookback_days": days,
                "reversal_down": reversal,
                "intraday_pullback": pullback,
            },
        },
        "backtest_summary_lines": backtest.get("summary_lines") or [],
        "backtest_has_data": backtest.get("has_data"),
    }
