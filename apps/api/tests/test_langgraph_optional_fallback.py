from __future__ import annotations

from types import SimpleNamespace

from app.models import (
    AnalysisRequest,
    DiscoveryRequest,
    FundDiscoveryReport,
    Holding,
    InvestorProfile,
    Report,
    RiskAssessment,
)
from app.services import analyze_pipeline, discovery_pipeline


def _discovery_request() -> DiscoveryRequest:
    return DiscoveryRequest(
        profile=InvestorProfile(),
        holdings=[Holding(fund_code="000001", fund_name="测试基金", holding_amount=10_000)],
    )


def _analysis_request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[Holding(fund_code="000001", fund_name="测试基金", holding_amount=10_000)],
    )


def test_run_discovery_falls_back_when_langgraph_missing(monkeypatch) -> None:
    report = FundDiscoveryReport(title="linear fallback", summary="ok")
    monkeypatch.setattr(
        discovery_pipeline,
        "get_settings",
        lambda: SimpleNamespace(langgraph_enabled=True),
    )
    monkeypatch.setattr(
        discovery_pipeline,
        "_run_discovery_via_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ImportError("No module named 'langgraph'")),
    )
    monkeypatch.setattr(
        discovery_pipeline,
        "run_discovery_impl",
        lambda *_args, **_kwargs: report,
    )

    assert discovery_pipeline.run_discovery(_discovery_request()) is report


def test_run_analysis_falls_back_when_langgraph_missing(monkeypatch) -> None:
    report = Report(
        title="linear fallback",
        summary="ok",
        holdings=[],
        recommendations=[],
        caveats=[],
        risk=RiskAssessment(
            level="medium",
            suggested_action="watch",
            weighted_return_percent=0,
            alerts=[],
        ),
    )
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(langgraph_enabled=True),
    )
    monkeypatch.setattr(
        analyze_pipeline,
        "_run_analysis_via_graph",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ImportError("No module named 'langgraph'")),
    )
    monkeypatch.setattr(
        analyze_pipeline,
        "run_analysis_linear",
        lambda *_args, **_kwargs: report,
    )

    assert analyze_pipeline.run_analysis(_analysis_request()) is report
