from __future__ import annotations

from typing import Any


def _holding_key(holding: dict[str, Any]) -> str:
    code = str(holding.get("fund_code", "")).strip()
    if code and code != "000000":
        return code
    return str(holding.get("fund_name", "")).strip()


def diff_reports(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    current_map = {_holding_key(item): item for item in current.get("holdings", [])}
    previous_map = {_holding_key(item): item for item in previous.get("holdings", [])}

    holding_changes: list[dict[str, Any]] = []
    for key in sorted(set(current_map) | set(previous_map)):
        cur = current_map.get(key)
        prev = previous_map.get(key)
        if cur and not prev:
            holding_changes.append(
                {
                    "type": "added",
                    "fund_code": cur.get("fund_code"),
                    "fund_name": cur.get("fund_name"),
                    "holding_amount": cur.get("holding_amount"),
                    "return_percent": cur.get("return_percent"),
                }
            )
            continue
        if prev and not cur:
            holding_changes.append(
                {
                    "type": "removed",
                    "fund_code": prev.get("fund_code"),
                    "fund_name": prev.get("fund_name"),
                    "holding_amount": prev.get("holding_amount"),
                    "return_percent": prev.get("return_percent"),
                }
            )
            continue
        if not cur or not prev:
            continue

        amount_delta = float(cur.get("holding_amount") or 0) - float(prev.get("holding_amount") or 0)
        return_delta = float(cur.get("return_percent") or 0) - float(prev.get("return_percent") or 0)
        if abs(amount_delta) < 0.01 and abs(return_delta) < 0.01:
            continue
        holding_changes.append(
            {
                "type": "changed",
                "fund_code": cur.get("fund_code"),
                "fund_name": cur.get("fund_name"),
                "holding_amount": cur.get("holding_amount"),
                "return_percent": cur.get("return_percent"),
                "previous_holding_amount": prev.get("holding_amount"),
                "previous_return_percent": prev.get("return_percent"),
                "holding_amount_delta": round(amount_delta, 2),
                "return_percent_delta": round(return_delta, 2),
            }
        )

    current_recs = {
        item.get("fund_code"): item.get("action")
        for item in current.get("fund_recommendations", [])
        if item.get("fund_code")
    }
    previous_recs = {
        item.get("fund_code"): item.get("action")
        for item in previous.get("fund_recommendations", [])
        if item.get("fund_code")
    }
    recommendation_changes: list[dict[str, Any]] = []
    for code in sorted(set(current_recs) | set(previous_recs)):
        cur_action = current_recs.get(code)
        prev_action = previous_recs.get(code)
        if cur_action != prev_action:
            recommendation_changes.append(
                {
                    "fund_code": code,
                    "previous_action": prev_action,
                    "current_action": cur_action,
                }
            )

    cur_risk = current.get("risk", {})
    prev_risk = previous.get("risk", {})
    cur_weighted = float(cur_risk.get("weighted_return_percent") or 0)
    prev_weighted = float(prev_risk.get("weighted_return_percent") or 0)

    return {
        "previous_report_id": previous.get("id"),
        "previous_title": previous.get("title"),
        "previous_created_at": previous.get("created_at"),
        "risk_level_changed": cur_risk.get("level") != prev_risk.get("level"),
        "previous_risk_level": prev_risk.get("level"),
        "current_risk_level": cur_risk.get("level"),
        "suggested_action_changed": cur_risk.get("suggested_action") != prev_risk.get("suggested_action"),
        "previous_suggested_action": prev_risk.get("suggested_action"),
        "current_suggested_action": cur_risk.get("suggested_action"),
        "weighted_return_delta": round(cur_weighted - prev_weighted, 2),
        "holding_changes": holding_changes,
        "recommendation_changes": recommendation_changes,
    }
