import pytest

from app.models import FundSnapshot, Holding, InvestorProfile, NewsItem
from app.services.analysis_facts import build_analysis_facts
from app.services.risk import evaluate_portfolio_risk


@pytest.fixture(autouse=True)
def _stub_signal_backtest(monkeypatch):
    monkeypatch.setattr(
        "app.services.analysis_facts.build_signal_backtest_context",
        lambda *_args, **_kwargs: {"enabled": True, "has_data": False, "summary_lines": []},
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.resolve_signal_guard_policy",
        lambda *_args, **_kwargs: {
            "enforce_reversal_block": True,
            "enforce_pullback_block": True,
            "tighten_tactical": False,
            "backtest_summary_lines": [],
        },
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.summarize_sector_intraday_for_holding",
        lambda *_args, **_kwargs: None,
    )


def test_build_analysis_facts_attaches_factor_scores():
    holdings = [Holding(fund_code="015608", fund_name="基金A", holding_amount=5000)]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)
    factor_scores = {
        "available": True,
        "universe_size": 300,
        "factor_reliability": {"momentum": {"level": "高", "basis": "回测显著正向"}},
        "holdings": [{"fund_code": "015608", "composite_grade": "A"}],
    }
    facts = build_analysis_facts(
        holdings, risk, [], profile, factor_scores=factor_scores
    )
    assert facts["factor_scores"]["available"] is True
    assert facts["factor_scores"]["factor_reliability"]["momentum"]["level"] == "高"
    assert "factor_reliability" in facts["instruction"]


def test_build_analysis_facts_attaches_risk_metrics():
    holdings = [Holding(fund_code="015608", fund_name="基金A", holding_amount=5000)]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)
    risk_metrics = {
        "available": True,
        "sample_days": 150,
        "sharpe_ratio": 1.2,
        "confidence": {"level": "高", "basis": "150 交易日样本，置信高"},
    }
    facts = build_analysis_facts(
        holdings, risk, [], profile, risk_metrics=risk_metrics
    )
    assert facts["risk_metrics"]["confidence"]["level"] == "高"
    assert "risk_metrics" in facts["instruction"]


def test_build_analysis_facts_marks_concentration():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=8000,
            return_percent=-2,
            sector_return_percent=1.2,
        ),
        Holding(
            fund_code="008114",
            fund_name="基金B",
            holding_amount=2000,
            return_percent=1,
        ),
    ]
    profile = InvestorProfile(concentration_limit_percent=35)
    risk = evaluate_portfolio_risk(holdings, profile)
    facts = build_analysis_facts(holdings, risk, [], profile)

    by_code = {item["fund_code"]: item for item in facts["holdings"]}
    assert by_code["015608"]["over_concentration"] is True
    assert by_code["015608"]["weight_percent"] == 80.0
    assert facts["portfolio"]["total_amount"] == 10000


def test_build_analysis_facts_includes_nav_trend():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=5000,
        ),
    ]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)
    nav_trends = {
        "015608": {
            "period_change_percent": 3.2,
            "trend_label": "温和上行",
            "recent_nav_series": [{"date": "2026-05-30", "nav": 1.05}],
        }
    }
    snapshots = [
        FundSnapshot(
            fund_code="015608",
            fund_name="基金A",
            latest_nav=1.05,
            source="akshare",
        )
    ]
    facts = build_analysis_facts(
        holdings, risk, snapshots, profile, nav_trends_by_code=nav_trends
    )

    assert facts["holdings"][0]["nav_trend"]["trend_label"] == "温和上行"
    assert facts["holdings"][0]["latest_nav"] == 1.05


def test_build_analysis_facts_includes_sector_fund_gap_for_llm():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=5000,
            sector_return_percent=4.0,
            daily_return_percent=1.5,
        ),
    ]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)
    facts = build_analysis_facts(holdings, risk, [], profile, for_llm=True)
    assert facts["holdings"][0]["sector_fund_gap_percent"] == 2.5


def test_build_analysis_facts_includes_signal_backtest(monkeypatch):
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=5000,
            sector_name="半导体",
        ),
    ]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)

    monkeypatch.setattr(
        "app.services.analysis_facts.build_signal_backtest_context",
        lambda *_args, **_kwargs: {
            "enabled": True,
            "has_data": True,
            "lookback_days": 120,
            "by_rule": {
                "reversal_down": {
                    "hit_rate_percent": 58.0,
                    "trigger_count": 12,
                    "label": "涨后回吐",
                }
            },
            "sectors": [
                {
                    "sector_label": "半导体",
                    "by_rule": {
                        "reversal_down": {"hit_rate_percent": 58.0, "trigger_count": 12}
                    },
                }
            ],
            "summary_lines": ["测试"],
        },
    )
    monkeypatch.setattr(
        "app.services.analysis_facts.resolve_signal_guard_policy",
        lambda *_args, **_kwargs: {
            "enforce_reversal_block": True,
            "enforce_pullback_block": True,
            "tighten_tactical": True,
            "reason": "测试策略",
            "backtest_summary_lines": ["测试"],
        },
    )

    facts = build_analysis_facts(holdings, risk, [], profile)
    assert facts["signal_backtest"]["has_data"] is True
    assert facts["guard_policy"]["enforce_reversal_block"] is True
    assert facts["holdings"][0]["signal_backtest"]["sector_label"] == "半导体"


def test_build_analysis_facts_includes_news_freshness_and_momentum():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=5000,
            sector_return_percent=3.5,
        ),
    ]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)
    nav_trends = {
        "015608": {
            "recent_nav_series": [
                {"date": "2026-06-09", "nav": 1.02},
                {"date": "2026-06-10", "nav": 1.008},
            ],
        }
    }
    news = [
        NewsItem(
            topic="半导体",
            title="半导体走强",
            published_at="2026-06-10 10:00:00",
            is_today=True,
        )
    ]
    facts = build_analysis_facts(
        holdings,
        risk,
        [],
        profile,
        nav_trends_by_code=nav_trends,
        market_news=news,
    )
    assert facts["news"]["today_items"] == 1
    assert facts["holdings"][0]["sector_momentum"] is not None


def test_build_analysis_facts_includes_sector_fund_flow(monkeypatch):
    holdings = [
        Holding(
            fund_code="519674",
            fund_name="基A",
            holding_amount=5000,
            sector_name="半导体",
            sector_return_percent=2.0,
        ),
    ]
    profile = InvestorProfile()
    risk = evaluate_portfolio_risk(holdings, profile)
    monkeypatch.setattr(
        "app.services.analysis_facts.build_sector_fund_flow_map",
        lambda _holdings: {
            "半导体": {
                "available": True,
                "board_code": "BK1036",
                "today_main_force_net_yi": -3.0,
                "pattern_label": "distribution",
                "pattern_hint": "测试",
            }
        },
    )
    facts = build_analysis_facts(holdings, risk, [], profile)
    flow = facts["holdings"][0]["sector_fund_flow"]
    assert flow["available"] is True
    assert flow["pattern_label"] == "distribution"


def test_build_analysis_facts_aligns_estimated_holding_return_with_ui():
    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=815.57,
            return_percent=-9.08,
            holding_return_percent=-9.08,
            holding_profit=-81.0,
            sector_return_percent=2.48,
            daily_return_percent=2.48,
            daily_return_percent_source="sector_estimate",
        )
    ]
    profile = InvestorProfile(max_drawdown_percent=8)
    risk = evaluate_portfolio_risk(holdings, profile)
    facts = build_analysis_facts(holdings, risk, [], profile)
    row = facts["holdings"][0]

    assert row["holding_return_percent"] == pytest.approx(-9.08)
    assert row["estimated_holding_return_percent"] == pytest.approx(-6.6, abs=0.05)
    assert row["holding_return_is_estimated"] is True
    assert row["over_drawdown_limit"] is False
    assert facts["portfolio"]["weighted_return_percent"] == pytest.approx(-6.6, abs=0.05)
