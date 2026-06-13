from __future__ import annotations

from typing import Any


def discovery_report_to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# {report.get('title', '基金推荐报告')}",
        "",
        f"**生成时间：** {report.get('created_at', '')}",
        "",
        "## 摘要",
        "",
        str(report.get("summary") or ""),
        "",
    ]
    market_view = report.get("market_view")
    if market_view:
        lines.extend(["## 市场观点", "", str(market_view), ""])

    target = report.get("target_sectors") or []
    if target:
        lines.extend(["**扫描板块：** " + "、".join(target), ""])

    lines.extend(["", "## 推荐基金", ""])
    for index, rec in enumerate(report.get("recommendations") or [], start=1):
        lines.append(f"### {index}. [{rec.get('fund_code')}] {rec.get('fund_name')}")
        lines.append("")
        lines.append(f"- **板块：** {rec.get('sector_name', '')}")
        lines.append(f"- **动作：** {rec.get('action', '')}")
        lines.append(f"- **持有期：** {rec.get('hold_horizon', '')}")
        lines.append(f"- **置信度：** {rec.get('confidence', '')}")
        if rec.get("suggested_amount_yuan") is not None:
            lines.append(f"- **示意金额：** {rec.get('suggested_amount_yuan')} 元")
        if rec.get("amount_note"):
            lines.append(f"- **金额说明：** {rec.get('amount_note')}")
        for point in rec.get("points") or []:
            lines.append(f"- {point}")
        risks = rec.get("risks") or []
        if risks:
            lines.append("")
            lines.append("**风险：**")
            for risk in risks:
                lines.append(f"- {risk}")
        lines.append("")

    caveats = report.get("caveats") or []
    if caveats:
        lines.extend(["## 风险提示", ""])
        for caveat in caveats:
            lines.append(f"- {caveat}")

    return "\n".join(lines).strip() + "\n"
