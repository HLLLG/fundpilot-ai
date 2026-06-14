from __future__ import annotations

from typing import Any


def diff_discovery_reports(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    current_recs = {
        str(item.get("fund_code", "")).zfill(6): item
        for item in current.get("recommendations", [])
        if item.get("fund_code")
    }
    previous_recs = {
        str(item.get("fund_code", "")).zfill(6): item
        for item in previous.get("recommendations", [])
        if item.get("fund_code")
    }

    recommendation_changes: list[dict[str, Any]] = []
    for code in sorted(set(current_recs) | set(previous_recs)):
        cur = current_recs.get(code)
        prev = previous_recs.get(code)
        if cur and not prev:
            recommendation_changes.append(
                {
                    "type": "added",
                    "fund_code": code,
                    "fund_name": cur.get("fund_name"),
                    "action": cur.get("action"),
                    "suggested_amount_yuan": cur.get("suggested_amount_yuan"),
                }
            )
            continue
        if prev and not cur:
            recommendation_changes.append(
                {
                    "type": "removed",
                    "fund_code": code,
                    "fund_name": prev.get("fund_name"),
                    "action": prev.get("action"),
                }
            )
            continue
        if not cur or not prev:
            continue

        changes: dict[str, Any] = {
            "type": "changed",
            "fund_code": code,
            "fund_name": cur.get("fund_name") or prev.get("fund_name"),
        }
        if cur.get("action") != prev.get("action"):
            changes["action"] = cur.get("action")
            changes["previous_action"] = prev.get("action")
        if cur.get("suggested_amount_yuan") != prev.get("suggested_amount_yuan"):
            changes["suggested_amount_yuan"] = cur.get("suggested_amount_yuan")
            changes["previous_suggested_amount_yuan"] = prev.get("suggested_amount_yuan")
        if len(changes) > 3:
            recommendation_changes.append(changes)

    current_sectors = set(current.get("target_sectors") or [])
    previous_sectors = set(previous.get("target_sectors") or [])
    sector_changes = {
        "added": sorted(current_sectors - previous_sectors),
        "removed": sorted(previous_sectors - current_sectors),
    }

    return {
        "current_report_id": current.get("id"),
        "previous_report_id": previous.get("id"),
        "recommendation_changes": recommendation_changes,
        "sector_changes": sector_changes,
        "summary_changed": current.get("summary") != previous.get("summary"),
    }
