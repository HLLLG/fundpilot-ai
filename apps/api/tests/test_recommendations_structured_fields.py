from __future__ import annotations

from app.models import FundRecommendation
from app.services.recommendations import (
    merge_fund_recommendations,
    parse_fund_recommendations_raw,
)


def test_parse_fund_recommendations_raw_reads_structured_fields() -> None:
    raw = [
        {
            "fund_code": "519674",
            "fund_name": "银河创新成长",
            "action": "观察",
            "points": ["测试要点"],
            "confidence": "高",
            "hold_horizon": "1-2周",
            "risks": ["板块波动较大"],
            "decision_path": "先看板块方向，再看基金证据，最后给出动作",
            "sector_evidence": ["track=momentum", "confidence=中"],
            "fund_evidence": ["fund_quality_score=61.5"],
            "validation_notes": ["样本有限"],
        }
    ]
    result = parse_fund_recommendations_raw(raw)
    assert len(result) == 1
    rec = result[0]
    assert rec.confidence == "高"
    assert rec.hold_horizon == "1-2周"
    assert rec.risks == ["板块波动较大"]
    assert rec.decision_path == "先看板块方向，再看基金证据，最后给出动作"
    assert rec.sector_evidence == ["track=momentum", "confidence=中"]
    assert rec.fund_evidence == ["fund_quality_score=61.5"]
    assert rec.validation_notes == ["样本有限"]


def test_parse_fund_recommendations_raw_defaults_when_fields_missing() -> None:
    raw = [{"fund_code": "519674", "fund_name": "银河创新成长", "action": "观察"}]
    rec = parse_fund_recommendations_raw(raw)[0]
    assert rec.confidence == "中"
    assert rec.hold_horizon == ""
    assert rec.risks == []
    assert rec.decision_path == ""
    assert rec.sector_evidence == []
    assert rec.fund_evidence == []
    assert rec.validation_notes == []


def test_fund_recommendation_model_defaults_are_backward_compatible() -> None:
    rec = FundRecommendation(fund_code="519674", fund_name="银河创新成长", action="观察")
    assert rec.confidence == "中"
    assert rec.risks == []
    assert rec.decision_path == ""


def test_merge_fund_recommendations_combines_structured_fields() -> None:
    first = FundRecommendation(
        fund_code="519674",
        fund_name="银河创新成长",
        action="观察",
        confidence="中",
        risks=["风险A"],
        sector_evidence=["证据1"],
    )
    second = FundRecommendation(
        fund_code="519674",
        fund_name="银河创新成长",
        action="减仓评估",
        confidence="高",
        hold_horizon="1-3个月",
        decision_path="先看板块，再看基金",
        risks=["风险A", "风险B"],
        fund_evidence=["fund_evidence1"],
        validation_notes=["样本有限"],
    )
    merged = merge_fund_recommendations([first, second])
    assert len(merged) == 1
    rec = merged[0]
    assert rec.action == "减仓评估"
    assert rec.confidence == "高"
    assert rec.hold_horizon == "1-3个月"
    assert rec.decision_path == "先看板块，再看基金"
    assert rec.risks == ["风险A", "风险B"]
    assert rec.sector_evidence == ["证据1"]
    assert rec.fund_evidence == ["fund_evidence1"]
    assert rec.validation_notes == ["样本有限"]
