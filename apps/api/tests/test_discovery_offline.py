from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import InvestorProfile
from app.services.deepseek_http import ProviderFailure
from app.services.discovery_offline import build_offline_discovery_report
from app.services.discovery_recommendation_scope import RECOMMENDATION_SCOPE_VERSION


DECISION_AT = datetime(2026, 8, 6, 11, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_provider_failure_keeps_deterministic_discovery_action_and_amount(
    monkeypatch,
) -> None:
    candidate = {
        "fund_code": "008279",
        "fund_name": "国泰中证煤炭ETF联接A",
        "sector_label": "煤炭",
        "sector_match_kind": "tracking_exact",
        "sector_identity_status": "verified",
        "sector_identity_eligible": True,
        "sector_mapping_verified": True,
        "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
        "vehicle_quality_status": "eligible",
        "vehicle_quality_score": 83.75,
        "vehicle_quality_threshold": 60.0,
        "vehicle_quality_method": "passive_index_vehicle",
        "fund_quality_score": 62.0,
        "opportunity_score_20_60d": 72.0,
        "max_drawdown_1y_percent": -24.0,
        "fund_scale_yi": 2.3,
        "nav_trend": {
            "return_20d_percent": 5.0,
            "annualized_volatility_20d_percent": 28.0,
        },
    }
    facts = {
        "candidate_pool": [deepcopy(candidate)],
        "effective_configuration": {"discovery_strategy": "opportunity_first"},
        "sector_opportunities": [
            {
                "sector_label": "煤炭",
                "score_policy_version": "sector_entry_maturity.2026-08.v3",
                "entry_state": "ready_to_start",
                "opportunity_available": True,
                "first_tranche_scale": 1.0,
                "confidence": "高",
            }
        ],
        "recommendation_candidate_scope": {
            "schema_version": RECOMMENDATION_SCOPE_VERSION,
            "policy_enforced": True,
            "ordered_eligible_fund_codes": ["008279"],
            "candidate_decisions": [
                {
                    "fund_code": "008279",
                    "sector_label": "煤炭",
                    "status": "actionable",
                    "entry_path": "confirmed_entry",
                    "reason_codes": [],
                }
            ],
        },
        "portfolio_snapshot": {"stale": False, "authoritative": True},
        "portfolio_position_truth": {
            "position_complete": True,
            "positions": [],
            "cash": {"known": False, "balance_yuan": None},
        },
        "portfolio_gap": {
            "available_budget_yuan": 10_000,
            "weight_denominator_yuan": 10_000,
            "holdings_slim": [],
            "scan_mode": "full_market",
        },
        "sector_heat": [],
        "pipeline": {"provider": "deepseek", "provider_status": "pending"},
    }
    monkeypatch.setattr(
        "app.services.discovery_allocation_service.build_discovery_risk_context",
        lambda *_args, **_kwargs: {
            "schema_version": "discovery_risk_context.v1",
            "status": "qualified",
            "qualified": True,
            "reason_codes": [],
            "max_drawdown_percent_by_code": {"008279": 24.0},
            "covariance_by_code": {"008279": {"008279": 0.04}},
            "positive_correlation_penalty_to_current_holdings_by_code": {
                "008279": 0.0
            },
        },
    )

    report = build_offline_discovery_report(
        target_sectors=["煤炭"],
        candidate_pool=[candidate],
        discovery_facts=facts,
        profile=InvestorProfile(
            avoid_chasing=False,
            prefer_dca=True,
            concentration_limit_percent=100,
            expected_investment_amount=10_000,
        ),
        focus_sectors=[],
        analysis_mode="fast",
        provider_failure=ProviderFailure(
            category="empty_content",
            message="model returned empty content",
            retryable=True,
        ),
        attempted_model="deepseek-v4-flash",
        decision_at=DECISION_AT,
    )

    assert report.provider == "offline-fallback"
    assert len(report.recommendations) == 1
    recommendation = report.recommendations[0]
    assert recommendation.action == "分批买入", (
        recommendation.points,
        recommendation.validation_notes,
        recommendation.amount_note,
        report.allocation_plan,
        report.caveats,
    )
    assert (recommendation.suggested_amount_yuan or 0) > 0
    assert "后续加减仓由日报" in (recommendation.amount_note or "")
    assert report.allocation_plan["allocations"]
    assert facts["data_evidence_guard"]["execution_blocked"] is False
    assert facts["pipeline"]["provider_failure_category"] == "empty_content"
    assert facts["pipeline"]["deterministic_action_count"] == 1


def test_unclassified_holdings_do_not_block_offline_buy_amount(monkeypatch) -> None:
    candidate = {
        "fund_code": "008279",
        "fund_name": "国泰中证煤炭ETF联接A",
        "sector_label": "煤炭",
        "sector_match_kind": "tracking_exact",
        "sector_identity_status": "verified",
        "sector_identity_eligible": True,
        "sector_mapping_verified": True,
        "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
        "vehicle_quality_status": "eligible",
        "vehicle_quality_score": 83.75,
        "vehicle_quality_threshold": 60.0,
        "vehicle_quality_method": "passive_index_vehicle",
        "fund_quality_score": 62.0,
        "opportunity_score_20_60d": 72.0,
        "max_drawdown_1y_percent": -24.0,
        "fund_scale_yi": 2.3,
        "nav_trend": {
            "return_20d_percent": 5.0,
            "annualized_volatility_20d_percent": 28.0,
        },
    }
    facts = {
        "candidate_pool": [deepcopy(candidate)],
        "effective_configuration": {"discovery_strategy": "opportunity_first"},
        "sector_opportunities": [
            {
                "sector_label": "煤炭",
                "score_policy_version": "sector_entry_maturity.2026-08.v3",
                "entry_state": "ready_to_start",
                "opportunity_available": True,
                "first_tranche_scale": 1.0,
                "confidence": "高",
            }
        ],
        "recommendation_candidate_scope": {
            "schema_version": RECOMMENDATION_SCOPE_VERSION,
            "policy_enforced": True,
            "ordered_eligible_fund_codes": ["008279"],
            "candidate_decisions": [
                {
                    "fund_code": "008279",
                    "sector_label": "煤炭",
                    "status": "actionable",
                    "entry_path": "confirmed_entry",
                    "reason_codes": [],
                }
            ],
        },
        "portfolio_snapshot": {"stale": False, "authoritative": True},
        "portfolio_position_truth": {
            "position_complete": True,
            "positions": [],
            "cash": {"known": False, "balance_yuan": None},
        },
        "portfolio_gap": {
            "available_budget_yuan": 10_000,
            "weight_denominator_yuan": 30_000,
            "sector_exposure_complete": False,
            "holdings_slim": [
                {
                    "fund_code": "012200",
                    "fund_name": "新华鑫科技3个月滚动持有灵活配置混合A",
                    "sector_name": None,
                    "holding_amount": 2_227.19,
                },
                {
                    "fund_code": "017787",
                    "fund_name": "万家宏观择时多策略混合C",
                    "sector_name": "",
                    "holding_amount": 2_267.18,
                },
            ],
            "scan_mode": "full_market",
        },
        "sector_heat": [],
        "pipeline": {"provider": "deepseek", "provider_status": "pending"},
    }
    monkeypatch.setattr(
        "app.services.discovery_allocation_service.build_discovery_risk_context",
        lambda *_args, **_kwargs: {
            "schema_version": "discovery_risk_context.v1",
            "status": "qualified",
            "qualified": True,
            "reason_codes": [],
            "max_drawdown_percent_by_code": {"008279": 24.0},
            "covariance_by_code": {"008279": {"008279": 0.04}},
            "positive_correlation_penalty_to_current_holdings_by_code": {
                "008279": 0.0
            },
        },
    )

    report = build_offline_discovery_report(
        target_sectors=["煤炭"],
        candidate_pool=[candidate],
        discovery_facts=facts,
        profile=InvestorProfile(
            avoid_chasing=False,
            prefer_dca=True,
            concentration_limit_percent=35,
            expected_investment_amount=30_000,
        ),
        focus_sectors=[],
        analysis_mode="fast",
        provider_failure=ProviderFailure(
            category="timeout",
            message="read timeout",
            retryable=True,
        ),
        attempted_model="deepseek-v4-pro",
        decision_at=DECISION_AT,
    )

    recommendation = report.recommendations[0]
    assert recommendation.action == "分批买入", (
        recommendation.points,
        recommendation.validation_notes,
        recommendation.amount_note,
        report.allocation_plan,
        report.caveats,
    )
    assert (recommendation.suggested_amount_yuan or 0) > 0
    assert report.allocation_plan["status"] != "blocked"
    assert "sector_exposure_unavailable" not in (
        (report.allocation_plan.get("unallocated_budget") or {}).get("reason_codes")
        or []
    )
    assert facts["pipeline"]["deterministic_action_count"] == 1
