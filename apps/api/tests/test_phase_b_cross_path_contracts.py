from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.models import InvestorProfile
from app.services.benchmark_mapping_service import load_decision_benchmark_specs
from app.services.discovery_candidate_pool import (
    attach_candidate_benchmark_research,
    finalize_candidate_pool,
)
from app.services.discovery_client import build_discovery_report_from_parsed


DECISION_AT = datetime(2026, 7, 14, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _profile() -> InvestorProfile:
    return InvestorProfile(
        decision_style="conservative",
        prefer_dca=True,
        avoid_chasing=True,
        concentration_limit_percent=100,
        expected_investment_amount=100_000,
    )


def _tradeability() -> dict:
    checked_at = "2026-07-14T15:30:00+08:00"
    return {
        "schema_version": "fund_tradeability.v1",
        "data_status": "complete",
        "freshness": "fresh",
        "purchase_state": "open",
        "purchase_status": "开放申购",
        "redemption_state": "open",
        "redemption_status": "开放赎回",
        "currency": "CNY",
        "minimum_purchase_yuan": 10.0,
        "minimum_initial_purchase_yuan": 10.0,
        "minimum_additional_purchase_yuan": 10.0,
        "daily_purchase_limit_yuan": None,
        "daily_purchase_limit_unlimited": True,
        "daily_purchase_limit_scope": "all_channels_unlimited",
        "revalidation_required": True,
        "standard_purchase_fee_tiers": [
            {
                "condition": "小于100万元",
                "min_amount_yuan": None,
                "max_amount_yuan": 1_000_000.0,
                "min_inclusive": True,
                "max_inclusive": False,
                "fee_type": "percent",
                "fee_percent": 0.15,
                "flat_fee_yuan": None,
                "source_rate": "standard_undiscounted",
            }
        ],
        "redemption_fee_tiers": [
            {
                "condition": "持有大于等于7天",
                "min_days": 7,
                "max_days": None,
                "fee_percent": 0.0,
            }
        ],
        "sales_service_fee_annual_percent": 0.0,
        "share_class_fee_status": "standard_upper_bound_available",
        "source_conflict": False,
        "missing_fields": [],
        "source_ids": ["pytest.phase_b.tradeability"],
        "source_urls": [],
        "checked_at": checked_at,
        "fee_checked_at": checked_at,
        "fee_freshness": "fresh",
        "effective_at": checked_at,
    }


def _candidate_pool(*, descriptive_peer: bool = False) -> list[dict]:
    rows = [
        {
            "fund_code": "000001",
            "fund_name": "科技成长混合A",
            "sector_label": "科技",
            "fund_quality_score": 92.0,
            "sector_fit_score": 38.0,
            "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
            "tradeability": _tradeability(),
        },
        {
            "fund_code": "000002",
            "fund_name": "医药成长混合A",
            "sector_label": "医药",
            "fund_quality_score": 91.0,
            "sector_fit_score": 37.0,
            "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
            "tradeability": _tradeability(),
        },
    ]
    if descriptive_peer:
        rows[1]["peer_rank"] = {
            "schema_version": "peer_rank.v1",
            "status": "descriptive_only",
            "qualified": True,
            "execution_tilt_eligible": False,
            "execution_tilt_gate": {
                "status": "blocked",
                "eligible": False,
                "reason": "peer_rank_predictive_qualification_unavailable",
            },
            # Deliberately hostile: neither descriptive nor forged execution
            # percentiles may tilt money without the execution gate.
            "descriptive_performance_percentile": 100.0,
            "execution_score_percentile": 100.0,
        }
    return rows


def _facts() -> dict:
    return {
        "portfolio_snapshot": {
            "stale": False,
            "authoritative": True,
            "position_complete": True,
            "pending_transaction_count": 0,
        },
        "portfolio_position_truth": {
            "position_complete": True,
            "cash": {"known": True, "balance_yuan": 50_000},
            "positions": [],
        },
        "portfolio_gap": {
            "available_budget_yuan": 50_000,
            "total_amount": 0,
            "weight_denominator_yuan": 100_000,
            "holdings_slim": [],
        },
    }


def _parsed(*, amount: float, reverse: bool = False) -> dict:
    recommendations = [
        {
            "fund_code": "000001",
            "fund_name": "科技成长混合A",
            "sector_name": "科技",
            "action": "分批买入",
            "suggested_amount_yuan": amount,
            "amount_note": f"模型声称应立即投入 {amount}",
            "hold_horizon": "半年到一年",
            "confidence": "高",
            "points": ["模型自由文本不得决定金额"],
            "risks": ["净值波动"],
        },
        {
            "fund_code": "000002",
            "fund_name": "医药成长混合A",
            "sector_name": "医药",
            "action": "分批买入",
            "suggested_amount_yuan": amount,
            "amount_note": f"模型声称应立即投入 {amount}",
            "hold_horizon": "半年到一年",
            "confidence": "高",
            "points": ["模型自由文本不得决定金额"],
            "risks": ["净值波动"],
        },
    ]
    if reverse:
        recommendations.reverse()
    return {
        "title": "Phase B 确定性分配测试",
        "summary": "仅验证服务端决策边界。",
        "recommendations": recommendations,
        "caveats": [],
    }


def _report(*, amount: float, reverse: bool = False, pool: list[dict] | None = None):
    candidate_pool = deepcopy(pool if pool is not None else _candidate_pool())
    if reverse:
        candidate_pool.reverse()
    return build_discovery_report_from_parsed(
        _parsed(amount=amount, reverse=reverse),
        target_sectors=["科技", "医药"],
        focus_sectors=[],
        scan_mode="full_market",
        candidate_pool=candidate_pool,
        discovery_facts=_facts(),
        profile=_profile(),
        held_codes=set(),
        budget_yuan=50_000,
        sector_heat=[],
        analysis_mode="fast",
        decision_at=DECISION_AT,
    )


def _decision_projection(report) -> dict[str, tuple[str, float | None]]:
    return {
        row.fund_code: (row.action, row.suggested_amount_yuan)
        for row in report.recommendations
    }


def test_central_report_ignores_hostile_llm_amount_and_candidate_order() -> None:
    tiny = _report(amount=1, reverse=False)
    hostile = _report(amount=999_999, reverse=True)

    assert _decision_projection(tiny) == _decision_projection(hostile)
    assert tiny.allocation_plan == hostile.allocation_plan
    assert tiny.allocation_plan["policy"] == {
        **tiny.allocation_plan["policy"],
        "candidate_order_ignored": True,
        "llm_amount_and_prose_ignored": True,
    }
    assert sum(
        amount or 0 for _action, amount in _decision_projection(tiny).values()
    ) == 12_500


@pytest.mark.parametrize(
    "risk_context",
    [
        {},
        {
            "schema_version": "discovery_risk_context.v1",
            "status": "unqualified",
            "qualified": False,
            "reason_codes": ["candidate_nav_sample_insufficient"],
            "max_drawdown_percent_by_code": {},
            "covariance_by_code": {},
            "positive_correlation_penalty_to_current_holdings_by_code": {},
        },
    ],
    ids=["missing", "unqualified"],
)
def test_central_report_fails_closed_without_qualified_risk(
    monkeypatch, risk_context: dict
) -> None:
    monkeypatch.setattr(
        "app.services.discovery_allocation_service.build_discovery_risk_context",
        lambda *_args, **_kwargs: deepcopy(risk_context),
    )

    report = _report(amount=50_000)

    assert report.allocation_plan["status"] == "blocked"
    assert report.allocation_plan["allocations"] == []
    assert all(row.action == "建议关注" for row in report.recommendations)
    assert all(row.suggested_amount_yuan is None for row in report.recommendations)
    assert all(row.allocation == {} for row in report.recommendations)


def test_central_report_exposes_only_executable_current_tranche() -> None:
    report = _report(amount=999_999)

    assert report.allocation_plan["amount_semantics"] == "current_verified_initial_tranche"
    assert report.allocation_plan["revalidation_required"] is True
    assert report.recommendations
    for recommendation in report.recommendations:
        assert recommendation.action == "分批买入"
        assert recommendation.suggested_amount_yuan is not None
        assert recommendation.suggested_amount_yuan > 0
        assert recommendation.cost_assessment["executable"] is True
        assert recommendation.allocation["suggested_amount_yuan"] == (
            recommendation.suggested_amount_yuan
        )
        assert recommendation.allocation["amount_semantics"] == (
            "current_verified_initial_tranche"
        )
        future = recommendation.allocation["future_tranches"][0]
        assert future["amount_yuan"] is None
        assert future["revalidation_required"] is True
        assert "tradeability_gate_recheck" in future["preconditions"]
        assert "risk_context_recheck" in future["preconditions"]


def test_descriptive_peer_rank_cannot_tilt_central_allocator() -> None:
    baseline = _report(amount=1, pool=_candidate_pool())
    descriptive = _report(amount=999_999, pool=_candidate_pool(descriptive_peer=True))

    assert _decision_projection(descriptive) == _decision_projection(baseline)
    assert descriptive.allocation_plan["allocation_mode"] == (
        baseline.allocation_plan["allocation_mode"]
    )
    peer_row = next(
        row
        for row in descriptive.allocation_plan["allocations"]
        if row["fund_code"] == "000002"
    )
    assert peer_row["priority"]["qualified_peer_score_percentile"] is None
    assert peer_row["priority"]["peer_tilt_status"] == (
        "ignored_not_execution_qualified"
    )


def test_candidate_selection_audit_covers_selected_unselected_and_share_family() -> None:
    source = [
        {
            "fund_code": "100001",
            "fund_name": "同源科技成长混合A",
            "fund_type": "混合型",
            "sector_label": "科技",
            "fund_quality_score": 95.0,
            "sector_fit_score": 40.0,
            "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
            "tradeability": _tradeability(),
        },
        {
            "fund_code": "100002",
            "fund_name": "同源科技成长混合C",
            "fund_type": "混合型",
            "sector_label": "科技",
            "fund_quality_score": 90.0,
            "sector_fit_score": 39.0,
            "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
            "tradeability": _tradeability(),
        },
        {
            "fund_code": "100003",
            "fund_name": "独立科技成长混合A",
            "fund_type": "混合型",
            "sector_label": "科技",
            "fund_quality_score": 80.0,
            "sector_fit_score": 30.0,
            "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
            "tradeability": _tradeability(),
        },
    ]
    audit: dict = {}

    selected = finalize_candidate_pool(
        deepcopy(source),
        ["科技"],
        per_sector=1,
        pool_cap=1,
        minimum_holding_days=180,
        audit_sink=audit,
    )

    assert [row["fund_code"] for row in selected] == ["100001"]
    assert audit["schema_version"] == "discovery_candidate_selection_audit.v1"
    assert audit["prescreen_count"] == 3
    assert audit["post_share_family_count"] == 2
    assert audit["selected_count"] == 1
    rows = {row["fund_code"]: row for row in audit["rows"]}
    assert rows["100001"]["selected"] is True
    assert rows["100001"]["final_rank"] == 1
    assert rows["100002"]["selected"] is False
    assert "share_class_not_selected_after_tradeability_and_cost" in rows["100002"][
        "reason_codes"
    ]
    assert rows["100003"]["selected"] is False
    assert "outside_final_sector_quota_or_pool_cap" in rows["100003"][
        "reason_codes"
    ]
    assert len(audit["snapshot_hash"]) == 64

    reordered_audit: dict = {}
    finalize_candidate_pool(
        list(reversed(deepcopy(source))),
        ["科技"],
        per_sector=1,
        pool_cap=1,
        minimum_holding_days=180,
        audit_sink=reordered_audit,
    )
    assert reordered_audit["rows"] == audit["rows"]
    assert reordered_audit["snapshot_hash"] == audit["snapshot_hash"]


def test_early_benchmark_loader_and_attachment_enforce_pit_role_contract(
    monkeypatch,
) -> None:
    @contextmanager
    def fake_connect():
        yield object()

    def fake_freeze(*, fund_code, decision_at, user_id, connection):
        assert decision_at == "2026-07-14T08:00:00+00:00"
        assert user_id == 1
        assert connection is not None
        available_at = (
            "2026-07-14T07:30:00+00:00"
            if fund_code == "200001"
            else "2026-07-14T08:00:01+00:00"
        )
        spec = {
            "schema_version": "fund_benchmark_mapping.v1",
            "mapping_id": f"fbm-{fund_code}",
            "tier": "fund_contract_exact",
            "benchmark_kind": "official_contract",
            "contract_verification_kind": "verified_fund_contract",
            "completeness": "complete",
            "formal_excess_eligible": True,
            "benchmark_code": "000300",
            "benchmark_name": "沪深300指数收益率×95%+活期存款利率×5%",
            "available_at": available_at,
        }
        return spec, {"fund_code": fund_code}

    monkeypatch.setattr("app.database._connect", fake_connect)
    monkeypatch.setattr(
        "app.services.benchmark_mapping_service.freeze_fund_benchmark_spec",
        fake_freeze,
    )

    specs = load_decision_benchmark_specs(
        ["200001", "200002", "200001", "invalid"],
        decision_at=DECISION_AT,
    )
    attached = attach_candidate_benchmark_research(
        [
            {"fund_code": "200001", "fund_name": "价值股票混合A", "fund_type": "股票型"},
            {"fund_code": "200002", "fund_name": "成长股票混合A", "fund_type": "股票型"},
        ],
        specs,
        decision_at=DECISION_AT,
    )

    assert list(specs) == ["200001", "200002"]
    by_code = {row["fund_code"]: row for row in attached}
    assert by_code["200001"]["benchmark_spec"] == specs["200001"]
    assert by_code["200001"]["benchmark_comparison"]["comparison_role"] == (
        "formal_excess"
    )
    assert by_code["200001"]["benchmark_comparison"][
        "formal_excess_eligible"
    ] is True
    assert by_code["200002"]["benchmark_comparison"]["comparison_role"] == (
        "unavailable"
    )
    assert by_code["200002"]["benchmark_comparison"]["reason"] == (
        "benchmark_available_after_decision_at"
    )
