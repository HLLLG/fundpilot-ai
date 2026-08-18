from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.models import Holding
from app.services.sector_signal_context import (
    build_signal_backtest_context,
    sector_labels_from_holdings,
)


def resolve_signal_guard_policy(
    holdings: list[Holding] | None = None,
    *,
    sector_labels: list[str] | None = None,
    backtest_days: int | None = None,
    fetch_series=None,
) -> dict[str, Any]:
    """按板块信号回测的历史命中率决定守卫收紧/放松（纯数据驱动）。

    2026-08 决策风格收敛：原「日报复盘 Prompt 调参」（tactical 专属、默认禁用且
    口径未重建）已随战术风格一起删除；保留的是与风格无关的回测证据——涨后回吐/
    冲高回落两条信号命中率低于随机基准时放松对应门禁，避免用无效信号拦决策。
    """
    settings = get_settings()
    labels = sector_labels or (sector_labels_from_holdings(holdings or []) if holdings else [])
    days = backtest_days or settings.sector_signal_backtest_days
    min_triggers = settings.sector_signal_backtest_min_triggers

    backtest = build_signal_backtest_context(
        labels,
        lookback_days=days,
        fetch_series=fetch_series,
    )

    reversal = (backtest.get("by_rule") or {}).get("reversal_down") or {}
    pullback = (backtest.get("by_rule") or {}).get("intraday_pullback") or {}

    enforce_reversal = True
    enforce_pullback = True
    reasons: list[str] = []
    hints: list[str] = []

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

    reason = " ".join(reasons) if reasons else None

    return {
        "enforce_reversal_block": enforce_reversal,
        "enforce_pullback_block": enforce_pullback,
        "reason": reason,
        "hints": list(dict.fromkeys(hints)),
        "stats": {
            "backtest": {
                "lookback_days": days,
                "reversal_down": reversal,
                "intraday_pullback": pullback,
            },
        },
        "backtest_summary_lines": backtest.get("summary_lines") or [],
        "backtest_has_data": backtest.get("has_data"),
    }
