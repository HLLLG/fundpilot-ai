from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import (
    DiscoveryRecommendation,
    FundNavHistory,
    FundNavPoint,
    InvestorProfile,
)
from app.services.discovery_candidate_pool import (
    _with_data_quality_gate,
    _with_quality_score,
    enrich_candidates,
    finalize_candidate_pool,
)
from app.services.discovery_guard import (
    _quant_coverage_explanation,
    apply_discovery_guards,
    finalize_discovery_allocation_projection,
)


_DECISION_AT = datetime(2026, 7, 14, tzinfo=timezone.utc)
_DECISION_DATE = _DECISION_AT.date()


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
        ],
        decision_at=_DECISION_AT,
    )

    item = result[0]
    assert "max_drawdown_1y_percent" not in item
    assert "return_1y_percent" not in item
    assert 0 <= item["fund_quality_score"] <= 100
    assert item["quality_score_version"] == "fund_quality.v5"
    assert item["quality_gate"]["status"] == "eligible"
    assert item["quality_gate"]["coverage_percent"] == 100.0
    assert "nav_quality_return_coverage" not in item


def test_share_family_alternatives_are_not_nav_enriched(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (_snapshot(), None),
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "013596": {
                "fund_code": "013596",
                "fund_scale_yi": 3.5,
                "fund_category": "股票型",
                "fund_manager": "测试经理",
                "established_date": "2021-09-13",
                "profile_status": "complete",
                "benchmark_text": "中证煤炭等权指数收益率×95%+银行活期存款利率×5%",
                "benchmark_text_kind": "performance_benchmark",
            },
            "016347": {
                "fund_code": "016347",
                "fund_scale_yi": 2.0,
                "fund_category": "股票型",
                "fund_manager": "测试经理",
                "established_date": "2022-10-27",
                "profile_status": "complete",
            },
        },
    )
    base = {
        "sector_label": "煤炭",
        "return_3m_percent": -7.0,
        "return_6m_percent": 4.0,
        "return_1y_percent": 15.0,
        "max_drawdown_1y_percent": -25.0,
        "fund_scale_yi": 2.0,
    }
    pool = [
        {
            **base,
            "fund_code": "016347",
            "fund_name": "招商中证煤炭等权指数(LOF)E",
            "opportunity_score_20_60d": 100.0,
            "_share_family_alternatives": [
                {
                    **base,
                    "fund_code": "013596",
                    "fund_name": "招商中证煤炭等权指数(LOF)C",
                    "opportunity_score_20_60d": 1.0,
                }
            ],
        }
    ]

    enriched = enrich_candidates(pool, decision_at=_DECISION_AT)
    result = finalize_candidate_pool(
        enriched,
        ["煤炭"],
        per_sector=3,
        pool_cap=3,
        discovery_strategy="opportunity_first",
    )

    assert {item["fund_code"] for item in enriched} == {"016347"}
    assert [item["fund_code"] for item in result] == ["016347"]
    assert enriched[0]["share_family"]["member_codes"] == ["016347", "013596"]
    assert result[0]["share_family"]["member_codes"] == ["016347", "013596"]
    assert result[0]["share_family"]["selected_basis"] == (
        "prescreen_representative_nav_not_expanded"
    )


def test_one_year_drawdown_no_longer_changes_quality_gate():
    row = {
        "return_3m_percent": 18.0,
        "return_6m_percent": 32.0,
        "max_drawdown_1y_percent": -63.0,
        "fund_scale_yi": 6.0,
        "established_date": "2023-01-01",
        "fund_manager": "测试经理",
        "nav_date": "2026-07-10",
    }

    opportunity = _with_data_quality_gate(
        row,
        as_of_date=_DECISION_DATE,
        discovery_strategy="opportunity_first",
    )
    risk_first = _with_data_quality_gate(
        row,
        as_of_date=_DECISION_DATE,
        discovery_strategy="risk_first",
    )

    assert opportunity["quality_gate"]["status"] == "eligible"
    assert risk_first["quality_gate"]["status"] == "eligible"
    assert "max_drawdown_1y_percent" not in opportunity
    assert "max_drawdown_1y_percent" not in risk_first


def test_opportunity_quality_score_does_not_reward_shallow_drawdown():
    base = {
        "fund_code": "020356",
        "fund_name": "高弹性基金A",
        "sector_label": "半导体",
        "sector_match_kind": "primary",
        "sector_confidence": 0.9,
        "return_3m_percent": 20.0,
        "return_6m_percent": 35.0,
        "return_1y_percent": 45.0,
        "fund_scale_yi": 8.0,
        "quality_gate": {"status": "eligible", "coverage_percent": 100.0},
    }
    shallow = _with_quality_score(
        {**base, "max_drawdown_1y_percent": -12.0},
        fund_type_preference="any",
        discovery_strategy="opportunity_first",
    )
    deep = _with_quality_score(
        {**base, "max_drawdown_1y_percent": -58.0},
        fund_type_preference="any",
        discovery_strategy="opportunity_first",
    )

    assert shallow["fund_quality_score"] == deep["fund_quality_score"]
    assert shallow["quality_score_components"]["drawdown_control"] == 7.5
    assert deep["quality_score_components"]["drawdown_control"] == 7.5


def test_opportunity_quality_does_not_penalize_high_one_year_return():
    base = {
        "fund_code": "020356",
        "fund_name": "高弹性基金A",
        "sector_label": "半导体",
        "sector_match_kind": "primary",
        "sector_confidence": 0.9,
        "return_3m_percent": 20.0,
        "return_6m_percent": 35.0,
        "max_drawdown_1y_percent": -42.0,
        "fund_scale_yi": 8.0,
        "quality_gate": {"status": "eligible", "coverage_percent": 100.0},
    }
    ordinary = _with_quality_score(
        {**base, "return_1y_percent": 45.0},
        fund_type_preference="any",
        discovery_strategy="opportunity_first",
    )
    high_return = _with_quality_score(
        {**base, "return_1y_percent": 125.0},
        fund_type_preference="any",
        discovery_strategy="opportunity_first",
    )

    assert high_return["fund_quality_score"] == ordinary["fund_quality_score"]
    assert not any("追高偏差" in item for item in high_return["quality_penalties"])


def test_enrichment_derives_drawdown_from_fetched_nav_when_diagnostics_is_missing(
    monkeypatch,
):
    first_day = date(2025, 11, 4)
    trend = FundNavHistory(
        fund_code="020356",
        fund_name="test",
        source="akshare",
        points=[
            FundNavPoint(
                date=(first_day + timedelta(days=index)).isoformat(),
                nav=nav,
            )
            for index, nav in enumerate([100.0] * 251 + [80.0])
        ],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (_snapshot(drawdown=None), trend),
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "020356": {
                "fund_code": "020356",
                "fund_scale_yi": 3.55,
                "fund_manager": "test manager",
                "established_date": "2024-01-23",
                "profile_status": "complete",
            }
        },
    )

    item = enrich_candidates(
        [
            {
                "fund_code": "020356",
                "fund_name": "test fund",
                "sector_label": "test sector",
                "return_3m_percent": 18.0,
                "return_6m_percent": 35.0,
            }
        ],
        decision_at=_DECISION_AT,
    )[0]

    assert item["max_drawdown_1y_percent"] == -20.0
    assert "max_drawdown_1y_percent" not in item["quality_gate"]["missing_fields"]


def test_enrichment_backfills_three_month_return_from_short_nav_history(
    monkeypatch,
):
    first_day = date(2026, 7, 13) - timedelta(days=89)
    points = []
    nav = 1.0
    for index in range(90):
        if index:
            nav *= 1.001
        points.append(
            FundNavPoint(
                date=(first_day + timedelta(days=index)).isoformat(),
                nav=nav,
                daily_return_percent=0.1,
            )
        )
    trend = FundNavHistory(
        fund_code="000930",
        fund_name="test",
        source="akshare",
        points=points,
    )
    snapshot = SimpleNamespace(
        return_1y_percent=999.0,
        max_drawdown_1y_percent=-99.0,
        fund_scale_yi=None,
        management_fee="0.50%",
        fund_type="商品型-非QDII",
        latest_nav=nav,
        nav_date="2026-07-13",
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (snapshot, trend),
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_fund_research_profiles_cached",
        lambda _codes: {
            "000930": {
                "fund_code": "000930",
                "fund_scale_yi": 5.07,
                "fund_manager": "测试经理",
                "established_date": "2014-12-18",
                "profile_status": "complete",
            }
        },
    )

    item = enrich_candidates(
        [
            {
                "fund_code": "000930",
                "fund_name": "测试黄金I",
                "sector_label": "黄金",
                "sector_match_kind": "primary",
            }
        ],
        decision_at=_DECISION_AT,
        discovery_strategy="opportunity_first",
    )[0]

    assert item["return_3m_percent"] == pytest.approx(
        ((1.001**60) - 1) * 100,
        abs=1e-4,
    )
    assert "return_1y_percent" not in item
    assert "max_drawdown_1y_percent" not in item
    assert item["quality_gate"]["status"] == "eligible"
    assert item["quality_gate"]["coverage_percent"] == 100.0
    assert item["return_3m_percent_source"] == "akshare_total_return"
    assert item["nav_quality_return_coverage"] == 1.0


def test_enrichment_converts_xq_shares_with_report_nav_instead_of_treating_as_aum(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.fund_risk_metrics.persist_risk_metrics_from_points",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_sharpe.attach_alipay_style_sharpes",
        lambda row, *_args, **_kwargs: row,
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.FundDataService._snapshot_and_trend_for_holding",
        lambda *_args, **_kwargs: (
            _snapshot(),
            SimpleNamespace(
                source="akshare",
                points=[
                    FundNavPoint(date="2026-03-31", nav=1.2),
                    FundNavPoint(date="2026-07-10", nav=1.2),
                ],
            ),
        ),
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
        ],
        decision_at=_DECISION_AT,
    )[0]

    assert item["fund_scale_yi"] == 2.4
    assert item["fund_scale_basis"] == "quarterly_net_assets"
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
    assert any("2亿元" in reason for reason in item["quality_gate"]["reasons"])


def test_scale_below_2yi_is_excluded():
    item = _with_data_quality_gate(
        {
            "fund_scale_yi": 1.99,
            "return_3m_percent": 8.0,
            "return_6m_percent": 12.0,
            "max_drawdown_1y_percent": -18.0,
            "established_date": "2024-01-01",
            "fund_manager": "测试经理",
            "nav_date": "2026-07-10",
        },
        as_of_date=_DECISION_DATE,
    )
    assert item["quality_gate"]["status"] == "excluded"
    assert item["quality_gate"]["eligible"] is False
    assert any("低于2亿元" in reason for reason in item["quality_gate"]["reasons"])
    assert "max_drawdown_1y_percent" not in item


def test_scale_at_2yi_is_eligible():
    item = _with_data_quality_gate(
        {
            "fund_scale_yi": 2.0,
            "return_3m_percent": 8.0,
            "return_6m_percent": 12.0,
            "max_drawdown_1y_percent": -18.0,
            "established_date": "2024-01-01",
            "fund_manager": "测试经理",
            "nav_date": "2026-07-10",
        },
        as_of_date=_DECISION_DATE,
    )
    assert item["quality_gate"]["status"] == "eligible"
    assert item["quality_gate"]["eligible"] is True
    assert "max_drawdown_1y_percent" not in item


def test_fund_established_one_year_is_eligible():
    item = _with_data_quality_gate(
        {
            "fund_scale_yi": 2.0,
            "return_3m_percent": 6.0,
            "established_date": "2025-07-14",
            "fund_manager": "测试经理",
            "nav_date": "2026-07-10",
        },
        as_of_date=_DECISION_DATE,
    )
    assert item["quality_gate"]["status"] == "eligible"


def test_fund_established_under_one_year_is_excluded():
    item = _with_data_quality_gate(
        {
            "fund_scale_yi": 2.0,
            "return_3m_percent": 6.0,
            "established_date": "2025-07-15",
            "fund_manager": "测试经理",
            "nav_date": "2026-07-10",
        },
        as_of_date=_DECISION_DATE,
    )
    assert item["quality_gate"]["status"] == "excluded"
    assert any("成立不足1年" in reason for reason in item["quality_gate"]["reasons"])


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
    assert item["quality_gate"]["coverage_percent"] == 25.0
    assert set(item["quality_gate"]["profile_stale_fields"]) == {
        "fund_scale_yi",
        "established_date",
        "fund_manager",
    }
    assert not any("低于2亿元" in reason for reason in item["quality_gate"]["reasons"])


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
        },
        as_of_date=_DECISION_DATE,
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
    assert "fund_scale_yi" in invalid["quality_gate"]["missing_fields"]


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


def test_final_candidate_pool_ignores_platform_state_when_deduplicating_family() -> None:
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

    assert [item["fund_code"] for item in result] == ["020639"]
    assert result[0]["share_family"]["member_codes"] == ["020639", "020640"]
    assert result[0]["share_family"]["selected_basis"] == (
        "quality_and_opportunity_then_share_class_priority"
    )
    assert "fee_comparison_status" not in result[0]["share_family"]


def test_final_candidate_pool_does_not_compare_sales_platform_costs() -> None:
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

    assert [item["fund_code"] for item in result] == ["020639"]
    family = result[0]["share_family"]
    assert family["selected_basis"] == (
        "quality_and_opportunity_then_share_class_priority"
    )
    assert "comparison_amount_yuan" not in family
    assert "member_cost_upper_bound_percent" not in family


def test_final_candidate_pool_collapses_e_share_class_with_a_family() -> None:
    pool = [
        {
            "fund_code": "008279",
            "fund_name": "国泰中证煤炭ETF联接A",
            "fund_type": "股票型",
            "sector_label": "煤炭",
            "fund_quality_score": 90,
            "quality_gate": {"status": "eligible"},
        },
        {
            "fund_code": "022501",
            "fund_name": "国泰中证煤炭ETF联接E",
            "fund_type": "股票型",
            "sector_label": "煤炭",
            "fund_quality_score": 88,
            "quality_gate": {"status": "eligible"},
        },
    ]

    result = finalize_candidate_pool(pool, ["煤炭"], per_sector=3, pool_cap=3)

    assert [item["fund_code"] for item in result] == ["008279"]
    assert result[0]["share_family"]["member_codes"] == ["008279", "022501"]


def test_guard_removes_excluded_candidate_and_clears_non_buy_amounts():
    excluded = {
        "fund_code": "021627",
        "fund_name": "小规模基金C",
        "sector_label": "半导体",
        "quality_gate": {
            "status": "excluded",
            "eligible": False,
            "reasons": ["最新估算规模低于2亿元"],
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


def test_guard_does_not_block_buy_on_one_year_drawdown():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate["max_drawdown_1y_percent"] = -25.0
    guarded, _caveats, _eliminated = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=3000,
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"}
        },
    )
    assert guarded[0].action == "分批买入"
    assert all("近1年最大回撤" not in item for item in guarded[0].points)


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
    assert "PIT v3 量化模型尚未达到可执行条件" in guarded[0].points[0]
    assert facts["data_evidence_guard"]["quant_evidence_blocked_fund_codes"] == [
        "021627"
    ]
    assert facts["data_evidence_guard"]["quant_evidence_uncovered_reasons_by_fund"] == {
        "021627": "pit_v3_not_ready"
    }
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
    avoid_chasing: bool = False,
):
    profile = InvestorProfile(
        concentration_limit_percent=100,
        avoid_chasing=avoid_chasing,
    )
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


def test_guard_blocks_gold_equity_buy_when_gold_already_held():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate.update(
        {
            "fund_code": "021958",
            "fund_name": "南方黄金股A",
            "sector_label": "黄金股",
        }
    )
    guarded, caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="021958",
                fund_name="南方黄金股A",
                sector_name="黄金股",
                action="分批买入",
                suggested_amount_yuan=3500,
                confidence="中",
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "portfolio_gap": {
                "available_budget_yuan": 10_000,
                "total_amount": 2044,
                "weight_denominator_yuan": 18_000,
                "holdings_slim": [
                    {
                        "fund_code": "002610",
                        "fund_name": "博时黄金ETF联接A",
                        "sector_name": "黄金",
                        "holding_amount": 2044,
                    }
                ],
            },
            "recommendation_candidate_scope": {
                "theme_vehicle_fallbacks": {
                    "021958": {
                        "thesis_sector_label": "黄金",
                        "vehicle_sector_label": "黄金股",
                        "entry_path": "theme_vehicle_fallback",
                    }
                }
            },
            "sector_opportunities": [
                {
                    "sector_label": "黄金",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "entry_state": "ready_to_start",
                    "opportunity_available": True,
                },
                {
                    "sector_label": "黄金股",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "entry_state": "ready_on_pullback",
                    "opportunity_available": True,
                },
            ],
        },
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None
    assert any("已有「黄金」敞口" in point for point in guarded[0].points)
    assert any("黄金回退载体" in item for item in caveats)


def test_opportunity_first_keeps_quant_coverage_as_soft_risk_input():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate.update(
        {
            "max_drawdown_1y_percent": -37.26,
            "nav_trend": {
                "recent_5d_change_percent": 1.8,
                "return_20d_percent": 4.2,
                "max_drawdown_20d_percent": -5.4,
                "return_60d_percent": 7.6,
                "max_drawdown_60d_percent": -11.2,
                "distance_from_high_percent": -6.0,
            },
        }
    )
    guarded, caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=1000,
                confidence="高",
                points=["板块资金和净值趋势共同改善。"],
                risks=["近20日最大回撤 -5.4%，需关注修复是否持续。"],
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {
                "discovery_strategy": "opportunity_first"
            },
            "candidate_factor_scores": {
                "available": False,
                "message": "当前因子样本不足",
            },
        },
    )

    assert guarded[0].action == "分批买入"
    assert guarded[0].suggested_amount_yuan == 1000
    assert guarded[0].hold_horizon == "1-3个月"
    assert guarded[0].confidence == "中"
    assert guarded[0].points[0] == "板块资金和净值趋势共同改善。"
    assert any("量化 IC 快照当前不可用" in item for item in guarded[0].points)
    assert any("系统级量化证据状态" in item for item in guarded[0].validation_notes)
    assert all("近1年最大回撤" not in item for item in guarded[0].risks)
    assert all("严重不符" not in item for item in guarded[0].risks)
    assert any("未把证据不足误判为负面信号" in item for item in caveats)


def test_opportunity_first_explains_system_wide_v2_to_v3_gap_without_relaxing_gate():
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
                confidence="高",
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {
                "discovery_strategy": "opportunity_first"
            },
            "candidate_factor_scores": {
                "available": True,
                "model_version": "factor_ic.v2",
                "selected_fund_codes": ["020356"],
                "ic_status": {
                    "state": "available",
                    "available": True,
                    "stale": False,
                    "cohort_mode": "current_survivors",
                },
                "holdings": [
                    {
                        "fund_code": "020356",
                        "descriptive_applicable": True,
                        "execution_qualified": False,
                        "execution_qualified_factor_keys": [],
                    }
                ],
            },
        },
    )

    assert guarded[0].action == "分批买入"
    assert guarded[0].suggested_amount_yuan == 1000
    assert guarded[0].confidence == "中"
    assert any("PIT v3 量化模型尚未达到可执行条件" in item for item in guarded[0].points)
    assert any("未用 v2/非 PIT 因子替代" in item for item in guarded[0].validation_notes)
    assert all("量化模型目前没有给这只基金加分" not in item for item in guarded[0].points)
    assert any("系统级证据状态" in item for item in caveats)


@pytest.mark.parametrize(
    ("factor_patch", "expected_reason", "expected_text"),
    [
        (
            {"selected_fund_codes": ["999999"], "coverage_limit": 12},
            "candidate_outside_online_factor_budget",
            "未进入本次前 12 只线上量化候选",
        ),
        (
            {
                "selected_fund_codes": ["020356"],
                "holdings": [
                    {
                        "fund_code": "020356",
                        "execution_qualification": {
                            "reason": "descriptive_factor_input_not_applicable"
                        },
                    }
                ],
            },
            "descriptive_factor_input_not_applicable",
            "同类分类或净值因子特征不完整",
        ),
        (
            {
                "selected_fund_codes": ["020356"],
                "holdings": [
                    {
                        "fund_code": "020356",
                        "execution_qualification": {
                            "reason": "target_factor_feature_not_fresh"
                        },
                    }
                ],
            },
            "target_factor_feature_not_fresh",
            "目标净值因子特征不够新",
        ),
        (
            {
                "selected_fund_codes": ["020356"],
                "holdings": [
                    {
                        "fund_code": "020356",
                        "execution_qualification": {
                            "reason": "no_statistically_and_economically_qualified_factor"
                        },
                    }
                ],
            },
            "no_statistically_and_economically_qualified_factor",
            "同时通过统计显著性与扣费后经济显著性门槛",
        ),
    ],
)
def test_quant_coverage_explanation_identifies_the_first_decisive_v3_gate(
    factor_patch: dict,
    expected_reason: str,
    expected_text: str,
) -> None:
    factor_scores = {
        "available": True,
        "model_version": "factor_ic.v3",
        "ic_status": {
            "state": "available",
            "available": True,
            "stale": False,
            "cohort_mode": "point_in_time",
        },
        **factor_patch,
    }

    explanation = _quant_coverage_explanation(factor_scores, "020356")

    assert explanation.reason_code == expected_reason
    assert expected_text in explanation.point
    assert "不等于" in explanation.validation_note or "不代表" in explanation.validation_note


def test_final_discovery_projection_is_idempotent_and_replaces_stale_projection():
    recommendation = DiscoveryRecommendation(
        fund_code="020356",
        fund_name="守卫测试基金A",
        sector_name="半导体",
        action="分批买入",
        suggested_amount_yuan=1000,
        points=[
            "保留的业务依据。",
            "保留的业务依据。",
            "系统校验后最终动作调整为建议关注。",
            "系统校验后的最终动作：建议关注。",
        ],
    )

    finalize_discovery_allocation_projection(recommendation)
    recommendation.action = "等待回调"
    recommendation.suggested_amount_yuan = None
    finalize_discovery_allocation_projection(recommendation)

    projections = [
        point
        for point in recommendation.points
        if point.startswith("系统校验后的最终动作：")
    ]
    assert projections == ["系统校验后的最终动作：等待回调。"]
    assert recommendation.points[0] == "保留的业务依据。"


def test_opportunity_first_waits_only_when_price_extension_and_flow_weakness_coexist():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate["nav_trend"] = {
        "recent_5d_change_percent": 7.0,
        "return_20d_percent": 17.0,
        "distance_from_high_percent": -1.0,
    }
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
        avoid_chasing=True,
        extra_facts={
            "effective_configuration": {
                "discovery_strategy": "opportunity_first"
            },
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score": 82,
                    "confidence": "高",
                    "opportunity_available": True,
                    "pattern_label": "distribution",
                    "cumulative_5d_net_yi": -3.2,
                }
            ],
        },
    )

    assert guarded[0].action == "等待回调"
    assert any("短线涨幅已经偏快" in item for item in guarded[0].points)


def test_weak_evidence_downgrade_names_exact_trigger_values():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate["fund_quality_score"] = 52.3
    candidate["sector_fit_score"] = 16.0
    guarded, caveats, _ = _run_guard_for_test(
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
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score": 58.4,
                    "confidence": "低",
                    "opportunity_available": True,
                }
            ],
        },
    )

    assert guarded[0].action == "建议关注"
    assert "主方向置信度为低" in guarded[0].points[0]
    assert "板块机会分 58.40，低于 60" in guarded[0].points[0]
    assert "基金质量分 52.30，低于 55" in guarded[0].points[0]
    assert "基金代码对应的板块身份尚未通过独立核验" in guarded[0].points[0]
    assert any("动作降级触发项" in item for item in guarded[0].validation_notes)
    assert any("未达到买入证据门槛" in item for item in caveats)


def test_entry_maturity_v2_promotes_verified_ready_direction_to_initial_buy():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate["nav_trend"] = {
        "recent_5d_change_percent": 2.0,
        "return_20d_percent": 8.0,
        "distance_from_high_percent": -5.0,
    }
    guarded, _caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="建议关注",
                suggested_amount_yuan=1000,
                confidence="中",
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-07.v2",
                    "entry_state": "ready_to_start",
                    "entry_readiness_score": 72.0,
                    "evidence_quality": "complete",
                    "confidence": "高",
                    "cumulative_5d_net_yi": 18.0,
                }
            ],
        },
    )

    assert guarded[0].action == "分批买入"
    assert guarded[0].suggested_amount_yuan == 1000
    assert any("方向成熟度 V2" in item for item in guarded[0].points)
    assert any("ready_to_start" in item for item in guarded[0].validation_notes)


def test_entry_maturity_v3_ready_state_owns_the_action_boundary():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate["nav_trend"] = {
        "recent_5d_change_percent": 2.0,
        "return_20d_percent": 8.0,
        "distance_from_high_percent": -5.0,
    }
    guarded, _caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="建议关注",
                suggested_amount_yuan=1000,
                confidence="中",
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "entry_state": "ready_to_start",
                    "trend_strength_score": 78.0,
                    "evidence_quality": "complete",
                    "confidence": "中",
                    "pattern_label": "distribution",
                    "cumulative_5d_net_yi": -3.0,
                }
            ],
        },
    )

    assert guarded[0].action == "分批买入"
    assert any("方向成熟度 V3" in item for item in guarded[0].points)
    assert all("近5日主力净流出" not in item for item in guarded[0].points)


def test_fund_recovery_can_replace_only_the_v3_sector_position_gate():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate["fund_entry_signal"] = {
        "policy_version": "fund_entry_position.2026-08.v1",
        "status": "recovery_ready",
        "entry_ready": True,
        "invalidation_signals": ["近5日收益重新转负且20日修复率跌回40%以下"],
    }
    guarded, _caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="等待回调",
                suggested_amount_yuan=1000,
                confidence="中",
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "entry_state": "ready_on_pullback",
                    "trend_strength_score": 78.0,
                    "participation_score": 42.0,
                    "position_risk_score": 20.0,
                    "evidence_quality": "complete",
                    "confidence": "中",
                    "entry_gate_inputs": {"mainline_status": "confirmed"},
                }
            ],
        },
    )

    assert guarded[0].action == "分批买入"
    assert any("基金自身20日回撤修复已过半" in point for point in guarded[0].points)
    assert any("严格退出复核" in risk for risk in guarded[0].risks)


def test_fund_recovery_cannot_replace_weak_v3_participation():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate["fund_entry_signal"] = {
        "policy_version": "fund_entry_position.2026-08.v1",
        "status": "recovery_ready",
        "entry_ready": True,
    }
    guarded, _caveats, _ = _run_guard_for_test(
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
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "entry_state": "ready_on_pullback",
                    "trend_strength_score": 78.0,
                    "participation_score": 30.0,
                    "position_risk_score": 20.0,
                    "evidence_quality": "complete",
                    "entry_gate_inputs": {"mainline_status": "confirmed"},
                }
            ],
        },
    )

    assert guarded[0].action == "等待回调"


def test_improving_flow_and_fund_pullback_open_reduced_probe_and_remove_false_chase():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate["nav_trend"] = {
        "recent_5d_change_percent": 1.6,
        "return_20d_percent": 8.0,
        "distance_from_20d_high_percent": -1.06,
    }
    candidate["fund_entry_signal"] = {
        "policy_version": "fund_entry_position.2026-08.v2",
        "status": "pullback_ready",
        "entry_path": "benign_pullback",
        "entry_ready": True,
        "first_tranche_scale": 0.5,
        "overheat_flags": [],
        "invalidation_signals": ["近5日收益重新转负且20日修复率跌回40%以下"],
    }
    guarded, _caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="等待回调",
                suggested_amount_yuan=1000,
                confidence="中",
                risks=["当前距20日高点仅-1.06%，短期追高风险"],
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "entry_state": "ready_on_pullback",
                    "trend_strength_score": 68.0,
                    "participation_score": 25.0,
                    "position_risk_score": 58.0,
                    "evidence_quality": "complete",
                    "flow_improving_probe_eligible": True,
                    "waiting_reason_code": "fund_entry_confirmation",
                    "first_tranche_scale": 0.4,
                    "overheat_flags": [],
                    "entry_gate_inputs": {
                        "mainline_status": "forming",
                        "flow_improving": True,
                    },
                }
            ],
        },
    )

    assert guarded[0].action == "分批买入"
    assert guarded[0].entry_path == "flow_improving_probe"
    assert guarded[0].entry_tranche_scale == 0.4
    assert all("追高" not in item for item in guarded[0].risks)
    assert any("接近20日高点未被单独视为追高" in item for item in guarded[0].validation_notes)
    assert any("今日资金出现同日回流" in item for item in guarded[0].points)


def test_probability_direction_and_fund_early_repair_open_reduced_probe():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate["sector_label"] = "云计算"
    candidate["fund_entry_signal"] = {
        "policy_version": "fund_entry_position.2026-08.v2",
        "status": "forming",
        "entry_ready": False,
        "early_probe_ready": True,
        "first_tranche_scale": 0.4,
        "invalidation_signals": ["20日修复率跌回40%以下"],
    }
    guarded, _caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="云计算",
                action="建议关注",
                suggested_amount_yuan=1000,
                confidence="中",
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "云计算",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "entry_state": "forming",
                    "trend_strength_score": 55.0,
                    "participation_score": 68.0,
                    "position_risk_score": 58.0,
                    "evidence_quality": "complete",
                    "trend_formation_probability": 68.0,
                    "probability_early_probe_eligible": True,
                    "waiting_reason_code": "probability_fund_confirmation",
                    "first_tranche_scale": 0.4,
                    "overheat_flags": [],
                    "entry_gate_inputs": {
                        "mainline_status": "forming",
                        "leading_flow_confirmed": True,
                    },
                }
            ],
        },
    )

    assert guarded[0].action == "分批买入"
    assert guarded[0].entry_path == "probability_early_probe"
    assert guarded[0].entry_tranche_scale == 0.4
    # 措辞改为「趋势成形信号分」：那个数是未校准的加权合成，叫「概率」等于对用户
    # 宣称一件系统无法兑现的事（中性方向就会读出约 56）。数值本身不变。
    assert any("趋势成形信号分已达到提前试仓线" in item for item in guarded[0].points)
    assert not any("形成概率" in item for item in guarded[0].points)
    assert any("提前试仓只开放" in item for item in guarded[0].validation_notes)


def test_existing_wait_action_explains_flow_confirmation_instead_of_price_pullback():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    guarded, _caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="等待回调",
                suggested_amount_yuan=1000,
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-08.v3",
                    "entry_state": "ready_on_pullback",
                    "trend_strength_score": 62.4,
                    "participation_score": 21.4,
                    "position_risk_score": 58.1,
                    "evidence_quality": "complete",
                    "waiting_reason_code": "flow_confirmation",
                    "overheat_flags": [],
                    "entry_triggers": ["主力资金与上涨广度转为改善"],
                    "entry_gate_inputs": {"mainline_status": "forming"},
                }
            ],
        },
    )

    assert guarded[0].action == "等待回调"
    assert guarded[0].waiting_reason_code == "flow_confirmation"
    assert any("等待资金条件" in item for item in guarded[0].points)
    assert all("价格需要回调" not in item for item in guarded[0].points)


def test_ready_direction_uses_passive_vehicle_quality_instead_of_sector_returns():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate.update(
        {
            "fund_quality_score": 41.33,
            "sector_fit_score": 34.0,
            "sector_match_kind": "tracking_exact",
            "vehicle_quality_score": 93.0,
            "vehicle_quality_threshold": 60.0,
            "vehicle_quality_status": "eligible",
            "vehicle_quality_method": "passive_index_vehicle",
            "nav_trend": {
                "recent_5d_change_percent": 2.0,
                "return_20d_percent": 8.0,
                "distance_from_high_percent": -5.0,
            },
        }
    )
    guarded, _caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="建议关注",
                suggested_amount_yuan=1000,
                confidence="中",
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-07.v2",
                    "entry_state": "ready_to_start",
                    "entry_readiness_score": 72.0,
                    "evidence_quality": "complete",
                    "confidence": "高",
                    "cumulative_5d_net_yi": 18.0,
                }
            ],
        },
    )

    assert guarded[0].action == "分批买入"
    assert all("基金质量分 41.33" not in point for point in guarded[0].points)


def test_ready_direction_does_not_promote_fund_below_vehicle_quality_gate():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    candidate.update(
        {
            "sector_fit_score": 34.0,
            "sector_match_kind": "tracking_exact",
            "vehicle_quality_score": 45.0,
            "vehicle_quality_threshold": 60.0,
            "vehicle_quality_status": "watch_only",
            "vehicle_quality_method": "passive_index_vehicle",
        }
    )
    guarded, _caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="建议关注",
                suggested_amount_yuan=1000,
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-07.v2",
                    "entry_state": "ready_to_start",
                    "entry_readiness_score": 72.0,
                    "evidence_quality": "complete",
                }
            ],
        },
    )

    assert guarded[0].action == "建议关注"
    assert guarded[0].suggested_amount_yuan is None


def test_entry_maturity_v2_keeps_extended_direction_in_conditional_wait():
    candidate = _eligible_guard_candidate(
        quality_gate={"status": "eligible", "eligible": True, "reasons": []}
    )
    guarded, _caveats, _ = _run_guard_for_test(
        [
            DiscoveryRecommendation(
                fund_code="020356",
                fund_name="守卫测试基金A",
                sector_name="半导体",
                action="分批买入",
                suggested_amount_yuan=1000,
                confidence="中",
            )
        ],
        candidate,
        extra_facts={
            "effective_configuration": {"discovery_strategy": "opportunity_first"},
            "sector_opportunities": [
                {
                    "sector_label": "半导体",
                    "score_policy_version": "sector_entry_maturity.2026-07.v2",
                    "entry_state": "ready_on_pullback",
                    "entry_readiness_score": 58.0,
                    "evidence_quality": "complete",
                    "confidence": "中",
                    "cumulative_5d_net_yi": 18.0,
                    "entry_triggers": ["单日涨幅回落至3%以内"],
                }
            ],
        },
    )

    assert guarded[0].action == "等待回调"
    assert guarded[0].suggested_amount_yuan is None
    assert any("单日涨幅回落至3%以内" in item for item in guarded[0].points)


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
    assert "PIT v3 量化模型尚未达到可执行条件" in guarded[0].points[0]


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
