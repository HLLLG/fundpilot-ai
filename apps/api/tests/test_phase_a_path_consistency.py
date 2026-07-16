from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models import (
    AnalysisRequest,
    DiscoveryRecommendation,
    DiscoveryRequest,
    FundDiscoveryReport,
    FundRecommendation,
    Holding,
    InvestorProfile,
    Report,
    RiskAssessment,
)
from app.services import (
    analyze_pipeline,
    analyze_streaming,
    discovery_job_store,
    discovery_pipeline,
    discovery_streaming,
    job_store,
)
from app.services.decision_data_evidence import PortfolioPreflightResult
from app.services.deepseek_client import _apply_recommendation_guards_by_holding_order
from app.services.discovery_guard import apply_discovery_guards
from app.services.recommendations import canonicalize_fund_recommendations


RAW_DAILY_ACTION = "RAW_DAILY_UNGUARDED_ACTION"
RAW_DAILY_AMOUNT = "RAW_DAILY_AMOUNT_999999"
RAW_DISCOVERY_ACTION = "RAW_DISCOVERY_UNGUARDED_BUY"
RAW_DISCOVERY_AMOUNT = "RAW_DISCOVERY_AMOUNT_999999"


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level="medium",
        suggested_action="watch",
        weighted_return_percent=0,
        alerts=[],
    )


def _preflight(holdings: list[Holding]) -> PortfolioPreflightResult:
    return PortfolioPreflightResult(
        holdings=list(holdings),
        context={
            "authoritative": True,
            "stale": False,
            "position_complete": True,
            "pending_transaction_count": 0,
        },
    )


@pytest.fixture
def daily_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AnalysisRequest, Report]:
    holdings = [
        Holding(fund_code="000000", fund_name="未知基金甲", holding_amount=1_000),
        Holding(fund_code="000000", fund_name="未知基金乙", holding_amount=2_000),
    ]
    request = AnalysisRequest(
        holdings=holdings,
        profile=InvestorProfile(
            expected_investment_amount=10_000,
            concentration_limit_percent=35,
        ),
        analysis_mode="fast",
    )
    raw = [
        FundRecommendation(
            fund_code="000000",
            fund_name="未知基金乙",
            action="分批加仓",
            amount_yuan=999_999,
            amount_note="建议立即一次性加仓 999999 元",
            points=["建议立即一次性加仓 999999 元"],
            decision_path="动作：立即加仓 999999 元",
            suggested_position_change_percent=100,
            suggested_position_change_basis="模型自由给值",
        ),
        FundRecommendation(
            fund_code="000000",
            fund_name="未知基金甲",
            action="清仓评估",
            amount_yuan=888_888,
            amount_note="建议立即清仓 888888 元",
            points=["建议立即清仓 888888 元"],
            decision_path="动作：立即清仓 888888 元",
            suggested_position_change_percent=100,
            suggested_position_change_basis="模型自由给值",
        ),
    ]
    canonical = canonicalize_fund_recommendations(raw, holdings)
    facts = {
        # A stale snapshot deliberately drives the deterministic execution block.
        "portfolio_snapshot": {
            "authoritative": True,
            "stale": True,
            "position_complete": True,
            "pending_transaction_count": 0,
        },
        "allowed_actions": ["观察", "风控复核"],
    }
    monkeypatch.setattr(
        "app.services.recommendation_guard.get_settings",
        lambda: SimpleNamespace(
            tactical_prompt_tuning_enabled=False,
            sector_signal_backtest_enabled=False,
            tactical_prompt_tuning_lookback_reports=0,
            sector_signal_backtest_days=0,
            news_require_today_for_add=False,
            decision_escalation_mode="shadow",
        ),
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.build_sector_momentum_context",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.recommendation_guard.summarize_sector_intraday_for_holding",
        lambda *_args, **_kwargs: None,
    )
    portfolio_lines, guarded = _apply_recommendation_guards_by_holding_order(
        canonical,
        ["建议立即一次性加仓 999999 元"],
        request,
        _risk(),
        [],
        [],
        nav_trends_by_code=None,
        facts=facts,
    )
    report = Report(
        id="phase-a-daily",
        title="A1 日报出口契约",
        risk=_risk(),
        holdings=holdings,
        summary="guarded",
        recommendations=portfolio_lines,
        fund_recommendations=guarded,
        caveats=[],
        analysis_facts=facts,
    )
    return request, report


@pytest.fixture
def discovery_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DiscoveryRequest, FundDiscoveryReport]:
    profile = InvestorProfile(
        avoid_chasing=False,
        expected_investment_amount=100_000,
        concentration_limit_percent=30,
    )
    request = DiscoveryRequest(
        holdings=[],
        profile=profile,
        budget_yuan=50_000,
        focus_sectors=["半导体"],
        analysis_mode="fast",
    )
    candidates = [
        {
            "fund_code": code,
            "fund_name": name,
            "sector_label": "半导体",
            "fund_quality_score": 80,
            "sector_fit_score": 36,
            "quality_gate": {
                "status": "eligible",
                "eligible": True,
                "reasons": [],
            },
            "tradeability": {
                "data_status": "complete",
                "freshness": "fresh",
                "purchase_state": "open",
                "redemption_state": "open",
                "currency": "CNY",
                "minimum_purchase_yuan": 10.0,
                "daily_purchase_limit_yuan": None,
                "daily_purchase_limit_unlimited": True,
                "standard_purchase_fee_tiers": [
                    {
                        "condition": "全部",
                        "fee_type": "percent",
                        "fee_percent": 0.0,
                        "flat_fee_yuan": None,
                        "min_amount_yuan": None,
                        "max_amount_yuan": None,
                        "source_rate": "standard_undiscounted",
                    }
                ],
                "redemption_fee_tiers": [
                    {
                        "condition": "大于等于0天",
                        "min_days": 0,
                        "max_days": None,
                        "fee_percent": 0.0,
                    }
                ],
                "sales_service_fee_annual_percent": 0.0,
                "sales_service_fee_status": "known_zero",
                "fee_freshness": "fresh",
                "source_ids": ["pytest.tradeability"],
            },
        }
        for code, name in (("000001", "候选基金甲"), ("000002", "候选基金乙"))
    ]
    raw = [
        DiscoveryRecommendation(
            fund_code=item["fund_code"],
            fund_name=f"模型伪造名称-{item['fund_code']}",
            sector_name="模型伪造板块",
            action="分批买入",
            suggested_amount_yuan=999_999,
            amount_note="建议立即一次性买入 999999 元",
            points=["建议立即一次性买入 999999 元"],
            decision_path="动作：立即买入 999999 元",
            suggested_position_change_percent=100,
            suggested_position_change_basis="模型自由给值",
        )
        for item in candidates
    ]
    facts = {
        "portfolio_snapshot": {
            "authoritative": True,
            "stale": False,
            "position_complete": True,
            "pending_transaction_count": 0,
        },
        "portfolio_position_truth": {
            "position_complete": True,
            "cash": {"known": True, "balance_yuan": 50_000},
            "positions": [],
        },
        "portfolio_gap": {
            "weight_denominator_yuan": 100_000,
            "holdings_slim": [],
        },
        "candidate_pool": candidates,
    }
    monkeypatch.setattr(
        "app.services.discovery_guard.get_settings",
        lambda: SimpleNamespace(decision_escalation_mode="shadow"),
    )
    guarded, caveats, eliminated = apply_discovery_guards(
        raw,
        candidate_pool=candidates,
        held_codes=set(),
        profile=profile,
        budget_yuan=50_000,
        sector_heat=[],
        discovery_facts=facts,
    )
    report = FundDiscoveryReport(
        id="phase-a-discovery",
        title="A1 荐基出口契约",
        summary="guarded",
        focus_sectors=["半导体"],
        target_sectors=["半导体"],
        candidate_pool=candidates,
        recommendations=guarded,
        eliminated_candidates=eliminated,
        discovery_facts=facts,
        caveats=caveats,
        analysis_mode="fast",
    )
    return request, report


def _daily_projection(payload: dict) -> list[dict]:
    return [
        {
            "identity": (item["fund_code"], item["fund_name"]),
            "action": item["action"],
            "amount": item.get("amount_yuan"),
            "position": item.get("suggested_position_change_percent"),
            "guard_notes": tuple(item.get("validation_notes") or []),
        }
        for item in payload["fund_recommendations"]
    ]


def _discovery_projection(payload: dict) -> list[dict]:
    return [
        {
            "identity": (item["fund_code"], item["fund_name"], item["sector_name"]),
            "action": item["action"],
            "amount": item.get("suggested_amount_yuan"),
            "amount_note": item.get("amount_note"),
            "final_projection": tuple(
                point
                for point in item.get("points") or []
                if "系统校验后的最终动作" in point
            ),
            "guard_notes": tuple(item.get("validation_notes") or []),
        }
        for item in payload["recommendations"]
    ]


def _run_daily_service(
    monkeypatch: pytest.MonkeyPatch,
    request: AnalysisRequest,
    report: Report,
) -> Report:
    monkeypatch.setattr(
        analyze_pipeline,
        "resolve_portfolio_preflight",
        lambda holdings, **_kwargs: _preflight(holdings),
    )
    monkeypatch.setattr(
        analyze_pipeline,
        "FundProfileService",
        lambda: MagicMock(resolve_holdings=lambda holdings: holdings),
    )
    monkeypatch.setattr(analyze_pipeline, "evaluate_portfolio_risk", lambda *_args: _risk())
    monkeypatch.setattr(
        analyze_pipeline,
        "FundDataService",
        lambda: MagicMock(get_snapshots_with_nav_trends=lambda _holdings: ([], {})),
    )
    monkeypatch.setattr(
        analyze_pipeline,
        "DeepSeekClient",
        lambda: MagicMock(generate_report=lambda *_args, **_kwargs: report),
    )
    monkeypatch.setattr(analyze_pipeline, "save_report", lambda value: value)
    return analyze_pipeline.run_analysis(request)


def _run_daily_stream(
    monkeypatch: pytest.MonkeyPatch,
    request: AnalysisRequest,
    report: Report,
) -> list[dict]:
    monkeypatch.setattr(
        analyze_streaming,
        "get_settings",
        lambda: SimpleNamespace(
            deepseek_configured=True,
            deepseek_max_tokens_report=1_024,
        ),
    )
    monkeypatch.setattr(
        analyze_streaming,
        "resolve_portfolio_preflight",
        lambda holdings, **_kwargs: _preflight(holdings),
    )
    monkeypatch.setattr(
        analyze_streaming,
        "FundProfileService",
        lambda: MagicMock(resolve_holdings=lambda holdings: holdings),
    )
    monkeypatch.setattr(analyze_streaming, "evaluate_portfolio_risk", lambda *_args: _risk())
    monkeypatch.setattr(
        analyze_streaming,
        "FundDataService",
        lambda: MagicMock(get_snapshots_with_nav_trends=lambda _holdings: ([], {})),
    )
    monkeypatch.setattr(
        analyze_streaming,
        "NewsService",
        lambda: MagicMock(prefetch_for_holdings=lambda *_args, **_kwargs: []),
    )
    monkeypatch.setattr(analyze_streaming, "_build_topic_briefs", lambda *_args: [])
    monkeypatch.setattr(
        analyze_streaming,
        "prepare_analysis_bundle",
        lambda *_args, **_kwargs: SimpleNamespace(facts={}),
    )
    monkeypatch.setattr(
        analyze_streaming,
        "resolve_analysis_runtime",
        lambda *_args: SimpleNamespace(
            mode="fast",
            model="test",
            news_max_topics=0,
            news_retrieval_policy="bounded_prefetch.v1",
            news_tool_rounds_configured=0,
            news_tool_rounds_executed=0,
        ),
    )
    monkeypatch.setattr(
        analyze_streaming,
        "build_analysis_chat_messages",
        lambda *_args, **_kwargs: [],
    )

    def raw_stream(**_kwargs):
        yield (
            '{"title":"raw","summary":"'
            + RAW_DAILY_ACTION
            + '","fund_recommendations":['
        )
        yield (
            '{"fund_code":"000000","fund_name":"未知基金甲",'
            '"action":"分批加仓","amount_yuan":999999,'
            f'"points":["{RAW_DAILY_AMOUNT}"]}}],"caveats":[]}}'
        )

    monkeypatch.setattr(analyze_streaming, "stream_chat_completion", raw_stream)
    monkeypatch.setattr(
        analyze_streaming,
        "judge_parsed_report",
        lambda parsed, *_args, **_kwargs: (parsed, {}),
    )
    monkeypatch.setattr(analyze_streaming, "_build_final_report", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(analyze_streaming, "save_report", lambda value: value)
    return list(analyze_streaming.stream_analysis(request, user_id=1))


def _patch_discovery_prep(
    monkeypatch: pytest.MonkeyPatch,
    module,
    report: FundDiscoveryReport,
) -> None:
    monkeypatch.setattr(
        module,
        "resolve_portfolio_preflight",
        lambda holdings, **_kwargs: _preflight(holdings),
    )
    monkeypatch.setattr(
        module,
        "build_sector_heat_ranking",
        lambda: [{"sector_label": "半导体", "heat_score": 1}],
    )
    monkeypatch.setattr(
        module,
        "select_target_sectors",
        lambda *_args, **_kwargs: ["半导体"],
    )
    monkeypatch.setattr(
        module,
        "build_sector_flow_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        module,
        "build_sector_divergence_map_for_opportunities",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(module, "select_sector_opportunities", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "build_candidate_pool",
        lambda *_args, **_kwargs: report.candidate_pool,
    )
    monkeypatch.setattr(module, "enrich_candidates", lambda pool: pool)
    monkeypatch.setattr(module, "finalize_candidate_pool", lambda pool, *_args, **_kwargs: pool)
    monkeypatch.setattr(
        module,
        "NewsService",
        lambda: MagicMock(prefetch_topics=lambda _topics: []),
    )
    monkeypatch.setattr(module, "summarize_all_topics", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        module,
        "build_discovery_facts",
        lambda **_kwargs: report.discovery_facts,
    )
    monkeypatch.setattr(
        module,
        "attach_discovery_data_evidence",
        lambda facts, **_kwargs: facts,
    )


def _run_discovery_service(
    monkeypatch: pytest.MonkeyPatch,
    request: DiscoveryRequest,
    report: FundDiscoveryReport,
) -> FundDiscoveryReport:
    _patch_discovery_prep(monkeypatch, discovery_pipeline, report)
    monkeypatch.setattr(
        discovery_pipeline,
        "DiscoveryClient",
        lambda: MagicMock(generate_report=lambda **_kwargs: report),
    )
    monkeypatch.setattr(discovery_pipeline, "save_discovery_report", lambda value: value)
    return discovery_pipeline.run_discovery(request)


def _run_discovery_stream(
    monkeypatch: pytest.MonkeyPatch,
    request: DiscoveryRequest,
    report: FundDiscoveryReport,
) -> list[dict]:
    _patch_discovery_prep(monkeypatch, discovery_streaming, report)
    monkeypatch.setattr(
        discovery_streaming,
        "get_settings",
        lambda: SimpleNamespace(
            deepseek_configured=True,
            deepseek_max_tokens_report=1_024,
        ),
    )
    monkeypatch.setattr(
        discovery_streaming,
        "resolve_analysis_runtime",
        lambda *_args: SimpleNamespace(
            mode="fast",
            model="test",
            news_max_topics=3,
            news_tool_max_rounds=0,
            news_retrieval_policy="bounded_prefetch.v1",
            news_tool_rounds_configured=0,
            news_tool_rounds_executed=0,
        ),
    )
    monkeypatch.setattr(
        discovery_streaming,
        "DiscoveryClient",
        lambda: SimpleNamespace(_system_prompt=lambda *_args, **_kwargs: "system"),
    )
    monkeypatch.setattr(discovery_streaming, "build_user_payload", lambda **_kwargs: {})
    monkeypatch.setattr(
        discovery_streaming,
        "append_output_requirements_to_system",
        lambda value: value,
    )

    def raw_stream(**_kwargs):
        yield (
            '{"title":"raw","summary":"'
            + RAW_DISCOVERY_ACTION
            + '","recommendations":['
        )
        yield (
            '{"fund_code":"000001","fund_name":"伪造",'
            '"sector_name":"伪造","action":"分批买入",'
            '"suggested_amount_yuan":999999,'
            f'"points":["{RAW_DISCOVERY_AMOUNT}"]}}],"caveats":[]}}'
        )

    monkeypatch.setattr(discovery_streaming, "stream_chat_completion", raw_stream)
    monkeypatch.setattr(
        discovery_streaming,
        "judge_parsed_discovery_report",
        lambda parsed, **_kwargs: (parsed, {}),
    )
    monkeypatch.setattr(
        discovery_streaming,
        "build_discovery_report_from_parsed",
        lambda *_args, **_kwargs: report,
    )
    monkeypatch.setattr(discovery_streaming, "save_discovery_report", lambda value: value)
    return list(discovery_streaming.stream_discovery(request, user_id=1))


def _daily_background_payload(
    monkeypatch: pytest.MonkeyPatch,
    request: AnalysisRequest,
    report: Report,
) -> dict:
    updates: list[dict] = []
    monkeypatch.setattr(job_store, "_load_request", lambda _job_id: request)
    monkeypatch.setattr(job_store, "run_analysis", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        job_store,
        "_update_job",
        lambda _job_id, **values: updates.append(values),
    )
    job_store._run_job("daily-job", 1)
    assert updates[-1]["status"] == "completed"
    assert updates[-1]["report_id"] == report.id

    payload = report.model_dump(mode="json")
    monkeypatch.setattr(
        job_store,
        "get_job",
        lambda _job_id: {
            "id": "daily-job",
            "status": "completed",
            "request": request.model_dump(mode="json"),
            "report_id": report.id,
            "error": None,
            "stage": "completed",
            "stage_label": "completed",
            "created_at": "2026-07-14T00:00:00+00:00",
            "updated_at": "2026-07-14T00:00:00+00:00",
        },
    )
    monkeypatch.setattr("app.database.get_report", lambda _report_id: payload)
    return job_store.get_job_response("daily-job")["report"]


def _discovery_background_payload(
    monkeypatch: pytest.MonkeyPatch,
    request: DiscoveryRequest,
    report: FundDiscoveryReport,
) -> dict:
    updates: list[dict] = []
    monkeypatch.setattr(discovery_job_store, "_load_request", lambda _job_id: request)
    monkeypatch.setattr(discovery_job_store, "run_discovery", lambda *_args, **_kwargs: report)
    monkeypatch.setattr(
        discovery_job_store,
        "_update_job",
        lambda _job_id, **values: updates.append(values),
    )
    discovery_job_store._run_job("discovery-job", 1)
    assert updates[-1]["status"] == "completed"
    assert updates[-1]["discovery_report_id"] == report.id

    payload = report.model_dump(mode="json")
    monkeypatch.setattr(
        discovery_job_store,
        "get_discovery_job",
        lambda _job_id: {
            "id": "discovery-job",
            "status": "completed",
            "request": request.model_dump(mode="json"),
            "discovery_report_id": report.id,
            "error": None,
            "stage": "completed",
            "stage_label": "completed",
            "created_at": "2026-07-14T00:00:00+00:00",
            "updated_at": "2026-07-14T00:00:00+00:00",
        },
    )
    monkeypatch.setattr(discovery_job_store, "get_discovery_report", lambda _report_id: payload)
    return discovery_job_store.get_discovery_job_response("discovery-job")[
        "discovery_report"
    ]


def test_daily_final_projection_matches_service_sse_and_background(
    monkeypatch: pytest.MonkeyPatch,
    daily_contract: tuple[AnalysisRequest, Report],
) -> None:
    request, guarded_report = daily_contract
    service = _run_daily_service(monkeypatch, request, guarded_report).model_dump(mode="json")
    events = _run_daily_stream(monkeypatch, request, guarded_report)
    background = _daily_background_payload(monkeypatch, request, guarded_report)

    projection = _daily_projection(service)
    assert [item["identity"] for item in projection] == [
        ("000000", "未知基金甲"),
        ("000000", "未知基金乙"),
    ]
    assert [item["action"] for item in projection] == ["观察", "观察"]
    assert all(item["amount"] is None and item["position"] is None for item in projection)
    assert all(
        any("暂时关闭仓位操作" in note for note in item["guard_notes"])
        for item in projection
    )
    assert projection == _daily_projection(events[-1]["report"])
    assert projection == _daily_projection(background)

    assert events[-1]["type"] == "done"
    assert not [event for event in events if event.get("type") == "report_partial"]
    pre_done_text = repr(events[:-1])
    assert RAW_DAILY_ACTION not in pre_done_text
    assert RAW_DAILY_AMOUNT not in pre_done_text
    assert RAW_DAILY_ACTION not in repr(events)
    assert RAW_DAILY_AMOUNT not in repr(events)
    assert "999999" not in repr(service)


def test_discovery_final_projection_matches_service_sse_and_background(
    monkeypatch: pytest.MonkeyPatch,
    discovery_contract: tuple[DiscoveryRequest, FundDiscoveryReport],
) -> None:
    request, guarded_report = discovery_contract
    service = _run_discovery_service(monkeypatch, request, guarded_report).model_dump(
        mode="json"
    )
    events = _run_discovery_stream(monkeypatch, request, guarded_report)
    background = _discovery_background_payload(monkeypatch, request, guarded_report)

    projection = _discovery_projection(service)
    assert [item["identity"] for item in projection] == [
        ("000001", "候选基金甲", "半导体"),
        ("000002", "候选基金乙", "半导体"),
    ]
    assert sum(float(item["amount"] or 0) for item in projection) <= 15_000
    assert projection[0]["amount"] == 15_000
    assert projection[1]["amount"] is None
    assert projection[1]["action"] == "建议关注"
    assert all(
        item["final_projection"] and "系统" in (item["amount_note"] or "")
        for item in projection
    )
    assert all(len(item["final_projection"]) == 1 for item in projection)
    assert projection == _discovery_projection(events[-1]["report"])
    assert projection == _discovery_projection(background)

    assert events[-1]["type"] == "done"
    assert not [event for event in events if event.get("type") == "report_partial"]
    pre_done_text = repr(events[:-1])
    assert RAW_DISCOVERY_ACTION not in pre_done_text
    assert RAW_DISCOVERY_AMOUNT not in pre_done_text
    assert RAW_DISCOVERY_ACTION not in repr(events)
    assert RAW_DISCOVERY_AMOUNT not in repr(events)
    assert "999999" not in repr(service)
