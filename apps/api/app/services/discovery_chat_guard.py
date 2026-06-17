from __future__ import annotations

import re
from typing import Any

_FUND_CODE_PATTERN = re.compile(r"\b(\d{6})\b")


def allowed_fund_codes(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """候选池 + 正式推荐中的基金代码（追问唯一允许引用）。"""
    by_code: dict[str, dict[str, Any]] = {}
    for item in report.get("candidate_pool") or []:
        code = str(item.get("fund_code", "")).strip().zfill(6)
        if code.isdigit() and len(code) == 6:
            by_code[code] = dict(item)
    for rec in report.get("recommendations") or []:
        code = str(rec.get("fund_code", "")).strip().zfill(6)
        if code.isdigit() and len(code) == 6:
            by_code[code] = {
                "fund_code": code,
                "fund_name": rec.get("fund_name"),
                "sector_label": rec.get("sector_name"),
            }
    return by_code


def pool_by_sector(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in allowed_fund_codes(report).values():
        sector = str(item.get("sector_label") or item.get("sector_name") or "").strip()
        if not sector:
            continue
        grouped.setdefault(sector, []).append(item)
    return grouped


def format_candidate_pool_whitelist(report: dict[str, Any]) -> str:
    rows = sorted(
        allowed_fund_codes(report).values(),
        key=lambda item: (
            str(item.get("sector_label") or item.get("sector_name") or ""),
            str(item.get("fund_code", "")),
        ),
    )
    lines = [
        "## 候选基金池（追问时唯一允许引用的基金代码）",
        "",
        "| 代码 | 名称 | 板块 |",
        "| --- | --- | --- |",
    ]
    for item in rows:
        code = str(item.get("fund_code", "")).zfill(6)
        name = str(item.get("fund_name", "")).strip() or "—"
        sector = str(item.get("sector_label") or item.get("sector_name") or "").strip() or "—"
        lines.append(f"| {code} | {name} | {sector} |")
    lines.extend(
        [
            "",
            "**硬性约束：** 提及具体基金时必须且只能使用上表中的代码与名称；",
            "禁止编造表外代码（含 ETF 场内代码如 512660、159999 等）。",
            "若用户追问的板块在上表中，只能从该板块对应行中选择。",
        ]
    )
    return "\n".join(lines)


def sanitize_discovery_chat_fund_codes(
    content: str,
    report: dict[str, Any],
) -> tuple[str, list[str]]:
    """将追问回复中不在候选池的 6 位基金代码替换为候选池内同板块基金或警示。"""
    allowed = allowed_fund_codes(report)
    if not allowed:
        return content, []

    by_sector = pool_by_sector(report)
    sectors = sorted(by_sector.keys(), key=len, reverse=True)
    notes: list[str] = []
    output_lines: list[str] = []

    for line in content.splitlines():
        invalid_codes = [code for code in _FUND_CODE_PATTERN.findall(line) if code not in allowed]
        if not invalid_codes:
            output_lines.append(line)
            continue

        new_line = line
        sector = _detect_sector_in_text(line, sectors)
        for bad_code in dict.fromkeys(invalid_codes):
            replacement = _replacement_for_invalid_code(
                bad_code,
                sector=sector,
                by_sector=by_sector,
            )
            new_line = _replace_code_reference(new_line, bad_code, replacement)
            if replacement.startswith("（基金代码"):
                notes.append(f"已标注不在候选池的代码 {bad_code}")
            else:
                notes.append(f"已将不在候选池的 {bad_code} 替换为候选池基金")

        output_lines.append(new_line)

    return "\n".join(output_lines), notes


def _detect_sector_in_text(text: str, sectors: list[str]) -> str | None:
    for sector in sectors:
        if sector and sector in text:
            return sector
    return None


def _format_pool_entry(item: dict[str, Any]) -> str:
    code = str(item.get("fund_code", "")).zfill(6)
    name = str(item.get("fund_name", "")).strip()
    if name:
        return f"{code}（{name}）"
    return code


def _format_pool_alternatives(sector: str, by_sector: dict[str, list[dict[str, Any]]]) -> str:
    items = by_sector.get(sector) or []
    if not items:
        return ""
    return "、".join(_format_pool_entry(item) for item in items[:3])


def _replacement_for_invalid_code(
    bad_code: str,
    *,
    sector: str | None,
    by_sector: dict[str, list[dict[str, Any]]],
) -> str:
    if sector:
        alts = _format_pool_alternatives(sector, by_sector)
        if alts:
            return alts
    return "（该代码不在本次候选池，请仅参考候选池列表）"


def _replace_code_reference(line: str, bad_code: str, replacement: str) -> str:
    pattern = rf"\b{re.escape(bad_code)}\b(?:（[^）]*）)?"
    return re.sub(pattern, replacement, line, count=1)
