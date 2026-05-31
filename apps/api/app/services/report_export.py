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

    fund_recs = report.get("fund_recommendations") or []
    if fund_recs:
        lines.extend(["## 逐基金建议", ""])
        for item in fund_recs:
            lines.append(f"### {item.get('fund_code')} · {item.get('fund_name')}")
            lines.append("")
            lines.append(f"- **操作**：{item.get('action', '')}")
            if item.get("amount_note"):
                lines.append(f"- **金额**：{item['amount_note']}")
            elif item.get("amount_yuan") is not None:
                lines.append(f"- **金额**：约 {item['amount_yuan']} 元")
            for point in item.get("points") or []:
                lines.append(f"- {point}")
            lines.append("")

    alerts = [alert.get("message") for alert in risk.get("alerts") or [] if alert.get("message")]
    caveats = report.get("caveats") or []
    if alerts or caveats:
        lines.extend(["## 风险提醒", ""])
        for item in [*alerts, *caveats]:
            lines.append(f"- {item}")
        lines.append("")

    news = report.get("market_news") or []
    if news:
        lines.extend(["## 相关新闻", ""])
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
