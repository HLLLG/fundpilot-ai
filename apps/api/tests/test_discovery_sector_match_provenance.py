from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_candidate_llm import slim_candidate_for_llm
from app.services.discovery_candidate_pool import (
    _sector_fit_score,
    build_candidate_pool,
    enrich_candidates,
    finalize_candidate_pool,
    rank_candidates_balanced_fallback,
)
from app.services.discovery_guard import apply_discovery_guards


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"sector_match_kind": "primary", "sector_confidence": 0.8}, 36.8),
        ({"sector_match_kind": "name"}, 16.0),
        ({"sector_match_kind": "new_issue"}, 18.0),
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

    enriched = enrich_candidates(built)
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
