from __future__ import annotations

from typing import Any


_ACTION_LABEL = {
    "watch": "观察",
    "pause_add": "暂停加仓",
    "staggered_add": "分批加仓",
    "risk_review": "减仓/风控复核",
}


def report_to_markdown(report: dict[str, Any]) -> str:
    risk = report.get("risk", {})
    lines = [
        f"# {report.get('title', '基金操作日报')}",
        "",
        f"- 生成时间：{report.get('created_at', '')}",
        f"- 风险等级：**{risk.get('level', '')}**",
        f"- 建议动作：{_ACTION_LABEL.get(risk.get('suggested_action', ''), risk.get('suggested_action', ''))}",
        f"- 加权收益率：{risk.get('weighted_return_percent', 0)}%",
        f"- 提供方：{report.get('provider', '')}",
        "",
        "## 摘要",
        "",
        str(report.get("summary", "")),
        "",
    ]

    portfolio_recs = report.get("recommendations") or []
    if portfolio_recs:
        lines.extend(["## 组合建议", ""])
        for item in portfolio_recs:
            lines.append(f"- {item}")
        lines.append("")

    facts = report.get("analysis_facts") or {}
    evidence_by_code = {
        str(row.get("fund_code")): row.get("evidence")
        for row in (facts.get("holdings") or [])
        if row.get("evidence")
    }
    overview = facts.get("evidence_overview") or {}
    if overview.get("available") and overview.get("summary"):
        lines.extend([
            "## 组合量化背书",
            "",
            f"- {overview['summary']}",
            f"- 中/高背书市值占比：{overview.get('backed_weight_percent', 0)}%",
            "",
        ])

    fund_recs = report.get("fund_recommendations") or []
    if fund_recs:
        lines.extend(["## 逐基金建议", ""])
        for item in fund_recs:
            lines.append(f"### {item.get('fund_code')} · {item.get('fund_name')}")
            lines.append("")
            lines.append(f"- **操作**：{item.get('action', '')}")
            if item.get("confidence"):
                lines.append(f"- **置信度**：{item['confidence']}")
            if item.get("hold_horizon"):
                lines.append(f"- **持有/观察窗口**：{item['hold_horizon']}")
            position_percent = item.get("suggested_position_change_percent")
            if isinstance(position_percent, (int, float)) and position_percent != 0:
                verb = "加仓" if position_percent > 0 else "减仓"
                lines.append(
                    f"- **建议调整**：相对当前持仓{verb} {_format_percent(position_percent)}%"
                )
                estimated_amount = item.get("estimated_position_change_amount_yuan")
                if isinstance(estimated_amount, (int, float)) and estimated_amount > 0:
                    lines.append(
                        f"- **估算调整金额**：约 {float(estimated_amount):,.0f} 元"
                        "（按报告生成时持仓估值折算）"
                    )
            elif item.get("amount_note"):
                lines.append(f"- **金额**：{item['amount_note']}")
            elif item.get("amount_yuan") is not None:
                lines.append(f"- **金额**：约 {item['amount_yuan']} 元")
            for point in item.get("points") or []:
                lines.append(f"- {point}")
            evidence = evidence_by_code.get(str(item.get("fund_code")))
            if evidence:
                composite = (evidence.get("composite") or {}).get("level", "")
                summary = evidence.get("summary", "")
                lines.append(f"- **量化依据**（综合置信{composite}）：{summary}")
            if item.get("decision_path"):
                lines.append(f"- **决策路径**：{item['decision_path']}")
            _append_named_list(lines, "板块依据", item.get("sector_evidence"))
            _append_named_list(lines, "基金依据", item.get("fund_evidence"))
            _append_named_list(lines, "校验备注", item.get("validation_notes"))
            item_risks = item.get("risks") or []
            if item_risks:
                lines.append("")
                lines.append("**风险：**")
                for item_risk in item_risks:
                    lines.append(f"- {item_risk}")
            lines.append("")

    alerts = [alert.get("message") for alert in risk.get("alerts") or [] if alert.get("message")]
    caveats = report.get("caveats") or []
    if alerts or caveats:
        lines.extend(["## 风险提醒", ""])
        for item in [*alerts, *caveats]:
            lines.append(f"- {item}")
        lines.append("")

    briefs = report.get("topic_briefs") or []
    if briefs:
        lines.extend(["## 主题要闻摘要", ""])
        for brief in briefs:
            topic = brief.get("topic", "")
            summary = brief.get("summary", "")
            lines.append(f"### {topic}")
            lines.append("")
            if summary:
                lines.append(summary)
                lines.append("")
            for point in brief.get("points") or []:
                sentiment = point.get("sentiment", "neutral")
                headline = point.get("headline", "")
                lines.append(f"- [{sentiment}] {headline}")
            lines.append("")

    news = report.get("market_news") or []
    if news:
        lines.extend(["## 相关新闻（原文出处）", ""])
        for item in news:
            title = item.get("title", "")
            url = item.get("url")
            topic = item.get("topic", "")
            if url:
                lines.append(f"- [{topic}] [{title}]({url})")
            else:
                lines.append(f"- [{topic}] {title}")
        lines.append("")

    lines.append("---")
    lines.append("*仅供个人投研辅助，不构成投资建议。*")
    return "\n".join(lines)


def _format_percent(value: int | float) -> str:
    percent = abs(float(value))
    if abs(percent - round(percent)) < 1e-9:
        return f"{percent:.0f}"
    return f"{percent:.1f}"


def _append_named_list(lines: list[str], title: str, items: object) -> None:
    if not isinstance(items, list) or not items:
        return
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    if not cleaned:
        return
    lines.append(f"- **{title}**：" + "；".join(cleaned))
