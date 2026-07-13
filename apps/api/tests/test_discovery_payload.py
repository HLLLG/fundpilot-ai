"""荐基 LLM user payload 与日报对齐的字段覆盖。"""

from unittest.mock import patch

from app.models import Holding, InvestorProfile, NewsItem, TopicBrief, TopicBriefPoint
from app.services.analysis_payload import OUTPUT_REQUIREMENTS_SYSTEM, OUTPUT_REQUIREMENTS_USER
from app.services.discovery_facts import build_discovery_facts
from app.services.discovery_payload import (
    OUTPUT_DISCOVERY_REQUIREMENTS,
    _requirements_for_scan_mode,
    build_user_payload,
)
from app.services.discovery_prompt import DISCOVERY_FACTS_INSTRUCTION


def _profile() -> InvestorProfile:
    return InvestorProfile(
        decision_style="conservative",
        max_drawdown_percent=15,
        concentration_limit_percent=30,
        expected_investment_amount=100000,
    )


def _discovery_facts() -> dict:
    return {
        "readonly": True,
        "instruction": "系统数字只读",
        "session": {"session_kind": "trading_day_intraday", "effective_trade_date": "2026-06-25"},
        "profile": {"decision_style": "conservative"},
        "portfolio_gap": {"holding_count": 1, "available_budget_yuan": 50000},
        "portfolio_position_truth": {
            "schema_version": "portfolio_position_truth.compact.v1",
            "position_complete": True,
            "ledger_truncated": False,
            "pending_transaction_count": 0,
            "conflict_count": 0,
            "cash": {"balance_yuan": "1234.56", "known": True},
            "positions": [],
        },
        "sector_heat": [{"sector_label": "半导体", "heat_score": 0.9}],
        "target_sector_context": [
            {
                "sector_label": "半导体",
                "heat_score": 0.9,
                "sector_fund_flow": {"available": True, "today_main_force_net_yi": 1.2},
            }
        ],
        "stock_connect_flow": {
            "southbound_net_yi": -8.87,
        },
        "signal_backtest": {"available": True, "sectors": []},
        "news": {"freshness_label": "偏旧", "topic_count": 2},
        "candidate_factor_scores": {"available": False},
        "selection_strategy": "balanced",
        "candidate_pool": [
            {
                "fund_code": "161725",
                "fund_name": "招商中证白酒",
                "sector_label": "白酒",
                "return_1y_percent": 5.0,
                "return_3m_percent": 2.0,
                "return_6m_percent": 3.5,
                "max_drawdown_1y_percent": -10.0,
                "fund_scale_yi": 50.0,
                "fund_quality_score": 82.5,
                "sector_fit_score": 36.0,
                "quality_reasons": ["板块高置信匹配", "近3/6月表现占优"],
                "quality_penalties": [],
                "nav_trend": {
                    "trend_label": "震荡",
                    "recent_5d_change_percent": 1.0,
                    "period_change_percent": 6.0,
                    "latest_nav": 1.2,
                },
            }
        ],
        "fund_type_preference": "etf_link",
    }


def test_build_user_payload_includes_daily_report_parity_fields():
    news = [
        NewsItem(topic="半导体", title="芯片 ETF 大涨", url="https://example.com/1", source="test", is_today=True),
    ]
    briefs = [
        TopicBrief(
            topic="半导体",
            summary="AI 主线",
            points=[
                TopicBriefPoint(
                    headline="AI 主线延续",
                    sentiment="bullish",
                    is_today=True,
                    source_titles=["芯片 ETF 大涨"],
                )
            ],
        )
    ]
    with patch(
        "app.services.discovery_candidate_llm.get_cached_official_nav_return",
        return_value=1.1,
    ):
        payload = build_user_payload(
            discovery_facts=_discovery_facts(),
            profile=_profile(),
            focus_sectors=["半导体"],
            scan_mode="full_market",
            market_news=news,
            topic_briefs=briefs,
            analysis_mode="deep",
        )

    assert payload["news_titles"]
    assert payload["topic_briefs"]
    assert payload["fund_type_preference"] == "etf_link"
    facts = payload["discovery_facts"]
    assert facts["session"]["session_kind"] == "trading_day_intraday"
    assert facts["target_sector_context"][0]["sector_fund_flow"]["available"] is True
    assert facts["stock_connect_flow"]["southbound_net_yi"] == -8.87
    assert "northbound_net_yi" not in facts["stock_connect_flow"]
    assert facts["instruction"] == "系统数字只读"
    assert facts["portfolio_position_truth"]["cash"]["balance_yuan"] == "1234.56"
    candidate = facts["candidate_pool"][0]
    assert candidate["fund_code"] == "161725"
    assert candidate["return_3m_percent"] == 2.0
    assert candidate["fund_quality_score"] == 82.5
    assert candidate["sector_fit_score"] == 36.0
    assert candidate["quality_reasons"] == ["板块高置信匹配", "近3/6月表现占优"]
    assert candidate["nav_trend"]["trend_label"] == "震荡"
    assert "latest_nav" not in candidate["nav_trend"]
    assert candidate["estimated_daily_return_percent"] == 1.1
    assert candidate["daily_return_source"] == "official_nav"
    assert len(facts["sector_heat"]) <= 15
    joined = " ".join(payload["requirements"])
    assert "target_sector_context" in joined
    assert "holdings_slim" in joined


def test_daily_and_discovery_prompts_fail_closed_on_unknown_position_truth() -> None:
    daily_prompt = OUTPUT_REQUIREMENTS_SYSTEM + " ".join(OUTPUT_REQUIREMENTS_USER)
    discovery_prompt = OUTPUT_DISCOVERY_REQUIREMENTS + " ".join(
        _requirements_for_scan_mode("full_market")
    )

    for prompt in (daily_prompt, discovery_prompt):
        assert "portfolio_position_truth" in prompt
        assert "unknown/null" in prompt
        assert "pending/conflict" in prompt
    assert "amount_yuan" in daily_prompt
    assert "suggested_amount_yuan" in discovery_prompt


def test_build_user_payload_includes_sector_opportunities():
    facts = _discovery_facts()
    facts["sector_opportunities"] = [
        {
            "sector_label": "半导体",
            "track": "momentum",
            "score": 81.5,
            "entry_hint": "可分批关注",
            "evidence": ["价涨资金配合"],
            "penalties": [],
            "position_context": {
                "position_label": "pullback_acceptance",
                "drawdown_from_20d_high_percent": 4.17,
                "distance_from_20d_high_percent": -4.17,
                "distance_from_20d_low_percent": 15.0,
                "volume_ratio_5d_vs_20d": 1.18,
                "up_days_5d": 2,
                "down_days_5d": 3,
            },
        }
    ]
    payload = build_user_payload(
        discovery_facts=facts,
        profile=_profile(),
        focus_sectors=["半导体"],
    )
    opportunities = payload["discovery_facts"]["sector_opportunities"]
    assert opportunities[0]["sector_label"] == "半导体"
    assert opportunities[0]["track"] == "momentum"
    assert opportunities[0]["entry_hint"] == "可分批关注"
    assert "position_label" not in opportunities[0]
    assert "drawdown_from_20d_high_percent" not in opportunities[0]
    assert "volume_ratio_5d_vs_20d" not in opportunities[0]


def test_build_user_payload_fast_mode_uses_minimal_briefs():
    briefs = [
        TopicBrief(
            topic="半导体",
            summary="AI 主线",
            points=[
                TopicBriefPoint(
                    headline="AI 主线延续",
                    sentiment="bullish",
                    is_today=True,
                    source_titles=["标题A"],
                    source_urls=["https://example.com/a"],
                )
            ],
        )
    ]
    payload = build_user_payload(
        discovery_facts=_discovery_facts(),
        profile=_profile(),
        focus_sectors=[],
        market_news=[],
        topic_briefs=briefs,
        analysis_mode="fast",
    )
    point = payload["topic_briefs"][0]["points"][0]
    assert "source_urls" not in point


def test_gap_scan_mode_requirements():
    payload = build_user_payload(
        discovery_facts=_discovery_facts(),
        profile=_profile(),
        focus_sectors=["白酒"],
        scan_mode="gap",
    )
    joined = " ".join(payload["requirements"])
    assert "portfolio_gap" in joined
    assert "holdings_slim" in joined
    assert "target_sector_context" not in joined


def test_build_discovery_facts_includes_holdings_slim():
    holdings = [
        Holding(
            fund_code="110011",
            fund_name="易方达中小盘",
            sector_name="消费",
            holding_amount=30000,
            return_percent=5.0,
            daily_return_percent=0.5,
            daily_return_percent_source="sector_estimate",
        )
    ]
    with patch(
        "app.services.discovery_facts.get_cached_official_nav_return",
        return_value=1.2,
    ):
        facts = build_discovery_facts(
            holdings=holdings,
            profile=_profile(),
            target_sectors=["消费"],
            sector_heat=[{"sector_label": "消费", "heat_score": 0.8, "change_1d_percent": 1.0}],
            candidate_pool=[],
            fund_type_preference="no_c_class",
        )
    slim = facts["portfolio_gap"]["holdings_slim"]
    assert len(slim) == 1
    assert slim[0]["fund_code"] == "110011"
    assert slim[0]["weight_percent"] > 0
    assert slim[0]["estimated_daily_return_percent"] == 1.2
    assert facts["fund_type_preference"] == "no_c_class"
    assert facts["instruction"] == DISCOVERY_FACTS_INSTRUCTION


def test_build_discovery_facts_budget_degrades_slow_signal(monkeypatch):
    import time

    monkeypatch.setattr("app.services.discovery_facts.SIGNAL_BACKTEST_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr("app.services.discovery_facts.TARGET_SECTOR_CONTEXT_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        "app.services.discovery_facts.STOCK_CONNECT_FLOW_TIMEOUT_SECONDS",
        0.01,
    )

    def slow_signal(*_args, **_kwargs):
        time.sleep(0.08)
        return {"has_data": True}

    monkeypatch.setattr("app.services.discovery_facts.build_signal_backtest_context", slow_signal)

    start = time.monotonic()
    facts = build_discovery_facts(
        holdings=[],
        profile=_profile(),
        target_sectors=["半导体"],
        sector_heat=[],
        candidate_pool=[],
        budget_enhancements=True,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.12
    assert facts["signal_backtest"]["has_data"] is False
    assert facts["signal_backtest"]["reason"] == "timeout"


def test_discovery_requirements_limit_news_to_system_prefetch():
    payload = build_user_payload(
        discovery_facts=_discovery_facts(),
        profile=_profile(),
        focus_sectors=["半导体"],
        scan_mode="full_market",
    )
    joined = " ".join(payload["requirements"])
    assert "系统预取" in joined
    assert "过旧" in joined


def test_portfolio_gap_scan_mode_alias():
    reqs = _requirements_for_scan_mode("gap")
    joined = " ".join(reqs)
    assert "holdings_slim" in joined
    assert "target_sector_context" not in joined
