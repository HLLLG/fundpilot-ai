from __future__ import annotations

from types import SimpleNamespace

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_candidate_pool import _with_data_quality_gate, enrich_candidates
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
    assert item["quality_score_version"] == "fund_quality.v2"
    assert item["quality_gate"]["status"] == "eligible"
    assert item["quality_gate"]["coverage_percent"] == 100.0


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
