from __future__ import annotations

import pytest

from app.services.decision_guard_shared import (
    ACTION_BUCKET_ADD,
    ACTION_BUCKET_CLEAR_ALL,
    ACTION_BUCKET_DEEP_REDUCE,
    ACTION_BUCKET_PAUSE,
    ACTION_BUCKET_REDUCE,
    ACTION_BUCKET_WATCH,
    append_unique,
    as_float,
    fmt_abs_num,
    fmt_num,
    humanize_evidence_text,
    normalize_confidence_label,
    pattern_label,
    resolve_discovery_escalation,
    resolve_escalation_floor,
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


def test_humanize_evidence_text_translates_report_enum_leaks():
    text = "机会absent；daily_return数据pending；track=momentum"
    assert humanize_evidence_text(text) == (
        "当前不构成机会；当日涨跌待确认；顺势观察"
    )


def test_humanize_evidence_text_translates_english_opportunity_variants():
    text = "opportunity absent；opportunity present"
    assert humanize_evidence_text(text) == "当前不构成机会；当前构成机会"


def test_humanize_evidence_text_translates_chinese_prefixed_present():
    assert humanize_evidence_text("机会present") == "当前构成机会"


def test_humanize_evidence_text_translates_pending_daily_return_variants():
    text = "daily_return_percent pending；daily_return is pending"
    assert humanize_evidence_text(text) == "当日涨跌待确认；当日涨跌待确认"


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


# --- M2.1: resolve_escalation_floor ------------------------------------------------


def _opportunity(*, confidence: str = "高", available: bool = False, penalties: list[str] | None = None) -> dict:
    return {
        "opportunity_available": available,
        "confidence": confidence,
        "penalties": penalties if penalties is not None else ["资金背离或持续流出"],
    }


def _evidence(level: str) -> dict:
    return {"composite": {"level": level}}


def _breadth(*, sentiment_level: str = "冰点", change: int = -2) -> dict:
    return {
        "sentiment_level": sentiment_level,
        "sentiment_level_change": change,
        "decision_eligible": True,
        "freshness_status": "fresh",
        "stale": False,
    }


def test_no_escalation_when_sector_opportunity_missing() -> None:
    result = resolve_escalation_floor(
        sector_opportunity=None,
        evidence=None,
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert result["min_bucket"] is None
    assert result["reasons"] == []
    assert result["suggested_position_change_percent"] is None
    assert result["basis"] == ""


def test_no_escalation_when_opportunity_available_true() -> None:
    """opportunity_available!=False（包括 True 或缺失）时不触发——只有明确不构成机会才升级。"""
    result = resolve_escalation_floor(
        sector_opportunity={"opportunity_available": True, "confidence": "高"},
        evidence=_evidence("不足"),
        market_breadth=_breadth(),
        over_concentration=True,
        has_unrealized_gain=True,
        decision_style="conservative",
    )
    assert result["min_bucket"] is None


def test_no_escalation_when_divergence_not_strong() -> None:
    """confidence 非「高」（即量价背离证据不够强）时不触发——即使 opportunity_available=False。"""
    for weak_confidence in ("中", "低", "不足", ""):
        result = resolve_escalation_floor(
            sector_opportunity=_opportunity(confidence=weak_confidence),
            evidence=_evidence("不足"),
            market_breadth=_breadth(),
            over_concentration=True,
            has_unrealized_gain=True,
            decision_style="conservative",
        )
        assert result["min_bucket"] is None, f"confidence={weak_confidence!r} should not escalate"


def test_row1_pause_when_only_divergence_and_unavailable() -> None:
    """仅命中「量价背离显著+机会不成立」：最低档位=暂停追涨，设计未给出仓位比例。"""
    result = resolve_escalation_floor(
        sector_opportunity=_opportunity(),
        evidence=None,  # 无基金自身证据数据，不应触发第2档
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert result["min_bucket"] == ACTION_BUCKET_PAUSE
    assert result["suggested_position_change_percent"] is None
    assert result["reasons"]
    assert result["basis"]


def test_row1_pause_when_evidence_present_but_strong() -> None:
    """基金自身证据充分（高/中）时不应触发减仓类升级，只停在暂停追涨。"""
    for strong_level in ("高", "中"):
        result = resolve_escalation_floor(
            sector_opportunity=_opportunity(),
            evidence=_evidence(strong_level),
            market_breadth=None,
            over_concentration=False,
            has_unrealized_gain=False,
            decision_style="conservative",
        )
        assert result["min_bucket"] == ACTION_BUCKET_PAUSE, f"level={strong_level!r}"


def test_row2_reduce_when_fund_evidence_weak() -> None:
    result = resolve_escalation_floor(
        sector_opportunity=_opportunity(),
        evidence=_evidence("不足"),
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert result["min_bucket"] == ACTION_BUCKET_REDUCE
    assert result["suggested_position_change_percent"] == -25.0


def test_row3_reduce_with_higher_percent_when_unrealized_gain() -> None:
    """浮盈时下限档位不变（仍是减仓评估），但建议仓位调整比例应提高（更负）。"""
    result = resolve_escalation_floor(
        sector_opportunity=_opportunity(),
        evidence=_evidence("低"),
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=True,
        decision_style="conservative",
    )
    assert result["min_bucket"] == ACTION_BUCKET_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-(100 / 3))
    assert result["suggested_position_change_percent"] < -25.0  # 比 row2 更激进


def test_row4_deep_reduce_conservative_requires_both_conditions() -> None:
    """conservative 风格下，第4档要求「情绪冰点降档」与「集中度超限」同时满足。"""
    only_breadth = resolve_escalation_floor(
        sector_opportunity=_opportunity(),
        evidence=_evidence("不足"),
        market_breadth=_breadth(),
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert only_breadth["min_bucket"] == ACTION_BUCKET_REDUCE  # 未升级到第4档

    only_concentration = resolve_escalation_floor(
        sector_opportunity=_opportunity(),
        evidence=_evidence("不足"),
        market_breadth=None,
        over_concentration=True,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert only_concentration["min_bucket"] == ACTION_BUCKET_REDUCE  # 未升级到第4档

    both = resolve_escalation_floor(
        sector_opportunity=_opportunity(),
        evidence=_evidence("不足"),
        market_breadth=_breadth(),
        over_concentration=True,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert both["min_bucket"] == ACTION_BUCKET_DEEP_REDUCE
    assert both["suggested_position_change_percent"] == -50.0


def test_row4_deep_reduce_lenient_style_triggers_on_either_condition() -> None:
    """tactical/aggressive 风格门槛更松：情绪冰点或集中度超限任一满足即可触发第4档
    （decision_style 只影响触发门槛松紧，不是触发的必要条件——本用例验证"更容易触发"）。
    用默认 fixture（1 条 penalty）验证：第5档门槛固定为 >=2 条 penalty、不随
    decision_style 松紧，因此这里应稳定停在第4档，不会连带触发第5档。"""
    for style in ("tactical", "aggressive"):
        only_breadth = resolve_escalation_floor(
            sector_opportunity=_opportunity(),
            evidence=_evidence("不足"),
            market_breadth=_breadth(),
            over_concentration=False,
            has_unrealized_gain=False,
            decision_style=style,
        )
        assert only_breadth["min_bucket"] == ACTION_BUCKET_DEEP_REDUCE, style

        only_concentration = resolve_escalation_floor(
            sector_opportunity=_opportunity(),
            evidence=_evidence("不足"),
            market_breadth=None,
            over_concentration=True,
            has_unrealized_gain=False,
            decision_style=style,
        )
        assert only_concentration["min_bucket"] == ACTION_BUCKET_DEEP_REDUCE, style


def test_row4_requires_sentiment_both_ice_and_dropping() -> None:
    """情绪档位必须同时满足「冰点」与「较前一交易日下降≥2档」，缺一不可。"""
    ice_but_not_dropping = resolve_escalation_floor(
        sector_opportunity=_opportunity(),
        evidence=_evidence("不足"),
        market_breadth={"sentiment_level": "冰点", "sentiment_level_change": 0},
        over_concentration=True,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert ice_but_not_dropping["min_bucket"] == ACTION_BUCKET_REDUCE

    dropping_but_not_ice = resolve_escalation_floor(
        sector_opportunity=_opportunity(),
        evidence=_evidence("不足"),
        market_breadth={"sentiment_level": "低迷", "sentiment_level_change": -3},
        over_concentration=True,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert dropping_but_not_ice["min_bucket"] == ACTION_BUCKET_REDUCE


def test_row4_rejects_stale_or_legacy_breadth_for_hard_guard() -> None:
    """过期、明确不合格或缺少资格字段的旧缓存都只能展示，不能触发第4档。"""
    candidates = [
        {"sentiment_level": "冰点", "sentiment_level_change": -2},
        {**_breadth(), "decision_eligible": False},
        {**_breadth(), "stale": True},
        {**_breadth(), "freshness_status": "stale"},
    ]
    for breadth in candidates:
        result = resolve_escalation_floor(
            sector_opportunity=_opportunity(),
            evidence=_evidence("不足"),
            market_breadth=breadth,
            over_concentration=True,
            has_unrealized_gain=False,
            decision_style="conservative",
        )
        assert result["min_bucket"] == ACTION_BUCKET_REDUCE


def test_row5_clear_all_conservative_requires_two_penalties() -> None:
    """conservative 风格下，第5档（多重信号共振）要求至少 2 条 penalties 同时命中。"""
    one_penalty = resolve_escalation_floor(
        sector_opportunity=_opportunity(penalties=["资金背离或持续流出"]),
        evidence=_evidence("不足"),
        market_breadth=_breadth(),
        over_concentration=True,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert one_penalty["min_bucket"] == ACTION_BUCKET_DEEP_REDUCE  # 未升级到第5档

    two_penalties = resolve_escalation_floor(
        sector_opportunity=_opportunity(
            penalties=["资金背离或持续流出", "单日涨幅过热"]
        ),
        evidence=_evidence("不足"),
        market_breadth=_breadth(),
        over_concentration=True,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert two_penalties["min_bucket"] == ACTION_BUCKET_CLEAR_ALL
    assert two_penalties["suggested_position_change_percent"] == -100.0


def test_row5_threshold_is_uniform_across_decision_styles() -> None:
    """第5档门槛（>=2 条 penalty 同时命中）不随 decision_style 松紧——只有第4档的
    触发条件（情绪冰点 or/and 集中度超限）会因风格而松紧不同；第5档统一保持更高
    门槛，避免 lenient 风格下第4/5档因门槛雷同而无法区分（见实现注释）。"""
    for style in ("conservative", "tactical", "aggressive"):
        one_penalty = resolve_escalation_floor(
            sector_opportunity=_opportunity(penalties=["资金背离或持续流出"]),
            evidence=_evidence("不足"),
            market_breadth=_breadth(),
            over_concentration=True,
            has_unrealized_gain=False,
            decision_style=style,
        )
        assert one_penalty["min_bucket"] == ACTION_BUCKET_DEEP_REDUCE, style

        two_penalties = resolve_escalation_floor(
            sector_opportunity=_opportunity(
                penalties=["资金背离或持续流出", "单日涨幅过热"]
            ),
            evidence=_evidence("不足"),
            market_breadth=_breadth(),
            over_concentration=True,
            has_unrealized_gain=False,
            decision_style=style,
        )
        assert two_penalties["min_bucket"] == ACTION_BUCKET_CLEAR_ALL, style


def test_action_bucket_constants_ordering() -> None:
    """完整 6 档 bucket 数值须严格单调递增（清仓 < 大幅减仓 < 减仓 < 观察 < 暂停 < 加仓）。"""
    ordered = [
        ACTION_BUCKET_CLEAR_ALL,
        ACTION_BUCKET_DEEP_REDUCE,
        ACTION_BUCKET_REDUCE,
        ACTION_BUCKET_WATCH,
        ACTION_BUCKET_PAUSE,
        ACTION_BUCKET_ADD,
    ]
    assert ordered == sorted(ordered)
    assert len(set(ordered)) == len(ordered)


def test_reasons_and_basis_are_consistent() -> None:
    result = resolve_escalation_floor(
        sector_opportunity=_opportunity(),
        evidence=_evidence("不足"),
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert result["basis"] == "；".join(result["reasons"])
    assert len(result["reasons"]) == 2


# --- M4: resolve_discovery_escalation ----------------------------------------------


def test_discovery_no_escalation_when_opportunity_missing() -> None:
    result = resolve_discovery_escalation(sector_opportunity=None, pool_item={"fund_quality_score": 30})
    assert result["action"] is None
    assert result["amount_multiplier"] is None
    assert result["reasons"] == []
    assert result["basis"] == ""


def test_discovery_no_escalation_when_confidence_not_high() -> None:
    for confidence in ("中", "低", "不足", ""):
        result = resolve_discovery_escalation(
            sector_opportunity={"opportunity_available": False, "confidence": confidence},
            pool_item={"fund_quality_score": 20},
        )
        assert result["action"] is None, f"confidence={confidence!r}"


def test_discovery_exclude_when_negative_resonance() -> None:
    """量价背离显著 + 板块不构成机会 + 基金质量分也偏低（<55）：两维度共振才剔除。"""
    result = resolve_discovery_escalation(
        sector_opportunity={"opportunity_available": False, "confidence": "高"},
        pool_item={"fund_quality_score": 40},
    )
    assert result["action"] == "exclude"
    assert result["amount_multiplier"] is None
    assert result["reasons"]
    assert result["basis"]


def test_discovery_exclude_when_quality_score_missing() -> None:
    """基金质量分缺失时也视为"不够强"，与显式低分同样触发剔除（不能因为没数据就放过）。"""
    result = resolve_discovery_escalation(
        sector_opportunity={"opportunity_available": False, "confidence": "高"},
        pool_item={},
    )
    assert result["action"] == "exclude"


def test_discovery_no_exclude_when_fund_quality_strong_despite_weak_sector() -> None:
    """板块弱但基金质量分本身够高（>=55）时不剔除——只命中一个维度，交给既有弱证据
    降级逻辑处理（降为"建议关注"而非直接剔除）。"""
    result = resolve_discovery_escalation(
        sector_opportunity={"opportunity_available": False, "confidence": "高"},
        pool_item={"fund_quality_score": 70},
    )
    assert result["action"] is None


def test_discovery_boost_when_positive_resonance() -> None:
    """量价背离显著 + 板块构成机会 + 基金质量分也够高（>=75）：两维度共振才提额。"""
    result = resolve_discovery_escalation(
        sector_opportunity={"opportunity_available": True, "confidence": "高"},
        pool_item={"fund_quality_score": 80},
    )
    assert result["action"] == "boost"
    assert result["amount_multiplier"] == 1.2
    assert result["reasons"]


def test_discovery_no_boost_when_fund_quality_only_moderate() -> None:
    """板块强但基金质量分不够高（<75）时不提额——同样要求两个维度共振。"""
    result = resolve_discovery_escalation(
        sector_opportunity={"opportunity_available": True, "confidence": "高"},
        pool_item={"fund_quality_score": 60},
    )
    assert result["action"] is None


def test_discovery_no_boost_when_quality_score_missing() -> None:
    result = resolve_discovery_escalation(
        sector_opportunity={"opportunity_available": True, "confidence": "高"},
        pool_item={},
    )
    assert result["action"] is None


def test_discovery_no_escalation_when_opportunity_available_is_none() -> None:
    """opportunity_available 既非 True 也非 False（如缺失该字段）时，不触发任一方向。"""
    result = resolve_discovery_escalation(
        sector_opportunity={"confidence": "高"},
        pool_item={"fund_quality_score": 90},
    )
    assert result["action"] is None
