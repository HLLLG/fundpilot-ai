from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_candidate_pool import (
    _with_data_quality_gate,
    enrich_candidates,
    finalize_candidate_pool,
)
from app.services.discovery_guard import apply_discovery_guards


def _snapshot(*, drawdown: float = -20.0):
    return SimpleNamespace(
        return_1y_percent=25.0,
        max_drawdown_1y_percent=drawdown,
        fund_scale_yi=None,
        management_fee=None,
        fund_type=None,
        latest_nav=1.2,
        nav_date="2026-07-10",
    )


def test_enrichment_recomputes_bounded_score_and_quality_gate(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (_snapshot(), None),
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "020356": {
                "fund_code": "020356",
                "fund_scale_yi": 3.55,
                "fund_category": "股票型",
                "fund_manager": "测试经理",
                "established_date": "2024-01-23",
                "profile_updated_at": "2026-07-10",
            }
        },
    )

    result = enrich_candidates(
        [
            {
                "fund_code": "020356",
                "fund_name": "半导体ETF联接A",
                "sector_label": "半导体",
                "return_3m_percent": 18.0,
                "return_6m_percent": 35.0,
                "return_1y_percent": 70.0,
                "max_drawdown_1y_percent": -158.0,
                "fund_quality_score": 134.0,
            }
        ]
    )

    item = result[0]
    assert item["max_drawdown_1y_percent"] == -20.0
    assert 0 <= item["fund_quality_score"] <= 100
    assert item["quality_score_version"] == "fund_quality.v3"
    assert item["quality_gate"]["status"] == "eligible"
    assert item["quality_gate"]["coverage_percent"] == 100.0


def test_enrichment_converts_xq_shares_with_latest_nav_instead_of_treating_as_aum(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (_snapshot(), None),
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "020356": {
                "fund_code": "020356",
                "fund_shares_yi": 2.0,
                "fund_shares_basis": "xq_latest_reported_shares",
                "fund_manager": "测试经理",
                "established_date": "2024-01-23",
                "profile_status": "complete",
            }
        },
    )

    item = enrich_candidates(
        [
            {
                "fund_code": "020356",
                "fund_name": "半导体ETF联接A",
                "sector_label": "半导体",
                "return_3m_percent": 18.0,
                "return_6m_percent": 35.0,
                "max_drawdown_1y_percent": -20.0,
            }
        ]
    )[0]

    assert item["fund_scale_yi"] == 2.4
    assert item["fund_scale_basis"] == "nav_times_xq_latest_shares"
    assert item["quality_gate"]["status"] == "eligible"


def test_small_or_incomplete_fund_cannot_become_actionable(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (_snapshot(), None),
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "021627": {
                "fund_code": "021627",
                "fund_scale_yi": 0.18,
                "fund_category": "混合型",
                "fund_manager": "测试经理",
                "established_date": "2024-11-22",
                "profile_updated_at": "2026-07-10",
            }
        },
    )
    item = enrich_candidates(
        [
            {
                "fund_code": "021627",
                "fund_name": "半导体产业混合C",
                "sector_label": "半导体",
                "return_3m_percent": 50.0,
                "return_6m_percent": 90.0,
                "return_1y_percent": 120.0,
            }
        ]
    )[0]
    assert item["quality_gate"]["status"] == "excluded"
    assert any("0.5亿元" in reason for reason in item["quality_gate"]["reasons"])


def test_borderline_scale_fund_is_watch_only():
    item = _with_data_quality_gate(
        {
            "fund_scale_yi": 0.56,
            "return_3m_percent": 8.0,
            "return_6m_percent": 12.0,
            "max_drawdown_1y_percent": -18.0,
            "established_date": "2024-01-01",
            "fund_manager": "测试经理",
            "nav_date": "2026-07-10",
        }
    )
    assert item["quality_gate"]["status"] == "watch_only"
    assert item["quality_gate"]["eligible"] is False


def test_stale_profile_fallback_is_watch_only_even_when_fields_are_complete():
    item = _with_data_quality_gate(
        {
            "fund_scale_yi": 12.0,
            "return_3m_percent": 8.0,
            "return_6m_percent": 12.0,
            "max_drawdown_1y_percent": -18.0,
            "established_date": "2020-01-01",
            "fund_manager": "测试经理",
            "nav_date": "2026-07-10",
            "profile_status": "stale_fallback",
        }
    )

    assert item["quality_gate"]["status"] == "watch_only"
    assert any("缓存已过期" in reason for reason in item["quality_gate"]["reasons"])


def test_stale_profile_fields_do_not_trigger_hard_exclusion_or_full_coverage():
    item = _with_data_quality_gate(
        {
            "fund_scale_yi": 0.1,
            "return_3m_percent": 0.0,
            "return_6m_percent": 0.0,
            "max_drawdown_1y_percent": 0.0,
            "established_date": "2026-07-01",
            "fund_manager": "缓存经理",
            "nav_date": "2026-07-10",
            "profile_status": "stale_fallback",
        }
    )

    assert item["quality_gate"]["status"] == "watch_only"
    assert item["quality_gate"]["coverage_percent"] == 57.1
    assert set(item["quality_gate"]["profile_stale_fields"]) == {
        "fund_scale_yi",
        "established_date",
        "fund_manager",
    }
    assert not any("低于0.5亿元" in reason for reason in item["quality_gate"]["reasons"])


def test_zero_returns_and_drawdown_are_valid_core_values_but_non_finite_values_are_not():
    valid = _with_data_quality_gate(
        {
            "fund_scale_yi": 3.0,
            "return_3m_percent": 0.0,
            "return_6m_percent": 0.0,
            "max_drawdown_1y_percent": 0.0,
            "established_date": "2020-01-01",
            "fund_manager": "测试经理",
            "nav_date": "2026-07-10",
        }
    )
    invalid = _with_data_quality_gate(
        {
            **valid,
            "fund_scale_yi": float("nan"),
            "return_3m_percent": float("inf"),
        }
    )

    assert valid["quality_gate"]["coverage_percent"] == 100.0
    assert valid["quality_gate"]["status"] == "eligible"
    assert invalid["quality_gate"]["status"] == "watch_only"
    assert {"fund_scale_yi", "return_3m_percent"}.issubset(
        invalid["quality_gate"]["missing_fields"]
    )


@pytest.mark.parametrize("nav_date", ["2099-01-01", "not-a-date"])
def test_candidate_quality_gate_rejects_future_or_invalid_nav_dates(nav_date: str):
    item = _with_data_quality_gate(
        {
            "fund_scale_yi": 3.0,
            "return_3m_percent": 1.0,
            "return_6m_percent": 2.0,
            "max_drawdown_1y_percent": -10.0,
            "established_date": "2020-01-01",
            "fund_manager": "测试经理",
            "nav_date": nav_date,
        },
        as_of_date=date(2026, 7, 14),
    )

    assert item["quality_gate"]["status"] == "excluded"
    assert item["quality_gate"]["eligible"] is False
    assert any("时点" in reason for reason in item["quality_gate"]["reasons"])


def test_enrichment_propagates_partial_profile_stale_fields(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (_snapshot(), None),
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "020356": {
                "fund_code": "020356",
                "fund_scale_yi": 3.55,
                "fund_category": "股票型",
                "fund_manager": "缓存经理",
                "established_date": "2024-01-23",
                "profile_updated_at": "2026-07-10",
                "profile_status": "partial",
                "profile_stale_fields": ["fund_manager"],
            }
        },
    )

    item = enrich_candidates(
        [
            {
                "fund_code": "020356",
                "fund_name": "半导体ETF联接A",
                "sector_label": "半导体",
                "return_3m_percent": 18.0,
                "return_6m_percent": 35.0,
                "max_drawdown_1y_percent": -20.0,
            }
        ]
    )[0]

    assert item["profile_stale_fields"] == ["fund_manager"]
    assert item["quality_gate"]["profile_stale_fields"] == ["fund_manager"]
    assert item["quality_gate"]["status"] == "watch_only"
    assert any("仍含过期字段" in reason for reason in item["quality_gate"]["reasons"])


def test_final_candidate_pool_drops_excluded_and_backfills_by_sector():
    pool = [
        {
            "fund_code": "000001",
            "sector_label": "半导体",
            "fund_quality_score": 99,
            "quality_gate": {"status": "excluded"},
        },
        {
            "fund_code": "000002",
            "sector_label": "半导体",
            "fund_quality_score": 82,
            "quality_gate": {"status": "eligible"},
        },
        {
            "fund_code": "000003",
            "sector_label": "半导体",
            "fund_quality_score": 70,
            "quality_gate": {"status": "watch_only"},
        },
        {
            "fund_code": "000004",
            "sector_label": "医药",
            "fund_quality_score": 75,
            "quality_gate": {"status": "eligible"},
        },
    ]

    result = finalize_candidate_pool(pool, ["半导体", "医药"], per_sector=1, pool_cap=3)

    assert [item["fund_code"] for item in result] == ["000002", "000004", "000003"]
    assert [item["candidate_final_rank"] for item in result] == [1, 2, 3]


def test_final_candidate_pool_selects_open_share_after_family_evidence() -> None:
    base_tradeability = {
        "data_status": "complete",
        "freshness": "fresh",
        "redemption_state": "open",
        "currency": "CNY",
        "minimum_purchase_yuan": 10.0,
        "daily_purchase_limit_yuan": None,
        "daily_purchase_limit_unlimited": True,
    }
    pool = [
        {
            "fund_code": "020639",
            "fund_name": "广发半导体设备ETF联接A",
            "fund_type": "指数型",
            "sector_label": "半导体",
            "fund_quality_score": 90,
            "quality_gate": {"status": "eligible"},
            "tradeability": {**base_tradeability, "purchase_state": "suspended"},
        },
        {
            "fund_code": "020640",
            "fund_name": "广发半导体设备ETF联接C",
            "fund_type": "指数型",
            "sector_label": "半导体",
            "fund_quality_score": 88,
            "quality_gate": {"status": "eligible"},
            "tradeability": {**base_tradeability, "purchase_state": "open"},
        },
    ]

    result = finalize_candidate_pool(pool, ["半导体"], per_sector=1, pool_cap=1)

    assert [item["fund_code"] for item in result] == ["020640"]
    assert result[0]["share_family"]["member_codes"] == ["020640", "020639"]
    assert result[0]["share_family"]["selected_basis"] == (
        "tradeability_gate_then_legacy_share_class_priority"
    )
    assert result[0]["share_family"]["fee_comparison_status"] == "not_compared"


def test_final_candidate_pool_compares_share_cost_at_profile_horizon() -> None:
    def tradeability(*, purchase_fee: float, sales_service_fee: float) -> dict:
        return {
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
                    "fee_percent": purchase_fee,
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
            "sales_service_fee_annual_percent": sales_service_fee,
        }

    pool = [
        {
            "fund_code": "020639",
            "fund_name": "广发半导体设备ETF联接A",
            "fund_type": "指数型",
            "sector_label": "半导体",
            "fund_quality_score": 90,
            "quality_gate": {"status": "eligible"},
            "tradeability": tradeability(purchase_fee=1.2, sales_service_fee=0.0),
        },
        {
            "fund_code": "020640",
            "fund_name": "广发半导体设备ETF联接C",
            "fund_type": "指数型",
            "sector_label": "半导体",
            "fund_quality_score": 88,
            "quality_gate": {"status": "eligible"},
            "tradeability": tradeability(purchase_fee=0.0, sales_service_fee=0.3),
        },
    ]

    result = finalize_candidate_pool(
        pool,
        ["半导体"],
        per_sector=1,
        pool_cap=1,
        minimum_holding_days=180,
    )

    assert [item["fund_code"] for item in result] == ["020640"]
    family = result[0]["share_family"]
    assert family["selected_basis"] == "standard_cost_upper_bound_at_profile_horizon"
    assert family["fee_comparison_status"] == (
        "compared_standard_upper_bound_at_profile_horizon"
    )
    assert family["comparison_amount_yuan"] == 100.0
    assert family["member_cost_upper_bound_percent"]["020640"] < family[
        "member_cost_upper_bound_percent"
    ]["020639"]


def test_guard_removes_excluded_candidate_and_clears_non_buy_amounts():
    excluded = {
        "fund_code": "021627",
        "fund_name": "小规模基金C",
        "sector_label": "半导体",
        "quality_gate": {
            "status": "excluded",
            "eligible": False,
            "reasons": ["最新估算规模低于0.5亿元"],
        },
    }
    observed = {
        "fund_code": "020356",
        "fund_name": "观察基金A",
        "sector_label": "半导体",
        "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
    }
    recommendations = [
        DiscoveryRecommendation(
            fund_code="021627",
            fund_name="小规模基金C",
            sector_name="半导体",
            action="分批买入",
            suggested_amount_yuan=3000,
        ),
        DiscoveryRecommendation(
            fund_code="020356",
            fund_name="观察基金A",
            sector_name="半导体",
            action="建议关注",
            suggested_amount_yuan=3000,
        ),
    ]

    guarded, _caveats, eliminated = apply_discovery_guards(
        recommendations,
        candidate_pool=[excluded, observed],
        held_codes=set(),
        profile=InvestorProfile(concentration_limit_percent=100),
        budget_yuan=10_000,
        sector_heat=[],
        discovery_facts={"candidate_pool": [excluded, observed]},
    )

    assert [item.fund_code for item in guarded] == ["020356"]
    assert guarded[0].suggested_amount_yuan is None
    assert "未生成可执行" in (guarded[0].amount_note or "")
    assert [item.fund_code for item in eliminated] == ["021627"]


def test_guard_deterministically_downgrades_high_score_watch_only_candidate():
    candidate = {
        "fund_code": "020356",
        "fund_name": "高分但资料待补基金A",
        "sector_label": "半导体",
        "fund_quality_score": 90.0,
        "sector_fit_score": 38.0,
        "quality_gate": {
            "status": "watch_only",
            "eligible": False,
            "reasons": ["核心字段缺失：基金经理"],
        },
    }
    guarded, caveats, _ = apply_discovery_guards(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="高分但资料待补基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=5000,
                confidence="高",
            )
        ],
        candidate_pool=[candidate],
        held_codes=set(),
        profile=InvestorProfile(concentration_limit_percent=100),
        budget_yuan=10_000,
        sector_heat=[],
        discovery_facts={"candidate_pool": [candidate]},
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
    assert guarded[0].confidence != "高"
    assert "质量门禁" in guarded[0].points[0]
    assert any("研究观察" in caveat for caveat in caveats)


def test_guard_applies_profile_drawdown_suitability_before_buy():
    pool_item = {
        "fund_code": "020356",
        "fund_name": "高回撤基金A",
        "sector_label": "半导体",
        "max_drawdown_1y_percent": -25.0,
        "fund_quality_score": 70.0,
        "sector_fit_score": 30.0,
        "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
    }
    guarded, _caveats, _eliminated = apply_discovery_guards(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="高回撤基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=3000,
            )
        ],
        candidate_pool=[pool_item],
        held_codes=set(),
        profile=InvestorProfile(
            decision_style="conservative",
            max_drawdown_percent=8,
            concentration_limit_percent=100,
        ),
        budget_yuan=10_000,
        sector_heat=[],
        discovery_facts={"candidate_pool": [pool_item]},
    )
    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
    assert "当前风格" in guarded[0].points[0]


def test_guard_blocks_buy_when_candidate_is_outside_quant_coverage() -> None:
    covered = {
        "fund_code": "020356",
        "fund_name": "已量化基金",
        "sector_label": "半导体",
        "fund_quality_score": 82.0,
        "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
    }
    uncovered = {
        "fund_code": "021627",
        "fund_name": "未量化基金",
        "sector_label": "半导体",
        "fund_quality_score": 70.0,
        "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
    }
    facts = {
        "candidate_pool": [covered, uncovered],
        "candidate_factor_scores": {
            "available": True,
            "ic_status": {"state": "available", "available": True, "stale": False},
            "applicable_fund_codes": ["020356"],
        },
    }

    guarded, caveats, _ = apply_discovery_guards(
        [
            DiscoveryRecommendation(
                fund_code="021627",
                fund_name="未量化基金",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=3000,
            )
        ],
        candidate_pool=[covered, uncovered],
        held_codes=set(),
        profile=InvestorProfile(concentration_limit_percent=100),
        budget_yuan=10_000,
        sector_heat=[],
        discovery_facts=facts,
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
    assert guarded[0].confidence == "低"
    assert "量化证据未覆盖" in guarded[0].points[0]
    assert facts["data_evidence_guard"]["quant_evidence_blocked_fund_codes"] == [
        "021627"
    ]
    assert any("量化覆盖集合" in caveat for caveat in caveats)


def test_guard_blocks_buy_when_factor_ic_is_stale_even_if_row_is_applicable() -> None:
    candidate = {
        "fund_code": "020356",
        "fund_name": "过期因子候选",
        "sector_label": "半导体",
        "quality_gate": {"status": "eligible", "eligible": True, "reasons": []},
    }
    facts = {
        "candidate_pool": [candidate],
        "candidate_factor_scores": {
            "available": True,
            "ic_status": {"state": "stale", "available": True, "stale": True},
            "applicable_fund_codes": ["020356"],
            "holdings": [{"fund_code": "020356", "applicable": True}],
        },
    }

    guarded, _, _ = apply_discovery_guards(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="过期因子候选",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=2000,
            )
        ],
        candidate_pool=[candidate],
        held_codes=set(),
        profile=InvestorProfile(concentration_limit_percent=100),
        budget_yuan=10_000,
        sector_heat=[],
        discovery_facts=facts,
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
    assert facts["data_evidence_guard"]["quant_evidence_blocked_fund_codes"] == [
        "020356"
    ]


def _eligible_guard_candidate(*, quality_gate: dict | None = None) -> dict:
    candidate = {
        "fund_code": "020356",
        "fund_name": "守卫测试基金A",
        "sector_label": "半导体",
        "fund_quality_score": 90.0,
        "sector_fit_score": 38.0,
        "tradeability": {
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
        },
    }
    if quality_gate is not None:
        candidate["quality_gate"] = quality_gate
    return candidate


def _run_guard_for_test(
    recommendations: list[DiscoveryRecommendation],
    candidate: dict,
    *,
    budget_yuan: float = 10_000,
    extra_facts: dict | None = None,
):
    profile = InvestorProfile(concentration_limit_percent=100)
    facts = {
        "candidate_pool": [candidate],
        "portfolio_snapshot": {
            "stale": False,
            "authoritative": True,
            "position_complete": True,
            "pending_transaction_count": 0,
        },
        "portfolio_position_truth": {
            "position_complete": True,
            "cash": {"known": True, "balance_yuan": budget_yuan},
            "positions": [],
        },
        "portfolio_gap": {
            "available_budget_yuan": budget_yuan,
            "total_amount": 0,
            "weight_denominator_yuan": 0,
            "holdings_slim": [],
        },
    }
    facts.update(extra_facts or {})
    return apply_discovery_guards(
        recommendations,
        candidate_pool=[candidate],
        held_codes=set(),
        profile=profile,
        budget_yuan=budget_yuan,
        sector_heat=[],
        discovery_facts=facts,
    )


@pytest.mark.parametrize("quality_gate", [None, {}, {"status": "future_state"}])
def test_guard_fails_closed_when_quality_gate_is_missing_or_unknown(quality_gate):
    candidate = _eligible_guard_candidate(quality_gate=quality_gate)
    guarded, _, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=1000,
                confidence="高",
            )
        ],
        candidate,
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
    assert any("门禁缺失" in point for point in guarded[0].points)


def test_guard_deduplicates_same_fund_before_allocating_budget():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    guarded, caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=1000,
            ),
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=1000,
            ),
        ],
        candidate,
    )

    assert len(guarded) == 1
    assert guarded[0].suggested_amount_yuan == 1000
    assert any("重复推荐" in caveat for caveat in caveats)


def test_guard_never_uses_legacy_descriptive_factor_alias_for_execution():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    guarded, _, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=1000,
            )
        ],
        candidate,
        extra_facts={
            "candidate_factor_scores": {
                "available": True,
                "ic_status": {
                    "state": "available",
                    "available": True,
                    "stale": False,
                },
                "applicable_fund_codes": ["020356"],
                "holdings": [
                    {
                        "fund_code": "020356",
                        "applicable": True,
                        "descriptive_applicable": True,
                        "execution_qualified": False,
                    }
                ],
            }
        },
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
    assert "量化证据未覆盖" in guarded[0].points[0]


@pytest.mark.parametrize("invalid_amount", [-1000.0, float("nan"), float("inf")])
def test_guard_rejects_non_positive_or_non_finite_amounts(invalid_amount):
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    guarded, _, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=invalid_amount,
            )
        ],
        candidate,
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None


@pytest.mark.parametrize(
    "negative_action",
    ["不建议买入", "暂不买入", "不加仓", "停止加仓"],
)
def test_guard_does_not_turn_negated_actions_into_buy_orders(negative_action):
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    guarded, _, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action=negative_action,
                suggested_amount_yuan=1000,
            )
        ],
        candidate,
    )

    assert guarded[0].action != "分批买入"
    assert guarded[0].suggested_amount_yuan is None


def test_guard_downgrades_buy_when_budget_is_zero():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    guarded, _, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=1000,
            )
        ],
        candidate,
        budget_yuan=0,
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
