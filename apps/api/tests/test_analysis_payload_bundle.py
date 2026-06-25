"""analysis_facts 单次计算 bundle 复用。"""

from app.models import AnalysisRequest, FundSnapshot, Holding, InvestorProfile, RiskAssessment
from app.services.analysis_payload import (
    AnalysisFactsBundle,
    build_user_payload,
    finalize_analysis_facts,
    prepare_analysis_bundle,
)


def _minimal_request() -> AnalysisRequest:
    profile = InvestorProfile(
        decision_style="conservative",
        max_drawdown_percent=15,
        concentration_limit_percent=30,
        expected_investment_amount=100000,
    )
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name="半导体",
            holding_amount=10000,
        ),
    ]
    return AnalysisRequest(holdings=holdings, profile=profile)


def _minimal_risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        weighted_return_percent=1.2,
        suggested_action="watch",
        alerts=[],
    )


def test_build_user_payload_reuses_analysis_bundle(monkeypatch):
    request = _minimal_request()
    risk = _minimal_risk()
    snapshots = [
        FundSnapshot(
            fund_code="519674",
            fund_name="银河创新成长",
            latest_nav=1.0,
            source="test",
        ),
    ]
    build_calls = {"count": 0}
    original = __import__(
        "app.services.analysis_facts",
        fromlist=["build_analysis_facts"],
    ).build_analysis_facts

    def _counting_build(*args, **kwargs):
        build_calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.analysis_payload.build_analysis_facts",
        _counting_build,
    )
    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: ({}, None, None, None),
    )

    bundle = AnalysisFactsBundle(
        session={"session_kind": "trading_day_intraday"},
        factor_scores=None,
        risk_metrics=None,
        portfolio_trend=None,
        facts={"readonly": True, "holdings": [], "portfolio": {}},
    )
    payload = build_user_payload(
        request,
        risk,
        snapshots,
        [],
        analysis_bundle=bundle,
    )
    assert build_calls["count"] == 0
    assert payload["analysis_facts"]["readonly"] is True


def test_finalize_analysis_facts_overlays_pipeline():
    base = {"readonly": True, "holdings": [], "news": {"freshness_label": "old"}}
    pipeline = {"analysis_mode": "deep", "model": "deepdestination"}
    finalized = finalize_analysis_facts(
        base,
        market_news=[],
        topic_briefs=[],
        pipeline=pipeline,
    )
    assert finalized["pipeline"] == pipeline
    assert "news" in finalized


def test_prepare_analysis_bundle_calls_build_once(monkeypatch):
    request = _minimal_request()
    risk = _minimal_risk()
    snapshots = [
        FundSnapshot(
            fund_code="519674",
            fund_name="银河创新成长",
            latest_nav=1.0,
            source="test",
        ),
    ]
    build_calls = {"count": 0}

    def _fake_build(*_args, **_kwargs):
        build_calls["count"] += 1
        return {"readonly": True, "holdings": [], "portfolio": {}}

    monkeypatch.setattr(
        "app.services.analysis_payload.build_analysis_facts",
        _fake_build,
    )
    monkeypatch.setattr(
        "app.services.analysis_payload._compute_analysis_context",
        lambda *_args, **_kwargs: ({}, None, None, None),
    )
    bundle = prepare_analysis_bundle(request, risk, snapshots, [])
    build_user_payload(request, risk, snapshots, [], analysis_bundle=bundle)
    assert build_calls["count"] == 1
