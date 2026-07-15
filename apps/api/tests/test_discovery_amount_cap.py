from __future__ import annotations

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_guard import (
    apply_discovery_guards,
    resolve_discovery_amount_cap,
)


def _known_cash(amount: float = 50_000) -> dict:
    return {
        "position_complete": True,
        "cash": {"known": True, "balance_yuan": amount},
        "positions": [],
    }


def _verified_tradeability() -> dict:
    return {
        "data_status": "partial",
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
        "source_conflict": False,
        "source_ids": ["pytest.tradeability"],
    }


def test_amount_cap_keeps_boost_below_request_concentration_limit() -> None:
    result = resolve_discovery_amount_cap(
        portfolio_truth=_known_cash(),
        holdings_slim=[],
        candidate_sector="半导体",
        allocated_by_sector={},
        allocated_total_yuan=0,
        request_budget_yuan=50_000,
        concentration_limit_percent=30,
        weight_denominator_yuan=100_000,
    )

    assert result.available is True
    assert result.cap_yuan == 15_000


def test_amount_cap_subtracts_existing_and_current_sector_exposure() -> None:
    result = resolve_discovery_amount_cap(
        portfolio_truth=_known_cash(),
        holdings_slim=[
            {
                "fund_code": "000001",
                "sector_name": "半导体",
                "holding_amount": 25_000,
            }
        ],
        candidate_sector="半导体",
        allocated_by_sector={"半导体": 4_000},
        allocated_total_yuan=4_000,
        request_budget_yuan=50_000,
        concentration_limit_percent=30,
        weight_denominator_yuan=100_000,
    )

    assert result.available is True
    assert result.existing_sector_amount_yuan == 25_000
    assert result.cap_yuan == 1_000


def test_amount_cap_uses_canonical_sector_aliases_for_existing_exposure() -> None:
    result = resolve_discovery_amount_cap(
        portfolio_truth=_known_cash(),
        holdings_slim=[
            {
                "fund_code": "000001",
                "sector_name": "中证半导体指数",
                "holding_amount": 28_000,
            }
        ],
        candidate_sector="半导体",
        allocated_by_sector={},
        allocated_total_yuan=0,
        request_budget_yuan=50_000,
        concentration_limit_percent=30,
        weight_denominator_yuan=100_000,
    )

    assert result.available is True
    assert result.existing_sector_amount_yuan == 28_000
    assert result.cap_yuan == 2_000


def test_amount_cap_uses_confirmed_cash_as_independent_ceiling() -> None:
    result = resolve_discovery_amount_cap(
        portfolio_truth=_known_cash(8_000),
        holdings_slim=[],
        candidate_sector="电子",
        allocated_by_sector={},
        allocated_total_yuan=0,
        request_budget_yuan=50_000,
        concentration_limit_percent=30,
        weight_denominator_yuan=100_000,
    )

    assert result.available is True
    assert result.cap_yuan == 8_000


def test_amount_cap_fails_closed_when_cash_is_unknown() -> None:
    result = resolve_discovery_amount_cap(
        portfolio_truth={
            "position_complete": True,
            "cash": {"known": False, "balance_yuan": None},
            "positions": [],
        },
        holdings_slim=[],
        candidate_sector="电子",
        allocated_by_sector={},
        allocated_total_yuan=0,
        request_budget_yuan=50_000,
        concentration_limit_percent=30,
        weight_denominator_yuan=100_000,
    )

    assert result.available is False
    assert result.cap_yuan is None
    assert "cash_unknown" in result.reasons


def test_amount_cap_fails_closed_when_existing_sector_is_unknown() -> None:
    result = resolve_discovery_amount_cap(
        portfolio_truth=_known_cash(),
        holdings_slim=[
            {
                "fund_code": "000001",
                "sector_name": None,
                "holding_amount": 20_000,
            }
        ],
        candidate_sector="电子",
        allocated_by_sector={},
        allocated_total_yuan=0,
        request_budget_yuan=50_000,
        concentration_limit_percent=30,
        weight_denominator_yuan=100_000,
    )

    assert result.available is False
    assert result.cap_yuan is None
    assert "sector_exposure_unknown" in result.reasons


def test_amount_cap_rejects_non_finite_inputs() -> None:
    result = resolve_discovery_amount_cap(
        portfolio_truth=_known_cash(),
        holdings_slim=[],
        candidate_sector="电子",
        allocated_by_sector={},
        allocated_total_yuan=float("nan"),
        request_budget_yuan=50_000,
        concentration_limit_percent=30,
        weight_denominator_yuan=100_000,
    )

    assert result.available is False
    assert "invalid_amount_input" in result.reasons


def test_guard_tracks_multiple_recommendations_in_the_same_sector() -> None:
    recommendations = [
        DiscoveryRecommendation(
            fund_code=code,
            fund_name=f"测试基金{index}",
            sector_name="半导体",
            action="分批买入",
            suggested_amount_yuan=10_000,
            points=["候选质量通过"],
            risks=["波动风险"],
        )
        for index, code in enumerate(("000001", "000002"), start=1)
    ]
    candidate_pool = [
        {
            "fund_code": rec.fund_code,
            "fund_name": rec.fund_name,
            "sector_label": "半导体",
            "fund_quality_score": 80,
            "sector_fit_score": 36,
            "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
            "tradeability": _verified_tradeability(),
        }
        for rec in recommendations
    ]
    facts = {
        "portfolio_snapshot": {
            "stale": False,
            "authoritative": True,
            "position_complete": True,
            "pending_transaction_count": 0,
        },
        "portfolio_position_truth": _known_cash(),
        "portfolio_gap": {
            "weight_denominator_yuan": 100_000,
            "holdings_slim": [],
        },
        "sector_opportunities": [
            {
                "sector_label": "半导体",
                "score": 85,
                "confidence": "高",
                "opportunity_available": True,
                "pattern_label": "price_flow_aligned_up",
                "cumulative_5d_net_yi": 5,
            }
        ],
    }

    guarded, _, _ = apply_discovery_guards(
        recommendations,
        candidate_pool=candidate_pool,
        held_codes=set(),
        profile=InvestorProfile(
            avoid_chasing=False,
            concentration_limit_percent=30,
            expected_investment_amount=100_000,
        ),
        budget_yuan=50_000,
        sector_heat=[],
        discovery_facts=facts,
    )

    assert [item.suggested_amount_yuan for item in guarded] == [10_000, 5_000]
    assert sum(float(item.suggested_amount_yuan or 0) for item in guarded) == 15_000


def _apply_single_guard(*, facts: dict, amount: float = 10_000, **overrides):
    payload = {
        "fund_code": "000001",
        "fund_name": "测试基金",
        "sector_name": "半导体",
        "action": "分批买入",
        "suggested_amount_yuan": amount,
        "points": ["候选质量通过"],
        "risks": ["波动风险"],
    }
    payload.update(overrides)
    recommendation = DiscoveryRecommendation(**payload)
    guarded, _, _ = apply_discovery_guards(
        [recommendation],
        candidate_pool=[
            {
                "fund_code": "000001",
                "fund_name": "测试基金",
                "sector_label": "半导体",
                "fund_quality_score": 80,
                "sector_fit_score": 36,
                "quality_gate": {
                    "status": "eligible",
                    "eligible": True,
                    "reasons": [],
                },
                "tradeability": _verified_tradeability(),
            }
        ],
        held_codes=set(),
        profile=InvestorProfile(
            avoid_chasing=False,
            concentration_limit_percent=30,
            expected_investment_amount=None,
        ),
        budget_yuan=50_000,
        sector_heat=[],
        discovery_facts=facts,
    )
    return guarded[0]


def test_guard_reprojects_free_text_and_amount_note_from_capped_amount() -> None:
    rec = _apply_single_guard(
        facts={
            "portfolio_snapshot": {
                "stale": False,
                "authoritative": True,
                "position_complete": True,
                "pending_transaction_count": 0,
            },
            "portfolio_position_truth": _known_cash(),
            "portfolio_gap": {
                "weight_denominator_yuan": 100_000,
                "holdings_slim": [],
            },
        },
        amount=100_000,
        amount_note="建议立即一次性买入 100000 元",
        points=["建议立即一次性买入 100000 元", "候选质量通过"],
        decision_path="动作：立即买入 100000 元",
        suggested_position_change_percent=100,
        suggested_position_change_basis="模型自由给值",
    )

    assert rec.action == "分批买入"
    assert rec.suggested_amount_yuan == 15_000
    assert "15,000" in (rec.amount_note or "")
    visible = " ".join([*rec.points, rec.decision_path, rec.amount_note or ""])
    assert "一次性买入 100000" not in visible
    assert "立即买入 100000" not in visible
    assert rec.suggested_position_change_percent != 100


def test_guard_empty_portfolio_does_not_apply_concentration_twice_to_cash() -> None:
    rec = _apply_single_guard(
        facts={
            "portfolio_snapshot": {
                "stale": False,
                "authoritative": True,
                "position_complete": True,
                "pending_transaction_count": 0,
            },
            "portfolio_position_truth": _known_cash(8_000),
            "portfolio_gap": {
                "total_amount": 0,
                "weight_denominator_yuan": 0,
                "holdings_slim": [],
            },
            "sector_opportunities": [],
        }
    )

    assert rec.action == "分批买入"
    assert rec.suggested_amount_yuan == 8_000


def test_guard_without_position_truth_fails_closed_instead_of_forging_cash() -> None:
    rec = _apply_single_guard(
        facts={
            "portfolio_gap": {
                "total_amount": 0,
                "weight_denominator_yuan": 0,
                "holdings_slim": [],
            },
            "sector_opportunities": [],
        }
    )

    assert rec.action == "建议关注"
    assert rec.suggested_amount_yuan is None
    assert any("未知值未按 0" in note for note in rec.validation_notes)
