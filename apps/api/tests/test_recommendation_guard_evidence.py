from __future__ import annotations

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
)
from app.services.recommendation_guard import apply_recommendation_guards

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
