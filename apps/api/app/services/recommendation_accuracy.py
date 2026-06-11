from __future__ import annotations

from typing import Any

from app.database import list_reports
from app.services.recommendation_outcomes import build_recommendation_outcomes


def build_recommendation_accuracy(*, limit_reports: int = 30) -> dict[str, Any]:
    reports = list_reports()[:limit_reports]
    if len(reports) < 2:
        return {
            "has_enough_data": False,
            "message": "历史日报不足 2 份，暂无法统计建议准确率。",
            "paired_days": 0,
            "by_style": {},
        }

    buckets: dict[str, dict[str, Any]] = {}

    for index in range(len(reports) - 1):
        current = reports[index]
        previous = reports[index + 1]
        style = _decision_style(previous)
        bucket = buckets.setdefault(
            style,
            {
                "decision_style": style,
                "paired_count": 0,
                "hit_count": 0,
                "miss_count": 0,
                "reversal": {
                    "up_then_down_count": 0,
                    "up_then_down_conservative_aligned": 0,
                    "up_then_down_aggressive_miss": 0,
                },
                "items": [],
            },
        )
        bucket["paired_count"] += 1

        outcome = build_recommendation_outcomes(current, previous)
        for item in outcome.get("items") or []:
            assessment = str(item.get("assessment") or "")
            if any(token in assessment for token in ("一致", "改善", "保留", "大体一致")):
                bucket["hit_count"] += 1
            elif any(token in assessment for token in ("走弱", "过保守", "承压")):
                bucket["miss_count"] += 1

            if item.get("reversal_scenario") == "up_then_down":
                rev = bucket["reversal"]
                rev["up_then_down_count"] += 1
                action = str(item.get("previous_action") or "")
                if any(token in action for token in ("减仓", "复核", "暂停", "观察")):
                    rev["up_then_down_conservative_aligned"] += 1
                elif "加仓" in action:
                    rev["up_then_down_aggressive_miss"] += 1

            if len(bucket["items"]) < 8:
                bucket["items"].append(
                    {
                        "fund_code": item.get("fund_code"),
                        "fund_name": item.get("fund_name"),
                        "previous_action": item.get("previous_action"),
                        "assessment": assessment,
                        "reversal_scenario": item.get("reversal_scenario"),
                    }
                )

    for bucket in buckets.values():
        paired = bucket["paired_count"] or 1
        bucket["hit_rate_percent"] = round(bucket["hit_count"] / paired * 100, 1)
        rev = bucket["reversal"]
        utd = rev["up_then_down_count"] or 0
        rev["aggressive_miss_rate_percent"] = (
            round(rev["up_then_down_aggressive_miss"] / utd * 100, 1) if utd else None
        )

    return {
        "has_enough_data": True,
        "paired_days": len(reports) - 1,
        "report_count": len(reports),
        "by_style": buckets,
        "summary_lines": _summary_lines(buckets),
    }


def _decision_style(report: dict) -> str:
    facts = report.get("analysis_facts") or {}
    portfolio = facts.get("portfolio") or {}
    style = portfolio.get("decision_style")
    if style in {"tactical", "conservative"}:
        return style
    profile = report.get("profile") or {}
    if profile.get("decision_style") in {"tactical", "conservative"}:
        return profile["decision_style"]
    return "conservative"


def _summary_lines(buckets: dict[str, dict]) -> list[str]:
    lines: list[str] = []
    for style, bucket in buckets.items():
        label = "战术短线" if style == "tactical" else "稳健"
        lines.append(
            f"{label}：{bucket['paired_count']} 组相邻日报，"
            f"方向吻合约 {bucket['hit_rate_percent']}%（{bucket['hit_count']}/{bucket['paired_count']}）。"
        )
        rev = bucket.get("reversal") or {}
        if rev.get("up_then_down_count"):
            lines.append(
                f"{label} 涨后回吐 {rev['up_then_down_count']} 次，"
                f"其中追涨加仓 {rev['up_then_down_aggressive_miss']} 次。"
            )
    return lines
