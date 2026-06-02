from __future__ import annotations

from typing import Any

from app.services.holding_metrics import compute_estimated_daily_return_percent


def build_recommendation_outcomes(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    if previous is None:
        return {
            "has_baseline": False,
            "message": "暂无上一份日报，无法复盘建议效果。",
            "items": [],
        }

    prev_holdings = {
        item["fund_code"]: item
        for item in previous.get("holdings", [])
        if item.get("fund_code")
    }
    curr_holdings = {
        item["fund_code"]: item
        for item in current.get("holdings", [])
        if item.get("fund_code")
    }
    prev_recs = _recs_by_code(previous.get("fund_recommendations", []))
    items: list[dict[str, Any]] = []

    for rec in current.get("fund_recommendations", []):
        code = rec.get("fund_code")
        if not code:
            continue
        prev_rec = prev_recs.get(code)
        if prev_rec is None:
            continue
        before = prev_holdings.get(code)
        after = curr_holdings.get(code)
        if before is None or after is None:
            continue

        return_before = _holding_return(before)
        return_after = _holding_return(after)
        holding_delta = (
            round(return_after - return_before, 2)
            if return_before is not None and return_after is not None
            else None
        )

        daily_before = _daily_return(before)
        daily_after = _daily_return(after)
        daily_delta = (
            round(daily_after - daily_before, 2)
            if daily_before is not None and daily_after is not None
            else None
        )

        items.append(
            {
                "fund_code": code,
                "fund_name": rec.get("fund_name") or before.get("fund_name"),
                "previous_action": prev_rec.get("action"),
                "current_action": rec.get("action"),
                "holding_return_before": return_before,
                "holding_return_after": return_after,
                "holding_return_delta": holding_delta,
                "daily_return_before": daily_before,
                "daily_return_after": daily_after,
                "daily_return_delta": daily_delta,
                "assessment": _assess_outcome(
                    prev_rec.get("action", ""),
                    holding_delta,
                    daily_delta,
                ),
            }
        )

    portfolio_delta = None
    if previous.get("risk") and current.get("risk"):
        portfolio_delta = round(
            float(current["risk"]["weighted_return_percent"])
            - float(previous["risk"]["weighted_return_percent"]),
            2,
        )

    prev_trend = (previous.get("analysis_facts") or {}).get("portfolio_trend") or {}
    curr_trend = (current.get("analysis_facts") or {}).get("portfolio_trend") or {}

    return {
        "has_baseline": True,
        "previous_report_id": previous.get("id"),
        "previous_created_at": previous.get("created_at"),
        "portfolio_return_delta": portfolio_delta,
        "portfolio_trend_summary": curr_trend.get("summary_line"),
        "portfolio_assets_delta_percent": curr_trend.get("assets_delta_percent"),
        "items": items,
    }


def _recs_by_code(recommendations: list) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for entry in recommendations:
        if not isinstance(entry, dict):
            continue
        code = str(entry.get("fund_code", "")).strip()
        if code:
            mapping[code] = entry
    return mapping


def _holding_return(holding: dict) -> float | None:
    value = holding.get("holding_return_percent")
    if value is None:
        value = holding.get("return_percent")
    if value is None:
        return None
    return float(value)


def _daily_return(holding: dict) -> float | None:
    if holding.get("daily_return_percent") is not None:
        return float(holding["daily_return_percent"])
    estimated = compute_estimated_daily_return_percent(_holding_like(holding))
    return estimated


def _holding_like(holding: dict) -> Any:
    from app.models import Holding

    return Holding(
        fund_code=str(holding.get("fund_code") or "000000"),
        fund_name=str(holding.get("fund_name") or ""),
        holding_amount=float(holding.get("holding_amount") or 0),
        return_percent=float(holding.get("return_percent") or 0),
        daily_return_percent=holding.get("daily_return_percent"),
        holding_return_percent=holding.get("holding_return_percent"),
        sector_return_percent=holding.get("sector_return_percent"),
    )


def _assess_outcome(
    previous_action: str,
    holding_delta: float | None,
    daily_delta: float | None,
) -> str:
    primary_delta = daily_delta if daily_delta is not None else holding_delta
    if primary_delta is None:
        return "数据不足，无法对比上一份建议后的涨跌"

    metric_label = "估算/实际当日涨跌" if daily_delta is not None else "持有收益率"
    action = previous_action or ""

    if any(token in action for token in ("减仓", "复核", "暂停")):
        if primary_delta <= 0:
            return (
                f"保守建议后{metric_label}未继续走弱（{primary_delta:+.2f}%），"
                "与控风险意图大体一致"
            )
        return (
            f"保守建议后{metric_label}回升（{primary_delta:+.2f}%），"
            "可结合当时要闻复核是否过保守"
        )
    if "加仓" in action or "定投" in action:
        if primary_delta >= 0:
            return (
                f"加仓类建议后{metric_label}改善或企稳（{primary_delta:+.2f}%），"
                "可保留类似节奏"
            )
        return (
            f"加仓类建议后{metric_label}走弱（{primary_delta:+.2f}%），"
            "宜缩小下次额度或等待板块企稳"
        )
    if primary_delta >= 0:
        return f"观望后{metric_label}改善（{primary_delta:+.2f}%）"
    return f"观望后{metric_label}走弱（{primary_delta:+.2f}%），可复盘是否应更早减仓"
