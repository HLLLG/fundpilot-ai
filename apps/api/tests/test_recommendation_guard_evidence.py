from __future__ import annotations

import pytest

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services.recommendation_guard import (
    _weak_evidence_reasons,
    apply_recommendation_guards,
)


@pytest.fixture(autouse=True)
def _no_live_intraday_reversal_signal(monkeypatch):
    """这些用例只测「板块方向/量化证据」降级逻辑，避免真实盘中数据（网络/交易日相关）
    偶发触发 reversal/pullback 分支导致断言随机失败。"""
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda _holding: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda _holding, _nav_trend: None,
    )

_TODAY_NEWS = [NewsItem(topic="半导体", title="半导体行业利好消息", is_today=True)]


def _request(*, sector_name: str = "半导体", decision_style: str = "conservative") -> AnalysisRequest:
    profile = InvestorProfile(
        decision_style=decision_style,
        max_drawdown_percent=15,
        concentration_limit_percent=30,
        expected_investment_amount=100000,
        avoid_chasing=False,
    )
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name=sector_name,
            holding_amount=10000,
        )
    ]
    return AnalysisRequest(holdings=holdings, profile=profile)


def _risk() -> RiskAssessment:
    return RiskAssessment(level="medium", weighted_return_percent=1.2, suggested_action="watch", alerts=[])


def _rec(**overrides) -> FundRecommendation:
    base = {
        "fund_code": "519674",
        "fund_name": "银河创新成长",
        "action": "分批加仓",
    }
    base.update(overrides)
    return FundRecommendation(**base)


def _facts_with_holding(sector_opportunity=None, evidence=None) -> dict:
    row = {"fund_code": "519674"}
    if sector_opportunity is not None:
        row["sector_opportunity"] = sector_opportunity
    if evidence is not None:
        row["evidence"] = evidence
    return {"holdings": [row]}


@pytest.mark.parametrize(
    "components",
    [
        None,
        [],
        [{"source": "signal", "level": "低"}],
        [{"source": "factor"}],
        [None, "invalid", {"source": "risk", "level": "不足"}],
    ],
)
def test_weak_composite_without_factor_component_reports_missing_ic_coverage(
    components,
) -> None:
    evidence = {"composite": {"level": "低"}}
    if components is not None:
        evidence["components"] = components

    reasons = _weak_evidence_reasons(None, evidence)

    assert "IC 回测未覆盖，现有量化证据置信偏低" in reasons
    assert "量化证据背书弱" not in reasons


def test_weak_composite_with_factor_component_retains_weak_evidence_reason() -> None:
    reasons = _weak_evidence_reasons(
        None,
        {
            "composite": {"level": "不足"},
            "components": [
                "invalid",
                {"source": "factor", "level": "低", "basis": "主因子动量·IC偏弱"},
                {"source": "risk", "level": "中"},
            ],
        },
    )

    assert "量化证据背书弱" in reasons
    assert "IC 回测未覆盖，现有量化证据置信偏低" not in reasons


def test_full_guard_ignores_non_dict_evidence_components() -> None:
    facts = _facts_with_holding(
        evidence={
            "composite": {"level": "低"},
            "components": [None, "invalid", {"source": "risk", "level": "低"}],
        }
    )

    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(decision_style="tactical"),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )

    assert guarded[0].action == "观察"
    assert any("IC 回测未覆盖，现有量化证据置信偏低" in point for point in guarded[0].points)


@pytest.mark.parametrize(
    ("ic_state", "weak_reason", "participation_note", "component_count"),
    [
        (
            "unavailable",
            "IC 回测未接入，现有非 IC 证据置信偏低",
            "IC 回测未接入，IC 未参与本次结论",
            1,
        ),
        (
            "stale",
            "IC 回测已过期，现有非 IC 证据置信偏低",
            "IC 回测已过期，IC 未参与本次结论",
            1,
        ),
        ("available", "量化证据背书弱", None, 2),
    ],
)
def test_top_level_ic_status_controls_public_evidence_wording(
    ic_state: str,
    weak_reason: str,
    participation_note: str | None,
    component_count: int,
) -> None:
    facts = _facts_with_holding(
        evidence={
            "composite": {"level": "低"},
            "components": [
                {"source": "factor", "level": "低", "basis": "主因子动量·IC偏弱"},
                {"source": "signal", "level": "低", "basis": "板块信号样本偏弱"},
            ],
        }
    )
    facts["factor_scores"] = {"ic_status": {"state": ic_state}}

    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(decision_style="tactical"),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )

    rec = guarded[0]
    public_text = "\n".join(
        [*rec.points, rec.decision_path, *rec.fund_evidence, *rec.validation_notes]
    )
    assert rec.action == "观察"
    assert weak_reason in rec.points[0]
    assert f"{component_count}路已参与量化证据综合置信" in rec.decision_path
    assert any(
        f"{component_count}路已参与量化证据综合置信" in item
        for item in rec.fund_evidence
    )
    assert "三路量化证据" not in public_text
    assert weak_reason in rec.validation_notes

    if participation_note is None:
        assert "主因子动量·IC偏弱" in rec.fund_evidence
    else:
        assert participation_note in rec.decision_path
        assert participation_note in rec.fund_evidence
        assert participation_note in rec.validation_notes
        assert "主因子动量·IC偏弱" not in rec.fund_evidence
        assert "量化证据背书弱" not in public_text


def test_available_ic_with_malformed_factor_uses_uncovered_wording() -> None:
    facts = _facts_with_holding(
        evidence={
            "composite": {"level": "不足"},
            "components": [
                {"source": "factor"},
                {"source": "signal", "level": "不足", "basis": "板块样本不足"},
            ],
        }
    )
    facts["factor_scores"] = {"ic_status": {"state": "available"}}

    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(decision_style="tactical"),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )

    rec = guarded[0]
    public_text = "\n".join(
        [*rec.points, rec.decision_path, *rec.fund_evidence, *rec.validation_notes]
    )
    assert "IC 回测未覆盖，现有量化证据置信偏低" in rec.points[0]
    assert "1路已参与量化证据综合置信" in rec.decision_path
    assert "IC 回测未覆盖，IC 未参与本次结论" in public_text
    assert "量化证据背书弱" not in public_text


def test_weak_sector_opportunity_downgrades_add_action() -> None:
    facts = _facts_with_holding(
        sector_opportunity={
            "track": "momentum",
            "confidence": "低",
            "opportunity_available": False,
            "pattern_label": "distribution",
        }
    )
    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(decision_style="tactical"),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "观察"
    assert any("证据不足" in point for point in rec.points)


def test_strong_evidence_keeps_add_action_and_backfills_fields() -> None:
    facts = _facts_with_holding(
        sector_opportunity={
            "track": "momentum",
            "confidence": "高",
            "opportunity_available": True,
            "pattern_label": "price_flow_aligned_up",
            "today_main_force_net_yi": 6.0,
            "cumulative_5d_net_yi": 12.0,
            "evidence": ["今日主力净流入"],
        },
        evidence={
            "composite": {"level": "高", "score": 3.0},
            "components": [{"source": "factor", "level": "高", "basis": "主因子动量(百分位80)"}],
            "summary": "主因子动量(百分位80)",
        },
    )
    _, guarded = apply_recommendation_guards(
        [_rec()],
        [],
        _request(decision_style="tactical"),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "分批加仓"
    assert rec.decision_path
    assert "半导体" in rec.decision_path
    assert rec.sector_evidence
    assert rec.fund_evidence
    assert rec.risks


def test_missing_facts_row_does_not_crash_and_still_backfills_generic_fields() -> None:
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=None,
    )
    rec = guarded[0]
    assert rec.decision_path
    assert rec.confidence == "中"


def test_confidence_is_normalized_to_known_labels() -> None:
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察", confidence="非常高")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=None,
    )
    assert guarded[0].confidence == "中"


def test_humanizes_internal_field_names_in_llm_provided_decision_path() -> None:
    rec = _rec(
        action="观察",
        decision_path="板块 track=momentum confidence=高，fund_quality_score=61.5",
    )
    _, guarded = apply_recommendation_guards(
        [rec],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=None,
    )
    text = guarded[0].decision_path
    assert "track=" not in text
    assert "fund_quality_score=" not in text
    assert "顺势观察" in text
    assert "基金质量分 61.5" in text


# --- M2: 双向 guard 升级（resolve_escalation_floor 接入 apply_recommendation_guards） ----


def _strong_divergence_opportunity(**overrides) -> dict:
    base = {
        "track": "momentum",
        "confidence": "高",  # M1.4 修复后，量价背离显著时才会出现「高」
        "opportunity_available": False,
        "pattern_label": "distribution",
        "penalties": ["资金背离或持续流出"],
    }
    base.update(overrides)
    return base


def test_llm_watch_gets_upgraded_to_pause_when_divergence_strong_and_evidence_ok() -> None:
    """本次升级要修的核心场景：LLM 本来就给"观察"（不是"分批加仓"），旧的单向 guard
    完全不会动它；新的双向 guard 在量价背离显著证据下应把它上调为更保守的动作。"""
    facts = _facts_with_holding(
        sector_opportunity=_strong_divergence_opportunity(),
        evidence={"composite": {"level": "高", "score": 3.0}},
    )
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(decision_style="conservative"),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "暂停追涨"
    assert any("上调" in point for point in rec.points)


def test_llm_watch_gets_upgraded_to_reduce_when_fund_evidence_also_weak() -> None:
    facts = _facts_with_holding(
        sector_opportunity=_strong_divergence_opportunity(),
        evidence={"composite": {"level": "不足", "score": 0.5}},
    )
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(decision_style="conservative"),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "减仓评估"
    assert rec.suggested_position_change_percent == -25.0
    assert rec.suggested_position_change_basis


def test_llm_add_action_gets_upgraded_past_the_normal_downgrade_to_reduce() -> None:
    """LLM 给"分批加仓"时，旧逻辑只会把它降到"观察"（弱证据分支）；新逻辑在证据
    极强时应继续往下拉到"减仓评估"，而不是停在"观察"就不动了。"""
    facts = _facts_with_holding(
        sector_opportunity=_strong_divergence_opportunity(),
        evidence={"composite": {"level": "低", "score": 1.0}},
    )
    _, guarded = apply_recommendation_guards(
        [_rec(action="分批加仓")],
        [],
        _request(decision_style="conservative"),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "减仓评估"


def test_escalation_does_not_downgrade_below_llm_action_when_evidence_is_fine() -> None:
    """证据不强（confidence 非「高」）时，不应触发额外升级——LLM 给"观察"应保持"观察"
    （除非其他既有 guard 分支介入，此用例特意避开那些分支）。"""
    facts = _facts_with_holding(
        sector_opportunity={
            "track": "setup",
            "confidence": "中",
            "opportunity_available": True,
            "pattern_label": "accumulation",
        },
        evidence={"composite": {"level": "中", "score": 2.0}},
    )
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(decision_style="conservative"),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "观察"
    assert rec.suggested_position_change_percent is None


def test_escalation_backfills_position_change_fields_only_when_triggered() -> None:
    """未触发升级时，suggested_position_change_percent/basis 保持模型默认值
    （不会被意外污染成非 None）。"""
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        _request(),
        _risk(),
        _TODAY_NEWS,
        [],
        facts=None,
    )
    rec = guarded[0]
    assert rec.suggested_position_change_percent is None
    assert rec.suggested_position_change_basis == ""


def test_deep_reduce_action_produces_matching_default_risk_text() -> None:
    facts = _facts_with_holding(
        sector_opportunity=_strong_divergence_opportunity(penalties=[]),
        evidence={"composite": {"level": "不足", "score": 0.0}},
    )
    market_breadth = {"sentiment_level": "冰点", "sentiment_level_change": -2}
    request = _request(decision_style="conservative")
    # 手工构造一个集中度超限的持仓场景：期望投入设小一点让 weight_percent 超过上限。
    request.profile.expected_investment_amount = 10000
    request.profile.concentration_limit_percent = 5
    facts["market_breadth"] = market_breadth
    facts["holdings"][0]["over_concentration"] = True
    _, guarded = apply_recommendation_guards(
        [_rec(action="观察")],
        [],
        request,
        _risk(),
        _TODAY_NEWS,
        [],
        facts=facts,
    )
    rec = guarded[0]
    assert rec.action == "大幅减仓评估"
    assert any("恢复原仓位" in risk for risk in rec.risks)
