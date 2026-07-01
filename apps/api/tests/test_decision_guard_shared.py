from __future__ import annotations

from app.services.decision_guard_shared import (
    append_unique,
    as_float,
    fmt_abs_num,
    fmt_num,
    humanize_evidence_text,
    normalize_confidence_label,
    pattern_label,
    track_label,
)


def test_humanize_evidence_text_replaces_internal_field_names() -> None:
    text = (
        "nav_trend.distance_from_high_percent=-3.2%，max_drawdown_1y_percent=-25.6%，"
        "fund_quality_score=61.5，sector_fit_score=24，confidence=高，track=momentum，pattern=distribution"
    )
    result = humanize_evidence_text(text)
    assert "距离近期高点约 -3.2%" in result
    assert "近1年最大回撤约 25.6%" in result
    assert "基金质量分 61.5" in result
    assert "板块匹配分 24" in result
    assert "置信度高" in result
    assert "顺势观察" in result
    assert "涨幅较快但资金流出" in result
    assert "fund_quality_score" not in result
    assert "sector_fit_score" not in result


def test_humanize_evidence_text_empty_passthrough() -> None:
    assert humanize_evidence_text("") == ""
    assert humanize_evidence_text(None) is None  # type: ignore[arg-type]


def test_humanize_evidence_text_extra_replacements() -> None:
    result = humanize_evidence_text(
        "factor_reliability 偏低",
        extra_text_replacements=(("factor_reliability", "因子回测置信"),),
    )
    assert result == "因子回测置信 偏低"


def test_track_and_pattern_label_fallback_to_raw() -> None:
    assert track_label("momentum") == "顺势观察"
    assert track_label("setup") == "蓄势观察"
    assert track_label("unknown_track") == "unknown_track"
    assert pattern_label("distribution") == "涨幅较快但资金流出"
    assert pattern_label(None) == "未知"


def test_normalize_confidence_label() -> None:
    assert normalize_confidence_label("高") == "高"
    assert normalize_confidence_label("garbage") == "中"
    assert normalize_confidence_label(None) == "中"


def test_append_unique_dedupes_and_respects_limit() -> None:
    result = append_unique(["a", "b"], ["b", "c", "d"], limit=3)
    assert result == ["a", "b", "c"]


def test_number_formatters() -> None:
    assert fmt_num(1.0) == "1"
    assert fmt_num(1.25) == "1.25"
    assert fmt_num(None) == "未知"
    assert fmt_abs_num(-3.5) == "3.5"
    assert as_float("2.5") == 2.5
    assert as_float("nope") is None
