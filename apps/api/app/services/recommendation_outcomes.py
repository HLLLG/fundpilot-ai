from __future__ import annotations

from typing import Any

from app.models import FundRecommendation


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
        delta = (
            round(return_after - return_before, 2)
            if return_before is not None and return_after is not None
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
                "holding_return_delta": delta,
                "assessment": _assess_outcome(prev_rec.get("action", ""), delta),
            }
        )

    portfolio_delta = None
    if previous.get("risk") and current.get("risk"):
        portfolio_delta = round(
            float(current["risk"]["weighted_return_percent"])
            - float(previous["risk"]["weighted_return_percent"]),
            2,
        )

    return {
        "has_baseline": True,
        "previous_report_id": previous.get("id"),
        "previous_created_at": previous.get("created_at"),
        "portfolio_return_delta": portfolio_delta,
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


def _assess_outcome(previous_action: str, delta: float | None) -> str:
    if delta is None:
        return "数据不足"
    action = previous_action or ""
    if any(token in action for token in ("减仓", "复核", "暂停")):
        if delta <= 0:
            return "保守建议与后续走势一致（持有收益未继续恶化）"
        return "保守建议后持有收益回升，可结合当时新闻复核是否过早"
    if "加仓" in action or "定投" in action:
        if delta >= 0:
            return "加仓类建议后持有收益改善或企稳"
        return "加仓类建议后持有收益走弱，宜缩小下次额度"
    if delta >= 0:
        return "观望后持有收益改善"
    return "观望后持有收益走弱"
