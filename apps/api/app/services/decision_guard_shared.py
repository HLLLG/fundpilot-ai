from __future__ import annotations

"""荐基 guard 与日报 guard 共用的文本处理 helper。

抽取自 discovery_guard.py（2026-06-30 P0.5 弱证据降级/结构化字段人话化），2026-07
日报升级时把与「决策证据文本」相关、跟 discovery 无强耦合的部分下沉到这里，供
recommendation_guard.py 复用同一套人话化/归一化逻辑，避免日报和荐基的措辞、字段
命名规则各写一套、后续维护时口径漂移。
"""

import re

_TRACK_LABELS = {
    "momentum": "顺势观察",
    "setup": "蓄势观察",
}

_PATTERN_LABELS = {
    "accumulation": "回调中有资金承接",
    "aligned_up": "上涨有资金配合",
    "distribution": "涨幅较快但资金流出",
    "flow_date_mismatch": "资金日期需核验",
    "flow_turning_positive": "资金开始转正",
    "multi_day_outflow_then_inflow": "资金由流出转回流",
    "price_flow_aligned_up": "上涨有资金配合",
    "weak_outflow": "资金偏弱",
}

# (pattern, replacement) 对，按顺序应用；日报/荐基各自可能有专属字段，
# 调用方可通过 extra_replacements 追加。
_BASE_REGEX_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        r"nav_trend\.distance_from_high_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?",
        "距离近期高点约 {0}%",
    ),
    (
        r"max_drawdown_1y_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?",
        "近1年最大回撤约 {abs0}%",
    ),
    (
        r"estimated_daily_return_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?",
        "今日涨跌约 {0}%",
    ),
    (
        r"distance_from_high_percent\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)%?",
        "距离近期高点约 {0}%",
    ),
    (
        r"heat_score\s*(?:=|为|约)?\s*([-+]?\d+(?:\.\d+)?)",
        "板块热度分 {0}",
    ),
)

_BASE_TEXT_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("sector_opportunities 得分", "系统方向得分"),
    ("sector_opportunities", "系统筛出的主方向"),
    ("quality_reasons", "加分原因"),
    ("quality_penalties提示", "系统校验提示"),
    ("quality_penalties", "系统校验提示"),
    ("sector_estimate", "板块估算"),
    ("nav_trend", "净值走势"),
    ("return_3m_percent", "近3月收益"),
    ("return_6m_percent", "近6月收益"),
    ("return_1y_percent", "近1年收益"),
)


def humanize_evidence_text(
    text: str,
    *,
    extra_regex_replacements: tuple[tuple[str, str], ...] = (),
    extra_text_replacements: tuple[tuple[str, str], ...] = (),
) -> str:
    """把内部字段名/枚举值替换为中文措辞，避免 LLM/guard 生成的文本泄漏内部字段名。"""
    if not text:
        return text
    result = str(text)
    for pattern, template in (*_BASE_REGEX_REPLACEMENTS, *extra_regex_replacements):
        result = re.sub(
            pattern,
            lambda match, _template=template: _format_number_template(_template, match),
            result,
            flags=re.IGNORECASE,
        )
    result = re.sub(
        r"confidence\s*(?:=|为)?\s*([高中低])",
        lambda match: f"置信度{match.group(1)}",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"track=([a-z_]+)",
        lambda match: track_label(match.group(1)),
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"pattern=([a-z_]+)",
        lambda match: pattern_label(match.group(1)),
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"fund_quality_score\s*(?:=|为)?\s*([-+]?\d+(?:\.\d+)?)",
        lambda match: f"基金质量分 {fmt_num(match.group(1))}",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"sector_fit_score\s*(?:=|为)?\s*([-+]?\d+(?:\.\d+)?)",
        lambda match: f"板块匹配分 {fmt_num(match.group(1))}",
        result,
        flags=re.IGNORECASE,
    )
    for old, new in (*_BASE_TEXT_REPLACEMENTS, *extra_text_replacements):
        result = re.sub(re.escape(old), new, result, flags=re.IGNORECASE)
    return result


def _format_number_template(template: str, match: re.Match) -> str:
    raw = match.group(1)
    return template.format(fmt_num(raw), abs0=fmt_abs_num(raw))


def track_label(track: object) -> str:
    normalized = str(track or "").strip().lower()
    return _TRACK_LABELS.get(normalized, str(track or "未知"))


def pattern_label(pattern: object) -> str:
    normalized = str(pattern or "").strip().lower()
    return _PATTERN_LABELS.get(normalized, str(pattern or "未知"))


def normalize_confidence_label(confidence: object) -> str:
    text = str(confidence or "").strip()
    if text in {"高", "中", "低"}:
        return text
    return "中"


def append_unique(existing: list[str], additions: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    for item in [*existing, *additions]:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def fmt_num(value: object) -> str:
    if value is None:
        return "未知"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.2f}".rstrip("0").rstrip(".")


def fmt_abs_num(value: object) -> str:
    if value is None:
        return "未知"
    try:
        number = abs(float(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.2f}".rstrip("0").rstrip(".")


def as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
