"""F1 回归：judge_parsed_report 复用上游 facts，不再调用 build_analysis_facts。"""

from __future__ import annotations

from unittest.mock import patch

from app.config import get_settings
from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_runtime import resolve_analysis_runtime


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="519674",
                fund_name="银河创新成长",
                sector_name="半导体",
                holding_amount=10000,
            )
        ],
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=15,
            concentration_limit_percent=30,
            expected_investment_amount=100000,
        ),
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.0,
        suggested_action="watch",
        alerts=[],
    )


def _fake_facts() -> dict:
    return {
        "readonly": True,
        "instruction": "fake",
        "portfolio": {
            "weighted_return_percent": 1.0,
            "risk_level": "medium",
            "suggested_action": "watch",
            "concentration_limit_percent": 30,
        },
        "holdings": [
            {"fund_code": "519674", "weight_percent": 50.0}
        ],
        "allowed_actions": ["观察", "暂停追涨", "分批加仓", "减仓评估", "风控复核"],
        "alerts": [],
        "news": {},
    }


def test_judge_does_not_call_build_analysis_facts():
    """当调用方传入 facts 时，judge 不应再调用 build_analysis_facts。"""
    from app.services import report_judge

    parsed = {
        "title": "test",
        "summary": "ok",
        "fund_recommendations": [
            {"fund_code": "519674", "fund_name": "银河创新成长", "action": "观察"}
        ],
        "caveats": [],
    }
    snapshots = [FundSnapshot(fund_code="519674", fund_name="银河创新成长", source="test")]
    runtime = resolve_analysis_runtime(get_settings(), "fast")

    with patch(
        "app.services.report_judge.build_analysis_facts",
        side_effect=AssertionError("build_analysis_facts 不应再被调用"),
    ):
        out, meta = report_judge.judge_parsed_report(
            parsed, _request(), _risk(), snapshots, runtime, facts=_fake_facts()
        )

    assert out["fund_recommendations"][0]["fund_code"] == "519674"
    assert meta["rule_judge"] is True


def test_rule_judge_respects_concentration_using_provided_facts():
    """超集中度的持仓建议加仓 → 应被改写为减仓评估，且不重算 facts。"""
    from app.services import report_judge

    facts = _fake_facts()
    facts["holdings"] = [{"fund_code": "519674", "weight_percent": 80.0}]  # 超 30%

    parsed = {
        "title": "test",
        "fund_recommendations": [
            {"fund_code": "519674", "fund_name": "x", "action": "分批加仓"}
        ],
    }
    snapshots = [FundSnapshot(fund_code="519674", fund_name="x", source="test")]
    runtime = resolve_analysis_runtime(get_settings(), "fast")

    with patch(
        "app.services.report_judge.build_analysis_facts",
        side_effect=AssertionError("不应再调"),
    ):
        out, _meta = report_judge.judge_parsed_report(
            parsed, _request(), _risk(), snapshots, runtime, facts=facts
        )

    assert out["fund_recommendations"][0]["action"] == "减仓评估"
