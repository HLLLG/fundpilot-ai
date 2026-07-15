"""Focused discovery benchmark integration contracts (all external I/O stubbed)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import DiscoveryRequest, FundDiscoveryReport, InvestorProfile
from app.services.analysis_runtime import AnalysisRuntime
from app.services.decision_data_evidence import PortfolioPreflightResult
from app.services.discovery_candidate_llm import slim_candidate_for_llm
from app.services.discovery_payload import build_user_payload


_DECISION_AT = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)


def _profile() -> InvestorProfile:
    return InvestorProfile(
        decision_style="conservative",
        max_drawdown_percent=15,
        concentration_limit_percent=30,
        expected_investment_amount=100_000,
    )


def _qualified_metrics() -> dict[str, Any]:
    return {
        "schema_version": "fund_benchmark_research.v1",
        "status": "qualified",
        "qualified": True,
        "comparison_role": "formal_excess",
        "formal_excess_eligible": True,
        "benchmark_code": "000300",
        "benchmark_name": "沪深300",
        "effective_trade_date": "2026-07-13",
        "reason_codes": [],
        "alignment": {
            "common_return_sample_days": 260,
            "first_common_date": "2025-07-01",
            "last_common_date": "2026-07-13",
            "private_alignment_detail": "must_not_reach_llm",
        },
        "horizons": {
            "3m": {
                "status": "qualified",
                "start_date": "2026-04-13",
                "end_date": "2026-07-13",
                "fund_return_percent": 8.4,
                "benchmark_return_percent": 5.1,
                "formal_excess_return_percent": 3.3,
                "fund_max_drawdown_percent": -6.2,
                "benchmark_max_drawdown_percent": -7.0,
                "drawdown_advantage_percent": 0.8,
            },
            "5y": {
                "status": "qualified",
                "formal_excess_return_percent": 99.9,
            },
        },
        "rolling_comparison": {
            "window_days": 60,
            "window_count": 12,
            "formal_excess_win_rate_percent": 75.0,
            "difference_stability_percent": 68.0,
        },
        "tracking_metrics": {
            "applicable": False,
            "available": False,
            "tracking_difference_percent": None,
            "annualized_tracking_error_percent": None,
        },
        "raw_nav_points": ["must_not_reach_llm"],
    }


def _unavailable_metrics() -> dict[str, Any]:
    return {
        "schema_version": "fund_benchmark_research.v1",
        "status": "unavailable",
        "qualified": False,
        "comparison_role": "unavailable",
        "formal_excess_eligible": False,
        "benchmark_code": None,
        "benchmark_name": None,
        "effective_trade_date": "2026-07-13",
        "reason_codes": ["benchmark_spec_unavailable"],
        "alignment": {"common_return_sample_days": 0},
        "horizons": {},
        "rolling_comparison": {},
        "tracking_metrics": {"applicable": False, "available": False},
    }


def _request() -> DiscoveryRequest:
    return DiscoveryRequest(
        holdings=[],
        profile=_profile(),
        analysis_mode="fast",
        focus_sectors=["半导体"],
    )


def test_candidate_llm_distinguishes_qualified_and_unavailable_metrics() -> None:
    qualified = slim_candidate_for_llm(
        {"fund_code": "000001", "benchmark_metrics": _qualified_metrics()},
        sector_change_index={},
        trade_date=None,
    )["benchmark_metrics"]
    unavailable = slim_candidate_for_llm(
        {"fund_code": "000002", "benchmark_metrics": _unavailable_metrics()},
        sector_change_index={},
        trade_date=None,
    )["benchmark_metrics"]

    assert qualified["status"] == "qualified"
    assert qualified["qualified"] is True
    assert qualified["formal_excess_eligible"] is True
    assert qualified["descriptive_only"] is True
    assert qualified["execution_tilt_eligible"] is False
    assert qualified["horizons"]["3m"]["formal_excess_return_percent"] == 3.3
    assert "5y" not in qualified["horizons"]
    assert "raw_nav_points" not in qualified
    assert "private_alignment_detail" not in qualified["alignment"]

    assert unavailable["status"] == "unavailable"
    assert unavailable["qualified"] is False
    assert unavailable["formal_excess_eligible"] is False
    assert unavailable["execution_tilt_eligible"] is False
    assert unavailable["reason_codes"] == ["benchmark_spec_unavailable"]
    assert unavailable["horizons"] == {}


def test_discovery_payload_preserves_benchmark_research_contract() -> None:
    contract = {
        "schema_version": "fund_benchmark_research.v1",
        "calculation_policy": "strict_pit_aligned_before_generation",
        "fund_count": 2,
        "qualified_count": 1,
        "unavailable_count": 1,
    }
    payload = build_user_payload(
        discovery_facts={
            "session": {
                "calendar_date": "2026-07-14",
                "effective_trade_date": "2026-07-13",
            },
            "candidate_pool": [],
            "benchmark_research_contract": contract,
        },
        profile=_profile(),
        focus_sectors=["半导体"],
    )

    assert payload["discovery_facts"]["benchmark_research_contract"] == contract


def _benchmark_specs() -> dict[str, dict[str, Any]]:
    return {
        "000001": {
            "schema_version": "fund_benchmark_mapping.v1",
            "tier": "fund_contract_exact",
            "benchmark_code": "000300",
            "benchmark_name": "沪深300",
            "formal_excess_eligible": True,
        },
        "000002": {
            "schema_version": "fund_benchmark_mapping.v1",
            "tier": "unavailable",
            "benchmark_code": None,
            "benchmark_name": None,
            "formal_excess_eligible": False,
        },
    }


def _benchmark_metrics() -> dict[str, dict[str, Any]]:
    return {
        "000001": _qualified_metrics(),
        "000002": _unavailable_metrics(),
    }


def _patch_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    module_name: str,
) -> dict[str, Any]:
    captured: dict[str, Any] = {"benchmark_batch_calls": 0}
    runtime = AnalysisRuntime(
        mode="fast",
        model="offline-test",
        news_enabled=False,
        news_max_topics=0,
        news_tool_max_rounds=0,
    )
    candidates = [
        {"fund_code": "000001", "fund_name": "合格基金", "sector_label": "半导体"},
        {"fund_code": "000002", "fund_name": "无基准基金", "sector_label": "半导体"},
    ]

    monkeypatch.setattr(
        f"{module_name}.capture_decision_clock",
        lambda: SimpleNamespace(decision_at=_DECISION_AT),
    )
    monkeypatch.setattr(
        f"{module_name}.get_settings",
        lambda: SimpleNamespace(deepseek_configured=False),
    )
    monkeypatch.setattr(
        f"{module_name}.resolve_analysis_runtime",
        lambda _settings, _mode: runtime,
    )
    monkeypatch.setattr(
        f"{module_name}.resolve_portfolio_preflight",
        lambda holdings, **_kwargs: PortfolioPreflightResult(
            holdings=list(holdings), context={"source": "test"}
        ),
    )
    monkeypatch.setattr(
        f"{module_name}.build_sector_heat_ranking",
        lambda **_kwargs: [{"sector_label": "半导体", "heat_score": 1.0}],
    )
    monkeypatch.setattr(
        f"{module_name}.select_target_sectors",
        lambda *_args, **_kwargs: ["半导体"],
    )
    monkeypatch.setattr(
        f"{module_name}.build_sector_flow_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        f"{module_name}.build_sector_divergence_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        f"{module_name}.select_sector_opportunities",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        f"{module_name}.build_candidate_pool",
        lambda *_args, **_kwargs: [dict(row) for row in candidates],
    )
    monkeypatch.setattr(
        f"{module_name}.enrich_candidates",
        lambda pool, **_kwargs: pool,
    )
    monkeypatch.setattr(
        f"{module_name}.finalize_candidate_pool",
        lambda pool, *_args, **_kwargs: pool,
    )
    monkeypatch.setattr(
        f"{module_name}.load_decision_benchmark_specs",
        lambda *_args, **_kwargs: _benchmark_specs(),
    )
    monkeypatch.setattr(
        f"{module_name}.attach_candidate_benchmark_research",
        lambda pool, specs, **_kwargs: [
            {**row, "benchmark_spec": specs[str(row["fund_code"])]} for row in pool
        ],
    )

    def fake_benchmark_batch(pool: list[dict], **_kwargs: Any) -> dict[str, dict[str, Any]]:
        captured["benchmark_batch_calls"] += 1
        captured["benchmark_batch_pool"] = pool
        return _benchmark_metrics()

    monkeypatch.setattr(
        f"{module_name}.build_fund_benchmark_research_batch",
        fake_benchmark_batch,
    )

    class FakeNewsService:
        def prefetch_topics(self, _topics: list[str], **_kwargs: Any) -> list[Any]:
            return []

    monkeypatch.setattr(f"{module_name}.NewsService", FakeNewsService)
    monkeypatch.setattr(
        f"{module_name}.summarize_all_topics",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        f"{module_name}.build_discovery_facts",
        lambda **kwargs: {"candidate_pool": kwargs["candidate_pool"]},
    )
    monkeypatch.setattr(
        f"{module_name}.attach_discovery_data_evidence",
        lambda facts, **_kwargs: facts,
    )
    monkeypatch.setattr(f"{module_name}.save_discovery_report", lambda report: report)
    return captured


def _assert_integrated_contract(captured_facts: dict[str, Any]) -> None:
    contract = captured_facts["benchmark_research_contract"]
    assert contract["schema_version"] == "fund_benchmark_research.v1"
    assert contract["calculation_policy"] == "strict_pit_aligned_before_generation"
    assert contract["fund_count"] == 2
    assert contract["qualified_count"] == 1
    assert contract["formal_excess_count"] == 1
    assert contract["unavailable_count"] == 1
    pool = captured_facts["candidate_pool"]
    assert pool[0]["benchmark_metrics"]["status"] == "qualified"
    assert pool[1]["benchmark_metrics"]["status"] == "unavailable"


def test_sync_discovery_writes_benchmark_research_contract_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import discovery_pipeline

    captured = _patch_path(
        monkeypatch,
        module_name="app.services.discovery_pipeline",
    )

    class FakeDiscoveryClient:
        def generate_report(self, **kwargs: Any) -> FundDiscoveryReport:
            captured["facts"] = kwargs["discovery_facts"]
            return FundDiscoveryReport(
                title="同步测试",
                discovery_facts=kwargs["discovery_facts"],
                candidate_pool=kwargs["candidate_pool"],
            )

    monkeypatch.setattr(discovery_pipeline, "DiscoveryClient", FakeDiscoveryClient)

    report = discovery_pipeline.run_discovery(_request())

    assert report.title == "同步测试"
    assert captured["benchmark_batch_calls"] == 1
    _assert_integrated_contract(captured["facts"])


def test_sse_discovery_writes_benchmark_research_contract_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import discovery_streaming

    captured = _patch_path(
        monkeypatch,
        module_name="app.services.discovery_streaming",
    )
    monkeypatch.setattr(discovery_streaming, "build_pipeline_metadata", lambda **_kwargs: {})

    def fake_offline_report(**kwargs: Any) -> FundDiscoveryReport:
        captured["facts"] = kwargs["discovery_facts"]
        return FundDiscoveryReport(
            title="SSE 测试",
            discovery_facts=kwargs["discovery_facts"],
            candidate_pool=kwargs["candidate_pool"],
        )

    monkeypatch.setattr(
        discovery_streaming,
        "build_offline_discovery_report",
        fake_offline_report,
    )

    events = list(discovery_streaming.stream_discovery(_request(), user_id=1))

    assert events[-1]["type"] == "done"
    assert events[-1]["report"]["title"] == "SSE 测试"
    assert captured["benchmark_batch_calls"] == 1
    _assert_integrated_contract(captured["facts"])
