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
    _is_execution_verified_primary_mapping,
    _name_matches_direction,
    _name_matches_sector,
    _sector_keywords,
    _sector_fit_score,
    _with_exact_passive_tracking_match,
    build_candidate_pool,
    enrich_candidates,
    finalize_candidate_pool,
    rank_candidates_balanced_fallback,
)
from app.services.discovery_guard import apply_discovery_guards
from app.services.discovery_sector_identity import (
    SECTOR_IDENTITY_PENDING,
    SECTOR_IDENTITY_VERIFIED,
    candidate_sector_identity_is_executable,
)


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


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"sector_match_kind": "primary", "sector_fit_score": 1}, True),
        ({"sector_match_kind": "tracking_exact", "sector_fit_score": 1}, True),
        ({"sector_match_kind": "name", "sector_fit_score": 99}, False),
        ({"sector_match_kind": "new_issue", "sector_fit_score": 18}, False),
        ({"sector_match_kind": "fallback", "sector_fit_score": 99}, False),
        (
            {
                "sector_match_kind": "primary",
                "sector_identity_status": SECTOR_IDENTITY_PENDING,
                "sector_fit_score": 99,
            },
            False,
        ),
        (
            {
                "sector_match_kind": "name",
                "sector_identity_status": SECTOR_IDENTITY_VERIFIED,
                "sector_identity_eligible": True,
                "sector_mapping_verified": True,
                "sector_fit_score": 99,
            },
            False,
        ),
        (
            {
                "sector_match_kind": "primary",
                "sector_identity_status": SECTOR_IDENTITY_VERIFIED,
                "sector_identity_eligible": False,
                "sector_fit_score": 99,
            },
            False,
        ),
        ({"sector_fit_score": 36}, True),  # pre-provenance report compatibility
    ],
)
def test_sector_identity_gate_uses_provenance_not_fit_score(
    row: dict,
    expected: bool,
) -> None:
    assert candidate_sector_identity_is_executable(row) is expected


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


def test_llm_primary_mapping_is_recall_only_until_independently_verified(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda _labels, limit_per_sector=20: [
            {
                "fund_code": "005576",
                "fund_name": "华泰柏瑞新金融地产混合A",
                "sector_name": "金融科技",
                "source": "precompute_llm",
                "confidence": 0.92,
            }
        ],
    )
    rank_row = {
        "fund_code": "005576",
        "fund_name": "华泰柏瑞新金融地产混合A",
        "fund_scale_yi": 20,
        "return_3m_percent": 6,
        "return_6m_percent": 8,
        "return_1y_percent": 10,
        "max_drawdown_1y_percent": -20,
        "established_date": "2016-01-01",
    }

    built = build_candidate_pool(
        ["金融科技"],
        per_sector=1,
        pool_cap=1,
        fetch_rank=lambda limit: [rank_row],
        fetch_new_funds=lambda limit: [],
        decision_at=_DECISION_AT,
    )

    assert built[0]["sector_match_kind"] == "fallback"
    assert built[0]["sector_mapping_verified"] is False
    assert built[0]["sector_identity_status"] == SECTOR_IDENTITY_PENDING
    assert built[0]["sector_identity_eligible"] is False
    assert built[0]["sector_fit_score"] == 16.0
    assert built[0]["selection_reason"] == "推断板块映射待核验"


def test_verified_gold_stock_mapping_is_not_consumed_by_gold_name_recall(
    monkeypatch,
) -> None:
    rank_row = {
        "fund_code": "021874",
        "fund_name": "中欧黄金股指数C",
        "fund_scale_yi": 2.0,
        "return_3m_percent": 3.0,
        "return_6m_percent": 8.0,
        "return_1y_percent": 20.0,
        "max_drawdown_1y_percent": -18.0,
        "established_date": "2024-01-01",
    }
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda _labels, limit_per_sector=20: [
            {
                "fund_code": "021874",
                "fund_name": "中欧黄金股指数C",
                "sector_name": "黄金股",
                "source": "precompute_benchmark",
                "confidence": 0.95,
                "detail": {
                    "benchmark_text": "中证沪深港黄金产业股票指数收益率×95%+存款×5%",
                },
            }
        ],
    )

    built = build_candidate_pool(
        ["黄金", "黄金股"],
        per_sector=1,
        pool_cap=2,
        fetch_rank=lambda limit: [rank_row],
        fetch_new_funds=lambda limit: [],
        decision_at=_DECISION_AT,
    )

    assert len(built) == 1
    assert built[0]["fund_code"] == "021874"
    assert built[0]["sector_label"] == "黄金股"
    assert built[0]["sector_match_kind"] == "primary"


def test_precious_metals_direction_keeps_gold_spot_but_not_gold_equity(
    monkeypatch,
) -> None:
    """贵金属方向可召回现货黄金，黄金股必须留在自己的板块上。"""

    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda _labels, limit_per_sector=20: [
            {
                "fund_code": "000217",
                "fund_name": "华安黄金易ETF联接A",
                "sector_name": "黄金",
                "source": "precompute_benchmark",
                "confidence": 0.9,
                "detail": {"benchmark_text": "上海黄金交易所Au99.99合约"},
            },
            {
                "fund_code": "021673",
                "fund_name": "国泰黄金股ETF联接A",
                "sector_name": "黄金股",
                "source": "precompute_benchmark",
                "confidence": 0.9,
                "detail": {
                    "benchmark_text": "中证沪深港黄金产业股票指数收益率×95%+存款×5%",
                },
            },
            {
                "fund_code": "021074",
                "fund_name": "华夏中证沪深港黄金产业股票ETF发起式联接A",
                "sector_name": "黄金股",
                "source": "precompute_benchmark",
                "confidence": 0.9,
                "detail": {
                    "benchmark_text": "中证沪深港黄金产业股票指数收益率×95%+存款×5%",
                },
            },
        ],
    )
    rank_rows = [
        {
            "fund_code": "000217",
            "fund_name": "华安黄金易ETF联接A",
            "fund_scale_yi": 80.0,
            "return_3m_percent": 6.0,
            "return_6m_percent": 12.0,
            "return_1y_percent": 25.0,
            "max_drawdown_1y_percent": -10.0,
            "established_date": "2013-08-01",
        },
        {
            "fund_code": "021673",
            "fund_name": "国泰黄金股ETF联接A",
            "fund_scale_yi": 6.0,
            "return_3m_percent": 10.0,
            "return_6m_percent": 20.0,
            "return_1y_percent": 40.0,
            "max_drawdown_1y_percent": -22.0,
            "established_date": "2024-05-01",
        },
        {
            "fund_code": "021074",
            "fund_name": "华夏中证沪深港黄金产业股票ETF发起式联接A",
            "fund_scale_yi": 8.0,
            "return_3m_percent": 8.0,
            "return_6m_percent": 16.0,
            "return_1y_percent": 30.0,
            "max_drawdown_1y_percent": -18.0,
            "established_date": "2024-03-01",
        },
    ]

    precious = build_candidate_pool(
        ["贵金属"],
        per_sector=1,
        pool_cap=1,
        fetch_rank=lambda limit: rank_rows,
        fetch_new_funds=lambda limit: [],
        decision_at=_DECISION_AT,
    )
    gold_equity = build_candidate_pool(
        ["黄金股"],
        per_sector=2,
        pool_cap=2,
        fetch_rank=lambda limit: rank_rows,
        fetch_new_funds=lambda limit: [],
        decision_at=_DECISION_AT,
    )

    precious_by_code = {row["fund_code"]: row for row in precious}
    assert set(precious_by_code) == {"000217"}
    assert precious_by_code["000217"]["sector_label"] == "贵金属"
    assert precious_by_code["000217"]["identity_sector_label"] == "黄金"

    gold_equity_by_code = {row["fund_code"]: row for row in gold_equity}
    assert set(gold_equity_by_code) == {"021673", "021074"}
    for row in gold_equity_by_code.values():
        assert row["sector_label"] == "黄金股"
        assert row["identity_sector_label"] == "黄金股"


def test_gold_direction_still_rejects_gold_stock_identity(monkeypatch) -> None:
    """同义映射是单向的：黄金方向不得召回黄金股身份的基金。"""

    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda _labels, limit_per_sector=20: [
            {
                "fund_code": "021673",
                "fund_name": "国泰黄金股ETF联接A",
                "sector_name": "黄金股",
                "source": "precompute_benchmark",
                "confidence": 0.9,
                "detail": {
                    "benchmark_text": "中证沪深港黄金产业股票指数收益率×95%+存款×5%",
                },
            }
        ],
    )

    built = build_candidate_pool(
        ["黄金"],
        per_sector=2,
        pool_cap=2,
        fetch_rank=lambda limit: [],
        fetch_new_funds=lambda limit: [],
        decision_at=_DECISION_AT,
    )

    assert built == []


def test_precious_metals_tracking_rejects_gold_equity_index() -> None:
    """贵金属方向不得把黄金股跟踪指数核验成同义命中。"""

    row = _with_exact_passive_tracking_match(
        {
            "fund_code": "021074",
            "fund_name": "华夏中证沪深港黄金产业股票ETF发起式联接A",
            "fund_type": "股票型",
            "sector_label": "贵金属",
            "sector_match_kind": "name",
            "tracking_reference_text": "中证沪深港黄金产业股票指数（931238）",
        }
    )

    assert row["sector_match_kind"] == "name"
    assert row["sector_identity_mismatch"]["verified_sector_label"] == "黄金股"
    assert row["sector_identity_mismatch"]["target_sector_label"] == "贵金属"


def test_exact_tracking_accepts_direction_synonym_for_precious_metals() -> None:
    """贵金属方向下，黄金 ETF 联接的精确跟踪标的核验应当通过。"""

    row = _with_exact_passive_tracking_match(
        {
            "fund_code": "000217",
            "fund_name": "华安黄金易ETF联接A",
            "fund_type": "股票指数",
            "sector_label": "贵金属",
            "sector_match_kind": "name",
            "tracking_reference_text": "上海黄金交易所Au99.99合约",
        }
    )

    assert row["sector_match_kind"] == "tracking_exact"
    assert row["sector_identity_status"] == SECTOR_IDENTITY_VERIFIED
    assert row["identity_sector_label"] == "黄金"
    assert row["tracking_reference_match"]["sector_label"] == "黄金"


def test_stale_broad_financial_benchmark_cannot_verify_fintech_mapping() -> None:
    assert not _is_execution_verified_primary_mapping(
        {
            "source": "precompute_benchmark",
            "detail": (
                '{"index_code":"000992","benchmark_text":'
                '"中证全指金融地产指数收益率×95%+银行活期存款利率×5%"}'
            ),
        },
        expected_sector="金融科技",
    )


def test_discovery_keywords_cover_target_directions_without_single_cloud_false_positive() -> None:
    media_keywords = _sector_keywords("传媒", None)
    gold_keywords = _sector_keywords("贵金属", None)
    cloud_keywords = _sector_keywords("云计算", None)

    assert _name_matches_sector("某某游戏传媒ETF联接A", media_keywords)
    assert _name_matches_direction("华安黄金易ETF联接A", gold_keywords, "贵金属")
    assert not _name_matches_direction("某某黄金产业股票A", gold_keywords, "贵金属")
    assert not _name_matches_direction(
        "华夏中证沪深港黄金产业股票ETF发起式联接A",
        gold_keywords,
        "贵金属",
    )
    assert _name_matches_sector("某某云计算ETF联接A", cloud_keywords)
    assert not _name_matches_sector("彩云成长混合A", cloud_keywords)


def _holdings_identity_row(code: str, name: str, sector: str = "半导体") -> dict:
    return {
        "fund_code": code,
        "fund_name": name,
        "sector_name": sector,
        "source": "holdings_infer",
        "identity_status": "verified",
        "confidence": 0.9,
    }


def _quality_rank_row(code: str, name: str, *, return_3m: float = 8.0) -> dict:
    return {
        "fund_code": code,
        "fund_name": name,
        "fund_scale_yi": 20,
        "return_3m_percent": return_3m,
        "return_6m_percent": 15,
        "return_1y_percent": 24,
        "max_drawdown_1y_percent": -12,
        "established_date": "2020-01-01",
    }


def test_name_only_catalogue_row_is_not_recalled_when_identity_is_thin(
    monkeypatch,
) -> None:
    verified_row = _holdings_identity_row("000111", "半导体持仓核验000111A")
    name_only = _quality_rank_row("088888", "某某半导体ETF联接A", return_3m=80.0)

    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda _labels, limit_per_sector=None: [verified_row],
    )

    built = build_candidate_pool(
        ["半导体"],
        per_sector=2,
        pool_cap=2,
        fetch_rank=lambda limit: [
            _quality_rank_row(verified_row["fund_code"], verified_row["fund_name"]),
            name_only,
        ],
        fetch_new_funds=lambda limit: [],
        decision_at=_DECISION_AT,
    )

    assert [row["fund_code"] for row in built] == ["000111"]
    assert built[0]["sector_match_kind"] == "primary"


def test_empty_identity_index_does_not_fall_back_to_catalogue_name_scan(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda *_args, **_kwargs: [],
    )

    built = build_candidate_pool(
        ["半导体"],
        per_sector=2,
        pool_cap=2,
        fetch_rank=lambda limit: [
            _quality_rank_row("088888", "某某半导体ETF联接A", return_3m=80.0),
            _quality_rank_row("088889", "某某半导体指数C", return_3m=40.0),
        ],
        fetch_new_funds=lambda limit: [],
        decision_at=_DECISION_AT,
    )

    assert built == []


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
                "detail": {
                    "benchmark_text": (
                        "中证全指半导体产品与设备指数收益率×95%+"
                        "银行活期存款利率（税后）×5%"
                    )
                },
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

    # Identity-index recall must already carry persistable primary provenance.
    assert built[0]["sector_match_kind"] == "primary"
    assert built[0]["sector_identity_status"] == SECTOR_IDENTITY_VERIFIED
    assert built[0]["sector_identity_eligible"] is True
    assert built[0]["sector_fit_score"] == 36.8
    assert built[0]["quality_score_version"] == "fund_quality.v5"
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
    assert enriched[0]["quality_score_version"] == "fund_quality.v5"

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
    assert exact["sector_identity_status"] == SECTOR_IDENTITY_VERIFIED
    assert exact["sector_identity_eligible"] is True
    assert exact["sector_fit_score"] == 34.0
    assert exact["tracking_reference_match"]["index_code"] == "HSTECH"
    assert exact["tracking_reference_match"]["formal_excess_eligible"] is False
    assert exact["vehicle_quality_method"] == "passive_index_vehicle"
    assert exact["vehicle_quality_status"] == "eligible"
    assert exact["vehicle_quality_score"] >= exact["vehicle_quality_threshold"]

    proxy = by_code["007882"]
    assert proxy["sector_match_kind"] == "name"
    assert proxy["sector_identity_status"] == SECTOR_IDENTITY_PENDING
    assert proxy["sector_identity_eligible"] is False
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


def test_exact_tracking_uses_canonical_sector_not_market_quote_proxy_code() -> None:
    coal = _with_exact_passive_tracking_match(
        {
            "fund_code": "008279",
            "fund_name": "国泰中证煤炭ETF联接A",
            "fund_type": "股票型",
            "sector_label": "煤炭",
            "sector_match_kind": "name",
            "tracking_reference_text": "中证煤炭指数（399998）",
        }
    )

    assert coal["sector_match_kind"] == "tracking_exact"
    assert coal["sector_identity_status"] == SECTOR_IDENTITY_VERIFIED
    assert coal["tracking_reference_match"]["index_code"] == "399998"

    distinct_theme = _with_exact_passive_tracking_match(
        {
            "fund_code": "021873",
            "fund_name": "中欧黄金股指数A",
            "fund_type": "股票型",
            "sector_label": "黄金",
            "sector_match_kind": "name",
            "tracking_reference_text": "中证沪深港黄金产业股票指数（931238）",
        }
    )

    assert distinct_theme["sector_match_kind"] == "name"
    assert "tracking_reference_match" not in distinct_theme
    assert distinct_theme["sector_identity_mismatch"] == {
        "relation_kind": "tracking_reference",
        "target_sector_label": "黄金",
        "verified_sector_label": "黄金股",
        "index_code": "931238",
        "index_name": "中证沪深港黄金产业股票指数",
        "benchmark_text_source_kind": None,
        "exact": True,
    }
