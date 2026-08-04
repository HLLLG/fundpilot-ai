from __future__ import annotations

from app.models import DiscoveryRequest, FundNavHistory, FundNavPoint, InvestorProfile
from app.services.discovery_strategy import (
    discovery_horizon_label,
    discovery_minimum_holding_days,
    strategy_from_facts,
)
from app.services.discovery_candidate_pool import build_candidate_pool, finalize_candidate_pool
from app.services.discovery_selection_strategy import (
    assess_fund_entry_position,
    current_opportunity_score,
    recall_upside_score,
)
from app.services.nav_trend_summary import summarize_nav_history


def test_new_discovery_requests_default_to_opportunity_first_without_changing_profile():
    profile = InvestorProfile(
        horizon="半年到一年",
        max_drawdown_percent=8,
        investment_preset="conservative_hold",
    )
    request = DiscoveryRequest(profile=profile)

    assert request.discovery_strategy == "opportunity_first"
    assert request.profile.max_drawdown_percent == 8
    assert request.profile.horizon == "半年到一年"
    assert discovery_horizon_label(request.discovery_strategy, profile) == "1-3个月"
    assert discovery_minimum_holding_days(request.discovery_strategy, profile) == 30


def test_reports_without_strategy_keep_legacy_risk_first_semantics():
    assert strategy_from_facts({"candidate_pool": []}) == "risk_first"


def test_nav_summary_exposes_full_20_and_60_day_opportunity_windows():
    navs = [100.0 + index for index in range(61)]
    navs[45] = 152.0
    navs[46] = 140.0
    history = FundNavHistory(
        fund_code="000001",
        fund_name="窗口基金",
        source="test",
        points=[
            FundNavPoint(date=f"2026-01-{index + 1:02d}", nav=nav)
            for index, nav in enumerate(navs)
        ],
    )

    summary = summarize_nav_history(history)

    assert summary is not None
    assert summary["return_20d_percent"] is not None
    assert summary["max_drawdown_20d_percent"] < 0
    assert summary["return_60d_percent"] == 60.0
    assert summary["max_drawdown_60d_percent"] < 0
    assert summary["annualized_volatility_20d_percent"] is not None
    assert 0 <= summary["drawdown_recovery_20d_percent"] <= 100


def test_opportunity_score_keeps_uncapped_upside_without_near_high_penalty():
    confirmed = current_opportunity_score(
        {
            "nav_trend": {
                "recent_5d_change_percent": 2.0,
                "return_20d_percent": 8.0,
                "return_60d_percent": 15.0,
                "max_drawdown_20d_percent": -4.0,
                "max_drawdown_60d_percent": -9.0,
                "distance_from_high_percent": -6.0,
            }
        }
    )
    extended = current_opportunity_score(
        {
            "nav_trend": {
                "recent_5d_change_percent": 6.0,
                "return_20d_percent": 18.0,
                "return_60d_percent": 30.0,
                "max_drawdown_20d_percent": -4.0,
                "max_drawdown_60d_percent": -9.0,
                "distance_from_high_percent": -1.0,
            }
        }
    )
    same_trend_with_room = current_opportunity_score(
        {
            "nav_trend": {
                "recent_5d_change_percent": 6.0,
                "return_20d_percent": 18.0,
                "return_60d_percent": 30.0,
                "max_drawdown_20d_percent": -4.0,
                "max_drawdown_60d_percent": -9.0,
                "distance_from_high_percent": -8.0,
            }
        }
    )

    assert confirmed is not None
    assert extended is not None
    assert same_trend_with_room is not None
    assert confirmed >= 0
    assert extended > confirmed
    assert extended == same_trend_with_room

    explosive = current_opportunity_score(
        {
            "nav_trend": {
                "recent_5d_change_percent": 14.0,
                "return_20d_percent": 65.0,
                "return_60d_percent": 140.0,
                "annualized_volatility_20d_percent": 72.0,
                "annualized_volatility_60d_percent": 65.0,
                "drawdown_recovery_20d_percent": 90.0,
                "rebound_from_20d_low_percent": 22.0,
            }
        }
    )
    assert explosive is not None and explosive > 100


def test_recall_upside_proxy_retains_high_elasticity_before_nav_enrichment():
    stable = {
        "return_3m_percent": 5.0,
        "return_6m_percent": 10.0,
        "return_1y_percent": 15.0,
        "max_drawdown_1y_percent": -8.0,
    }
    elastic = {
        "return_3m_percent": 12.0,
        "return_6m_percent": -4.0,
        "return_1y_percent": 8.0,
        "max_drawdown_1y_percent": -48.0,
    }

    assert recall_upside_score(elastic) > recall_upside_score(stable)


def test_opportunity_first_recall_selects_elastic_candidate_before_nav_enrichment(
    monkeypatch,
):
    common = {
        "fund_scale_yi": 8.0,
        "established_date": "2020-01-01",
        "nav_date": "2026-08-01",
    }
    rows = [
        {
            **common,
            "fund_code": "100001",
            "fund_name": "半导体稳健基金A",
            "return_3m_percent": 6.0,
            "return_6m_percent": 12.0,
            "return_1y_percent": 18.0,
            "max_drawdown_1y_percent": -8.0,
        },
        {
            **common,
            "fund_code": "100002",
            "fund_name": "半导体高弹性基金A",
            "return_3m_percent": 14.0,
            "return_6m_percent": -3.0,
            "return_1y_percent": 7.0,
            "max_drawdown_1y_percent": -48.0,
        },
    ]
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors_by_sector_names",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool._attach_descriptive_peer_research",
        lambda *_args, **_kwargs: None,
    )

    selected = build_candidate_pool(
        ["半导体"],
        discovery_strategy="opportunity_first",
        per_sector=1,
        pool_cap=1,
        fetch_rank=lambda limit: rows,
        fetch_new_funds=lambda limit: [],
    )

    assert selected[0]["fund_code"] == "100002"
    assert selected[0]["recall_upside_score"] > 0


def test_fund_entry_position_recognizes_repaired_pullback():
    signal = assess_fund_entry_position(
        {
            "nav_trend": {
                "recent_5d_change_percent": 4.5,
                "recent_5d_daily_change_percent": [0.8, 1.2, -0.2, 1.5, 1.0],
                "return_20d_percent": -3.0,
                "return_60d_percent": 18.0,
                "annualized_volatility_20d_percent": 38.0,
                "drawdown_recovery_20d_percent": 68.0,
                "rebound_from_20d_low_percent": 9.0,
            }
        }
    )

    assert signal["status"] == "recovery_ready"
    assert signal["entry_ready"] is True
    assert signal["high_elasticity"] is True
    assert signal["invalidation_signals"]


def test_opportunity_first_final_pool_prefers_current_setup_over_higher_long_term_quality():
    rows = [
        {
            "fund_code": "000001",
            "fund_name": "长期高分基金A",
            "sector_label": "半导体",
            "quality_gate": {"status": "eligible", "eligible": True},
            "fund_quality_score": 92.0,
            "sector_fit_score": 38.0,
            "opportunity_score_20_60d": 54.0,
        },
        {
            "fund_code": "000002",
            "fund_name": "当前机会基金A",
            "sector_label": "半导体",
            "quality_gate": {"status": "eligible", "eligible": True},
            "fund_quality_score": 78.0,
            "sector_fit_score": 36.0,
            "opportunity_score_20_60d": 81.0,
        },
    ]

    selected = finalize_candidate_pool(
        rows,
        ["半导体"],
        per_sector=1,
        pool_cap=1,
        discovery_strategy="opportunity_first",
    )

    assert [item["fund_code"] for item in selected] == ["000002"]
