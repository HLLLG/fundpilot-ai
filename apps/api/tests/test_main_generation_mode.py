from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import main
from app.models import AnalysisRequest, DiscoveryRequest, Holding, InvestorProfile


def _holding() -> Holding:
    return Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=1000,
    )


def test_sync_analysis_upgrades_legacy_fast_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    class ReportStub:
        def model_dump(self, *, mode: str) -> dict[str, str]:
            assert mode == "json"
            return {"analysis_mode": captured[-1]}

    monkeypatch.setattr(
        main,
        "run_analysis",
        lambda request: captured.append(request.analysis_mode) or ReportStub(),
    )

    response = main.analyze(AnalysisRequest(holdings=[_holding()], analysis_mode="fast"))

    assert captured == ["deep"]
    assert response["analysis_mode"] == "deep"


def test_async_generation_jobs_are_always_deep(monkeypatch: pytest.MonkeyPatch) -> None:
    holding = _holding()
    analysis_modes: list[str] = []
    discovery_modes: list[str] = []
    monkeypatch.setattr(
        main,
        "resolve_portfolio_preflight",
        lambda *_args, **_kwargs: SimpleNamespace(holdings=[holding]),
    )
    monkeypatch.setattr(
        main,
        "create_analysis_job",
        lambda request: analysis_modes.append(request.analysis_mode) or "analysis-1",
    )
    monkeypatch.setattr(
        main,
        "create_discovery_job",
        lambda request: discovery_modes.append(request.analysis_mode) or "discovery-1",
    )

    analysis_response = main.analyze_async(
        AnalysisRequest(holdings=[holding], analysis_mode="fast")
    )
    discovery_response = main.fund_discovery_async(
        DiscoveryRequest(
            profile=InvestorProfile(),
            holdings=[holding],
            analysis_mode="fast",
        )
    )

    assert analysis_response == {"job_id": "analysis-1", "status": "pending"}
    assert discovery_response == {"job_id": "discovery-1", "status": "pending"}
    assert analysis_modes == ["deep"]
    assert discovery_modes == ["deep"]
