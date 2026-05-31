from __future__ import annotations

from typing import Any


def report_chat_to_markdown(report: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    title = report.get("title", "基金操作日报")
    created_at = report.get("created_at", "")
    lines = [
        f"# 报告追问记录",
        "",
        f"- 关联日报：{title}",
        f"- 日报时间：{created_at}",
        f"- 报告 ID：`{report.get('id', '')}`",
        "",
    ]

    if not messages:
        lines.append("_（暂无对话）_")
        return "\n".join(lines)

    lines.append("## 对话")
    lines.append("")
    for message in messages:
        role = message.get("role", "")
        label = "用户" if role == "user" else "助手"
        timestamp = message.get("created_at", "")
        lines.append(f"### {label}")
        if timestamp:
            lines.append(f"_{timestamp}_")
        lines.append("")
        lines.append(str(message.get("content", "")).strip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
