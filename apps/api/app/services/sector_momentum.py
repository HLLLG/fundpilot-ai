from __future__ import annotations

from app.models import Holding


def build_sector_momentum_context(holding: Holding, nav_trend: dict | None) -> dict | None:
    """Heuristic short-term pattern from nav series + sector move (for LLM, not trading)."""
    sector_move = holding.sector_return_percent
    if nav_trend is None and sector_move is None:
        return None

    recent = (nav_trend or {}).get("recent_nav_series") or []
    daily_changes = _daily_nav_changes(recent)
    pattern = _classify_pattern(daily_changes, sector_move)

    return {
        "sector_return_percent": sector_move,
        "recent_nav_daily_changes_percent": daily_changes[-3:],
        "pattern_label": pattern["label"],
        "pattern_hint": pattern["hint"],
        "reversal_risk": pattern["reversal_risk"],
    }


def _daily_nav_changes(series: list[dict]) -> list[float]:
    changes: list[float] = []
    for index in range(1, len(series)):
        prev = series[index - 1].get("nav")
        curr = series[index].get("nav")
        if not prev or not curr or float(prev) <= 0:
            continue
        changes.append(round((float(curr) / float(prev) - 1) * 100, 2))
    return changes


def _classify_pattern(
    daily_changes: list[float],
    sector_move: float | None,
) -> dict:
    if len(daily_changes) >= 2:
        prev_day, last_day = daily_changes[-2], daily_changes[-1]
        if prev_day >= 1.0 and last_day <= -0.8:
            return {
                "label": "two_day_reversal_down",
                "hint": "近2日净值先涨后跌，存在短线回吐；追涨需警惕次日延续调整。",
                "reversal_risk": "high",
            }
        if prev_day <= -1.0 and last_day >= 0.8:
            return {
                "label": "two_day_reversal_up",
                "hint": "近2日净值先跌后涨，短线反弹中；需区分反弹与趋势反转。",
                "reversal_risk": "medium",
            }

    if sector_move is not None and sector_move >= 3.0:
        if daily_changes and daily_changes[-1] >= 1.5:
            return {
                "label": "sector_fund_same_day_strong",
                "hint": "板块与基金同日偏强，短线动能足但追高风险上升。",
                "reversal_risk": "medium",
            }
        return {
            "label": "sector_hot_fund_lagging",
            "hint": "板块强势但基金涨幅未必同步，注意滞后补涨或估值偏差。",
            "reversal_risk": "low",
        }

    if sector_move is not None and sector_move <= -2.0:
        return {
            "label": "sector_weak",
            "hint": "板块当日偏弱，短线加仓胜率通常不高。",
            "reversal_risk": "medium",
        }

    return {
        "label": "neutral",
        "hint": "短线动能不明显，宜观察或结合新闻催化。",
        "reversal_risk": "low",
    }
