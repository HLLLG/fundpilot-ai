from __future__ import annotations

from copy import deepcopy
import inspect
import json

from app.models import (
    AnalysisRequest,
    FundRecommendation,
    Holding,
    InvestorProfile,
    Report,
    RiskAssessment,
)
from app.services import (
    analyze_pipeline,
    analyze_streaming,
    deepseek_client,
    discovery_client,
    discovery_job_store,
    discovery_pipeline,
    discovery_streaming,
    job_store,
)
from app.services.analysis_payload import AnalysisFactsBundle
from app.services.analysis_runtime import AnalysisRuntime
from app.services.deepseek_client import (
    _build_final_report,
    _offline_report,
    _validate_daily_fund_lookthrough_claims,
)
from app.services.discovery_client import build_discovery_report_from_parsed
from app.services.discovery_offline import build_offline_discovery_report
from app.services.fund_lookthrough_claim_validator import (
    CLAIM_AUDIT_SCHEMA_VERSION,
    validate_fund_lookthrough_claims,
)


def _lookthrough_facts() -> dict:
    return {
        "schema_version": "fund_lookthrough_research.v1",
        "status": "qualified",
        "portfolio": {
            "identity_known_security_mass_lower_bound_percent": 40,
        },
        "candidates": [
            {
                "fund_code": "006081",
                "portfolio_overlap_interpretation": (
                    "no_common_in_disclosed_scope"
                ),
                "portfolio_security_overlap_lower_bound_percent": None,
                "capabilities": {
                    "research_eligible": True,
                    "concentration_risk_guard_eligible": False,
                    "allocation_authorization_eligible": False,
                },
                "vintage_alignment": {
                    "status": "same_as_of_date",
                    "gap_days": 0,
                    "as_of_dates": ["2026-06-30"],
                },
                "snapshot": {"as_of_date": "2026-06-30"},
            }
        ],
        "raw_holdings": [
            {"security_name": "隐私持仓哨兵", "weight_percent": 9.8765}
        ],
    }


def _request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="006081",
                fund_name="测试持仓基金",
                holding_amount=10_000,
            )
        ],
        profile=InvestorProfile(expected_investment_amount=20_000),
    )


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        suggested_action="watch",
        weighted_return_percent=0,
        alerts=[],
    )


def _bundle(*, with_lookthrough: bool = True) -> AnalysisFactsBundle:
    facts = {
        "readonly": True,
        "session": {},
        "holdings": [],
        "portfolio": {},
        "data_evidence_guard": {"execution_blocked": False},
    }
    if with_lookthrough:
        facts["fund_lookthrough"] = _lookthrough_facts()
    return AnalysisFactsBundle(
        session={},
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts=facts,
    )


def _runtime() -> AnalysisRuntime:
    return AnalysisRuntime(
        mode="deep",
        model="test-model",
        news_enabled=False,
        news_max_topics=0,
        news_tool_max_rounds=0,
        news_tool_rounds_configured=0,
    )


def _daily_recommendation() -> FundRecommendation:
    return FundRecommendation(
        fund_code="006081",
        fund_name="测试持仓基金",
        action="分批买入",
        amount_yuan=1_234,
        points=["006081与组合0%重合，因此完全分散，建议买入。"],
    )


def _fallback_report() -> Report:
    return Report(
        title="fallback",
        risk=_risk(),
        holdings=_request().holdings,
        summary="fallback",
        recommendations=[],
        caveats=[],
    )


def test_daily_online_builder_uses_full_facts_after_final_guard(monkeypatch) -> None:
    bundle = _bundle()
    original_facts = deepcopy(bundle.facts)
    captured: dict = {}

    monkeypatch.setattr(
        deepseek_client,
        "_offline_report",
        lambda *_args, **_kwargs: _fallback_report(),
    )
    monkeypatch.setattr(
        deepseek_client,
        "_finalize_recommendations",
        lambda *_args, **_kwargs: (["组合保持观察。"], [_daily_recommendation()]),
    )

    def capture_validator(report, fund_lookthrough):
        captured["fund_lookthrough"] = deepcopy(fund_lookthrough)
        return validate_fund_lookthrough_claims(report, fund_lookthrough)

    monkeypatch.setattr(
        deepseek_client,
        "validate_fund_lookthrough_claims",
        capture_validator,
    )

    report = _build_final_report(
        {"title": "日报", "summary": "普通摘要。", "caveats": []},
        request=_request(),
        risk=_risk(),
        snapshots=[],
        market_news=[],
        topic_briefs=[],
        nav_trends={},
        analysis_bundle=bundle,
        judge_meta={},
        runtime=_runtime(),
    )

    recommendation = report.fund_recommendations[0]
    assert recommendation.points == [
        "低或未观察到的披露重合不能证明完整组合更分散，也不能作为买入理由。"
    ]
    assert recommendation.action == "分批买入"
    assert recommendation.amount_yuan == 1_234
    assert captured["fund_lookthrough"] == original_facts["fund_lookthrough"]
    assert bundle.facts == original_facts

    audit = report.analysis_facts["fund_lookthrough_claim_audit"]
    assert audit["schema_version"] == CLAIM_AUDIT_SCHEMA_VERSION
    assert audit["status"] == "sanitized"
    assert "fund_lookthrough_claim_audit" not in report.analysis_facts["fund_lookthrough"]
    serialized_audit = json.dumps(audit, ensure_ascii=False)
    assert "隐私持仓哨兵" not in serialized_audit
    assert "9.8765" not in serialized_audit


def test_daily_offline_builder_handles_missing_facts_and_is_idempotent(
    monkeypatch,
) -> None:
    recommendation = _daily_recommendation()
    recommendation.points = ["006081的持仓重合下限为12.3%。"]
    monkeypatch.setattr(
        deepseek_client,
        "build_offline_fund_recommendations",
        lambda *_args, **_kwargs: [recommendation],
    )
    monkeypatch.setattr(
        deepseek_client,
        "_apply_recommendation_guards_by_holding_order",
        lambda fund_recs, portfolio, *_args, **_kwargs: (portfolio, fund_recs),
    )
    monkeypatch.setattr(
        deepseek_client,
        "apply_news_citation_guards",
        lambda recommendations, *_args, **_kwargs: recommendations,
    )
    monkeypatch.setattr(
        deepseek_client,
        "canonicalize_fund_recommendations",
        lambda recommendations, *_args, **_kwargs: recommendations,
    )

    report = _offline_report(
        _request(),
        _risk(),
        [],
        market_news=[],
        topic_briefs=[],
        analysis_bundle=_bundle(with_lookthrough=False),
    )

    sanitized = report.fund_recommendations[0]
    assert sanitized.points == ["缺少可核验的基金持仓穿透事实，相关叙述已省略。"]
    assert sanitized.action == "分批买入"
    assert sanitized.amount_yuan == 1_234
    assert report.analysis_facts["fund_lookthrough_claim_audit"]["facts_status"] == (
        "unavailable"
    )

    validated_twice = _validate_daily_fund_lookthrough_claims(report)
    assert validated_twice.fund_recommendations[0].points == sanitized.points
    assert validated_twice.fund_recommendations[0].action == sanitized.action
    assert validated_twice.fund_recommendations[0].amount_yuan == sanitized.amount_yuan
    assert validated_twice.analysis_facts["fund_lookthrough_claim_audit"]["status"] == (
        "clean"
    )


def test_discovery_online_builder_validates_after_allocation_guard(monkeypatch) -> None:
    full_lookthrough = _lookthrough_facts()
    discovery_facts = {
        "portfolio_snapshot": {"authoritative": True, "stale": False},
        "portfolio_gap": {"available_budget_yuan": 10_000, "holdings_slim": []},
        "data_evidence_guard": {"execution_blocked": False},
        "fund_lookthrough": deepcopy(full_lookthrough),
    }
    original_facts = deepcopy(discovery_facts)
    captured: dict = {}

    monkeypatch.setattr(
        discovery_client,
        "prepare_recommendations_for_deterministic_allocation",
        lambda recommendations, **_kwargs: recommendations,
    )
    monkeypatch.setattr(
        discovery_client,
        "apply_discovery_guards",
        lambda recommendations, **_kwargs: (recommendations, [], []),
    )

    def allocate(recommendations, **_kwargs):
        recommendations[0].action = "分批买入"
        recommendations[0].suggested_amount_yuan = 2_468
        return recommendations, {"status": "allocated"}, {"status": "ok"}, []

    monkeypatch.setattr(
        discovery_client,
        "apply_deterministic_discovery_allocation",
        allocate,
    )

    def capture_validator(report, fund_lookthrough):
        captured["fund_lookthrough"] = deepcopy(fund_lookthrough)
        return validate_fund_lookthrough_claims(report, fund_lookthrough)

    monkeypatch.setattr(
        discovery_client,
        "validate_fund_lookthrough_claims",
        capture_validator,
    )

    parsed = {
        "title": "荐基",
        "summary": "普通摘要。",
        "market_view": "普通市场观点。",
        "recommendations": [
            {
                "fund_code": "006081",
                "fund_name": "测试候选基金",
                "sector_name": "测试板块",
                "action": "模型建议",
                "suggested_amount_yuan": 99_999,
                "points": ["006081与组合0%重合，因此完全分散，建议买入。"],
            }
        ],
        "caveats": [],
    }
    original_parsed = deepcopy(parsed)

    report = build_discovery_report_from_parsed(
        parsed,
        target_sectors=["测试板块"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=[],
        discovery_facts=discovery_facts,
        profile=InvestorProfile(),
        held_codes=set(),
        budget_yuan=10_000,
        sector_heat=[],
    )

    recommendation = report.recommendations[0]
    assert recommendation.points == [
        "低或未观察到的披露重合不能证明完整组合更分散，也不能作为买入理由。"
    ]
    assert recommendation.action == "分批买入"
    assert recommendation.suggested_amount_yuan == 2_468
    assert captured["fund_lookthrough"] == full_lookthrough
    assert parsed == original_parsed
    assert "fund_lookthrough_claim_audit" not in original_facts
    assert "fund_lookthrough_claim_audit" not in report.discovery_facts["fund_lookthrough"]
    assert report.discovery_facts["fund_lookthrough_claim_audit"]["status"] == (
        "sanitized"
    )


def test_discovery_offline_builder_attaches_clean_unavailable_audit() -> None:
    discovery_facts = {
        "portfolio_snapshot": {"authoritative": True, "stale": False},
        "portfolio_gap": {"available_budget_yuan": 5_000, "holdings_slim": []},
        "data_evidence_guard": {"execution_blocked": False},
    }

    report = build_offline_discovery_report(
        target_sectors=["测试板块"],
        candidate_pool=[
            {
                "fund_code": "006081",
                "fund_name": "测试候选基金",
                "sector_label": "测试板块",
                "fund_quality_score": 80,
                "quality_gate": {"status": "eligible"},
            }
        ],
        discovery_facts=discovery_facts,
        profile=InvestorProfile(),
        focus_sectors=[],
    )

    audit = report.discovery_facts["fund_lookthrough_claim_audit"]
    assert audit["schema_version"] == CLAIM_AUDIT_SCHEMA_VERSION
    assert audit["status"] == "clean"
    assert audit["facts_status"] == "unavailable"


def test_sync_sse_background_and_offline_paths_share_validated_builders() -> None:
    daily_sync = inspect.getsource(deepseek_client.DeepSeekClient.generate_report)
    daily_sse = inspect.getsource(analyze_streaming.stream_analysis)
    daily_background = inspect.getsource(analyze_pipeline.run_analysis)
    daily_job = inspect.getsource(job_store._run_job)

    assert "_build_final_report(" in daily_sync
    assert "_offline_report(" in daily_sync
    assert "_build_final_report(" in daily_sse
    assert "_offline_report(" in daily_sse
    assert "DeepSeekClient().generate_report(" in daily_background
    assert "run_analysis(" in daily_job
    assert "_validate_daily_fund_lookthrough_claims(report)" in inspect.getsource(
        deepseek_client._build_final_report
    )
    assert "_validate_daily_fund_lookthrough_claims(report)" in inspect.getsource(
        deepseek_client._offline_report
    )

    discovery_sync = inspect.getsource(discovery_client.DiscoveryClient.generate_report)
    discovery_sse = inspect.getsource(discovery_streaming.stream_discovery)
    discovery_background = inspect.getsource(discovery_pipeline.run_discovery)
    discovery_job = inspect.getsource(discovery_job_store._run_job)

    assert "build_discovery_report_from_parsed(" in discovery_sync
    assert "build_offline_discovery_report(" in discovery_sync
    assert "build_discovery_report_from_parsed(" in discovery_sse
    assert "build_offline_discovery_report(" in discovery_sse
    assert "client.generate_report(" in discovery_background
    assert "run_discovery(" in discovery_job
    assert "_validate_discovery_fund_lookthrough_claims(report)" in inspect.getsource(
        discovery_client.build_discovery_report_from_parsed
    )
    assert "_validate_offline_discovery_fund_lookthrough_claims(report)" in inspect.getsource(
        build_offline_discovery_report
    )
