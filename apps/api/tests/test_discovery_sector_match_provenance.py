from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_allocation_service import (
    prepare_recommendations_for_deterministic_allocation,
)
from app.services.discovery_candidate_llm import slim_candidate_for_llm
from app.services.discovery_candidate_pool import (
    _sector_fit_score,
    build_candidate_pool,
    enrich_candidates,
    finalize_candidate_pool,
    rank_candidates_balanced_fallback,
)
from app.services.discovery_guard import apply_discovery_guards


_DECISION_AT = datetime(2026, 7, 14, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"sector_match_kind": "primary", "sector_confidence": 0.8}, 36.8),
        ({"sector_match_kind": "name"}, 16.0),
        ({"sector_match_kind": "new_issue"}, 18.0),
        ({"sector_match_kind": "tracking_exact"}, 34.0),
        ({"sector_match_kind": "fallback"}, 16.0),
        ({"_sector_match_kind": "primary", "sector_confidence": 0.8}, 36.8),
        (
            {
                "sector_match_kind": "name",
                "_sector_match_kind": "primary",
                "sector_confidence": 0.8,
            },
            16.0,
        ),
    ],
)
def test_sector_fit_score_prefers_public_provenance_and_supports_legacy_rows(
    row: dict,
    expected: float,
) -> None:
    assert _sector_fit_score(row) == expected


def test_fallback_candidates_have_explicit_public_provenance() -> None:
    rows = rank_candidates_balanced_fallback(
        [
            {
                "fund_code": "000001",
                "fund_name": "均衡配置基金A",
                "fund_scale_yi": 20,
                "return_3m_percent": 5,
                "return_6m_percent": 8,
                "return_1y_percent": 12,
                "max_drawdown_1y_percent": -10,
                "established_date": "2020-01-01",
            }
        ],
        excluded=set(),
        seen_codes=set(),
        fund_type_preference="any",
    )

    assert rows[0]["sector_match_kind"] == "fallback"
    assert not any(key.startswith("_") for key in rows[0])


def test_primary_match_survives_build_enrich_finalize_llm_and_guard(
    monkeypatch,
) -> None:
    rank_row = {
        "fund_code": "020640",
        "fund_name": "广发半导体设备ETF联接A",
        "fund_scale_yi": 20,
        "return_3m_percent": 8,
        "return_6m_percent": 15,
        "return_1y_percent": 24,
        "max_drawdown_1y_percent": -12,
        "established_date": "2020-01-01",
        "nav_date": "2026-07-10",
    }
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda _labels, limit_per_sector=20: [
            {
                "fund_code": "020640",
                "fund_name": "广发半导体设备ETF联接A",
                "sector_name": "半导体",
                "source": "precompute_benchmark",
                "confidence": 0.8,
            }
        ],
    )

    built = build_candidate_pool(
        ["半导体"],
        per_sector=1,
        pool_cap=1,
        fetch_rank=lambda limit: [rank_row],
        fetch_new_funds=lambda limit: [],
        decision_at=_DECISION_AT,
    )

    # The same fund is discovered through both primary mapping and name matching;
    # the stronger primary provenance must win and must already be persistable.
    assert built[0]["sector_match_kind"] == "primary"
    assert built[0]["sector_fit_score"] == 36.8
    assert built[0]["quality_score_version"] == "fund_quality.v3"
    assert not any(key.startswith("_") for key in built[0])

    snapshot = SimpleNamespace(
        return_1y_percent=24.0,
        max_drawdown_1y_percent=-12.0,
        fund_scale_yi=20.0,
        management_fee=0.5,
        fund_type="股票型",
        latest_nav=1.2,
        nav_date="2026-07-10",
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (snapshot, None),
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "020640": {
                "fund_code": "020640",
                "fund_scale_yi": 20.0,
                "fund_category": "股票型",
                "fund_manager": "测试经理",
                "established_date": "2020-01-01",
                "profile_updated_at": "2026-07-10",
                "profile_status": "complete",
            }
        },
    )

    enriched = enrich_candidates(built, decision_at=_DECISION_AT)
    assert enriched[0]["sector_match_kind"] == "primary"
    assert enriched[0]["sector_fit_score"] == 36.8
    assert "板块匹配置信偏低" not in enriched[0]["quality_penalties"]
    assert enriched[0]["quality_score_version"] == "fund_quality.v3"

    finalized = finalize_candidate_pool(enriched, ["半导体"], per_sector=1, pool_cap=1)
    assert finalized[0]["sector_match_kind"] == "primary"
    assert not any(key.startswith("_") for key in finalized[0])

    with patch(
        "app.services.discovery_candidate_llm.get_cached_official_nav_return",
        return_value=None,
    ):
        slim = slim_candidate_for_llm(
            finalized[0],
            sector_change_index={},
            trade_date=None,
        )
    assert slim["sector_match_kind"] == "primary"

    guarded, _caveats, _eliminated = apply_discovery_guards(
        [
            DiscoveryRecommendation(
                fund_code="020640",
                fund_name="广发半导体设备ETF联接A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=1000,
                confidence="中",
            )
        ],
        candidate_pool=finalized,
        held_codes=set(),
        profile=InvestorProfile(
            avoid_chasing=False,
            concentration_limit_percent=100,
            expected_investment_amount=10_000,
        ),
        budget_yuan=10_000,
        sector_heat=[],
        discovery_facts={
            "candidate_pool": finalized,
            "portfolio_snapshot": {
                "stale": False,
                "authoritative": True,
                "position_complete": True,
                "pending_transaction_count": 0,
            },
            "portfolio_position_truth": {
                "position_complete": True,
                "cash": {"known": True, "balance_yuan": "10000"},
                "positions": [],
            },
            "portfolio_gap": {
                "holding_count": 0,
                "total_amount": 0,
                "available_budget_yuan": 10_000,
                "holdings_slim": [],
            },
        },
    )

    assert guarded[0].action == "分批买入"
    assert guarded[0].suggested_amount_yuan == 1000


def test_exact_passive_tracking_reference_upgrades_name_match_without_upgrading_proxy(
    monkeypatch,
) -> None:
    snapshot = SimpleNamespace(
        return_1y_percent=-18.91,
        max_drawdown_1y_percent=-33.98,
        fund_scale_yi=None,
        management_fee=0.15,
        fund_type="QDII-股票型",
        latest_nav=1.0,
        nav_date="2026-07-21",
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (snapshot, None),
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "020989": {
                "fund_code": "020989",
                "fund_scale_yi": 14.21,
                "fund_category": "QDII-股票型",
                "fund_manager": "测试经理",
                "established_date": "2024-01-01",
                "profile_status": "complete",
                "benchmark_text": (
                    "经汇率调整后的恒生科技指数收益率×95%+"
                    "银行人民币活期存款利率（税后）×5%"
                ),
                "benchmark_text_kind": "performance_benchmark",
                "benchmark_text_source_kind": "xq_akshare_aggregator",
            },
            "007882": {
                "fund_code": "007882",
                "fund_scale_yi": 12.0,
                "fund_category": "股票指数",
                "fund_manager": "测试经理",
                "established_date": "2020-01-01",
                "profile_status": "complete",
                "benchmark_text": (
                    "沪深300非银行金融指数收益率×95%+"
                    "活期存款利率(税后)×5%"
                ),
                "benchmark_text_kind": "performance_benchmark",
                "benchmark_text_source_kind": "xq_akshare_aggregator",
            },
        },
    )

    tradeability = {
        "data_status": "partial",
        "freshness": "fresh",
        "purchase_state": "open",
        "redemption_state": "open",
        "currency": "CNY",
        "minimum_purchase_yuan": 10.0,
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
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.resolve_fund_tradeability_profiles",
        lambda codes, **_kwargs: {code: dict(tradeability) for code in codes},
    )

    rows = enrich_candidates(
        [
            {
                "fund_code": "020989",
                "fund_name": "南方恒生科技ETF发起联接(QDII)C",
                "sector_label": "恒生科技",
                "sector_match_kind": "name",
                "return_3m_percent": -8.8,
                "return_6m_percent": -21.95,
                "return_1y_percent": -18.91,
                "max_drawdown_1y_percent": -33.98,
            },
            {
                "fund_code": "007882",
                "fund_name": "易方达沪深300非银ETF联接C",
                "sector_label": "保险",
                "sector_match_kind": "name",
                "return_3m_percent": -3.0,
                "return_6m_percent": -6.0,
                "return_1y_percent": -12.0,
                "max_drawdown_1y_percent": -30.0,
            },
        ],
        decision_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )
    by_code = {row["fund_code"]: row for row in rows}

    exact = by_code["020989"]
    assert exact["sector_match_kind"] == "tracking_exact"
    assert exact["sector_fit_score"] == 34.0
    assert exact["tracking_reference_match"]["index_code"] == "HSTECH"
    assert exact["tracking_reference_match"]["formal_excess_eligible"] is False
    assert exact["vehicle_quality_method"] == "passive_index_vehicle"
    assert exact["vehicle_quality_status"] == "eligible"
    assert exact["vehicle_quality_score"] >= exact["vehicle_quality_threshold"]

    proxy = by_code["007882"]
    assert proxy["sector_match_kind"] == "name"
    assert proxy["sector_fit_score"] == 16.0
    assert proxy["vehicle_quality_status"] == "watch_only"

    recommendations = prepare_recommendations_for_deterministic_allocation(
        [
            DiscoveryRecommendation(
                fund_code="020989",
                fund_name="南方恒生科技ETF发起联接(QDII)C",
                sector_name="恒生科技",
                action="建议关注",
                confidence="中",
            ),
            DiscoveryRecommendation(
                fund_code="007882",
                fund_name="易方达沪深300非银ETF联接C",
                sector_name="保险",
                action="建议关注",
                confidence="中",
            ),
        ],
        candidate_pool=rows,
    )
    guarded, _caveats, _eliminated = apply_discovery_guards(
        recommendations,
        candidate_pool=rows,
        held_codes=set(),
        profile=InvestorProfile(
            avoid_chasing=False,
            concentration_limit_percent=100,
            expected_investment_amount=10_000,
        ),
        budget_yuan=10_000,
        sector_heat=[],
        discovery_facts={
            "candidate_pool": rows,
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "恒生科技",
                    "score_policy_version": "sector_entry_maturity.2026-07.v2",
                    "entry_state": "ready_to_start",
                    "entry_readiness_score": 81.5,
                    "evidence_quality": "complete",
                    "confidence": "高",
                    "cumulative_5d_net_yi": 6.42,
                },
                {
                    "sector_label": "保险",
                    "score_policy_version": "sector_entry_maturity.2026-07.v2",
                    "entry_state": "ready_to_start",
                    "entry_readiness_score": 88.56,
                    "evidence_quality": "complete",
                    "confidence": "高",
                    "cumulative_5d_net_yi": 3.18,
                },
            ],
            "portfolio_snapshot": {
                "stale": False,
                "authoritative": True,
                "position_complete": True,
                "pending_transaction_count": 0,
            },
            "portfolio_position_truth": {
                "position_complete": True,
                "cash": {"known": True, "balance_yuan": "10000"},
                "positions": [],
            },
            "portfolio_gap": {
                "holding_count": 0,
                "total_amount": 0,
                "weight_denominator_yuan": 10_000,
                "available_budget_yuan": 10_000,
                "holdings_slim": [],
            },
        },
    )
    guarded_by_code = {item.fund_code: item for item in guarded}
    assert guarded_by_code["020989"].action == "分批买入", (
        guarded_by_code["020989"].points,
        guarded_by_code["020989"].validation_notes,
        guarded_by_code["020989"].amount_note,
        _caveats,
    )
    assert (guarded_by_code["020989"].suggested_amount_yuan or 0) > 0
    assert guarded_by_code["007882"].action == "建议关注"
    assert guarded_by_code["007882"].suggested_amount_yuan is None
