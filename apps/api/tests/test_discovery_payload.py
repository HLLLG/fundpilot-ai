"""荐基 LLM user payload 与日报对齐的字段覆盖。"""

from app.models import InvestorProfile, NewsItem, TopicBrief, TopicBriefPoint
from app.services.discovery_payload import build_user_payload


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
        "sector_heat": [{"sector_label": "半导体", "heat_score": 0.9}],
        "target_sector_context": [
            {
                "sector_label": "半导体",
                "heat_score": 0.9,
                "sector_fund_flow": {"available": True, "today_main_force_net_yi": 1.2},
            }
        ],
        "market_flow": {"northbound_net_yi": 0, "southbound_net_yi": -8.87},
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
            }
        ],
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
    facts = payload["discovery_facts"]
    assert facts["session"]["session_kind"] == "trading_day_intraday"
    assert facts["target_sector_context"][0]["sector_fund_flow"]["available"] is True
    assert facts["market_flow"]["southbound_net_yi"] == -8.87
    assert facts["instruction"] == "系统数字只读"
    assert facts["candidate_pool"][0]["fund_code"] == "161725"
    joined = " ".join(payload["requirements"])
    assert "target_sector_context" in joined


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
    assert "target_sector_context" not in joined
