import json

from app.models import (
    AnalysisRequest,
    Holding,
    InvestorProfile,
    NewsItem,
    RiskAssessment,
    TopicBrief,
    TopicBriefPoint,
)
from app.services.analysis_payload import (
    build_user_payload,
    compact_news_titles,
    trim_analysis_facts_for_llm,
)
from app.services.risk import evaluate_portfolio_risk


def test_compact_news_titles_strips_snippets():
    items = [
        NewsItem(
            topic="半导体",
            title="半导体走强",
            snippet="很长" * 50,
            is_today=True,
            published_at="2026-06-10 10:00:00",
            source="eastmoney",
        ),
        NewsItem(
            topic="半导体",
            title="旧闻",
            is_today=False,
        ),
    ]
    compact = compact_news_titles(items, min_items=1)
    assert len(compact) == 1
    assert compact[0]["title"] == "半导体走强"
    assert compact[0]["is_today"] is True
    assert "snippet" not in compact[0]


def test_compact_news_titles_backfills_when_few_today_items():
    items = [
        NewsItem(topic="A", title=f"今日{i}", is_today=True)
        for i in range(3)
    ] + [
        NewsItem(topic="B", title=f"昨日{i}", is_today=False)
        for i in range(10)
    ]
    compact = compact_news_titles(items, min_items=8, max_items=12)
    assert len(compact) == 8
    assert sum(1 for row in compact if row["is_today"]) == 3


def test_compact_news_titles_merges_brief_source_titles():
    news = [
        NewsItem(topic="半导体", title="盘中新闻", is_today=True),
    ]
    briefs = [
        TopicBrief(
            topic="半导体",
            summary="摘要",
            points=[
                TopicBriefPoint(
                    headline="h",
                    sentiment="bullish",
                    is_today=False,
                    source_titles=["昨日专题标题"],
                )
            ],
            news_count=1,
            provider="test",
        )
    ]
    compact = compact_news_titles(news, briefs, min_items=1)
    titles = {row["title"] for row in compact}
    assert "盘中新闻" in titles
    assert "昨日专题标题" in titles
    assert any(row.get("from_brief") for row in compact)


def test_build_user_payload_omits_duplicate_top_level_blocks():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=5000,
            sector_return_percent=2.0,
            daily_return_percent=1.5,
        ),
    ]
    profile = InvestorProfile()
    request = AnalysisRequest(holdings=holdings, profile=profile)
    risk = evaluate_portfolio_risk(holdings, profile)
    payload = build_user_payload(request, risk, [], [], analysis_mode="deep")

    assert "holdings" not in payload
    assert "risk" not in payload
    assert "fund_snapshots" not in payload
    assert "ocr_text" not in payload
    assert "prefetched_news" not in payload
    assert "analysis_session" not in payload
    assert "holding_return_semantics" in payload
    assert "news_titles" in payload
    assert "analysis_facts" in payload
    assert len(payload["requirements"]) == 6
    assert payload["profile"]["decision_style"] == "conservative"
    assert "style" not in payload["profile"]


def test_trim_conservative_removes_tactical_blocks():
    facts = {
        "holdings": [
            {
                "fund_code": "015608",
                "sector_intraday": {"pattern_label": "x", "point_count": 99},
                "signal_backtest": {"sector_label": "半导体"},
                "management_fee": "1.2%",
            }
        ],
        "market_flow": {"available": True},
        "signal_backtest": {"has_data": True},
        "news": {"freshness_label": "fresh", "topics": [{"topic": "半导体"}]},
    }
    trimmed = trim_analysis_facts_for_llm(
        facts,
        analysis_mode="deep",
        decision_style="conservative",
        phase=3,
    )
    holding = trimmed["holdings"][0]
    assert "sector_intraday" in holding
    assert holding["sector_intraday"]["pattern_label"] == "x"
    assert "point_count" not in holding["sector_intraday"]
    assert "signal_backtest" not in holding
    assert "management_fee" not in holding
    assert "market_flow" not in trimmed
    assert "signal_backtest" not in trimmed
    assert "topics" not in trimmed["news"]


def test_build_user_payload_includes_sector_fund_gap():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=5000,
            sector_return_percent=3.0,
            daily_return_percent=1.0,
        ),
    ]
    profile = InvestorProfile()
    request = AnalysisRequest(holdings=holdings, profile=profile)
    risk = evaluate_portfolio_risk(holdings, profile)
    payload = build_user_payload(request, risk, [], [], analysis_mode="fast")
    gap = payload["analysis_facts"]["holdings"][0]["sector_fund_gap_percent"]
    assert gap == 2.0


def test_payload_size_smaller_than_legacy_shape():
    holdings = [
        Holding(
            fund_code="015608",
            fund_name="基金A",
            holding_amount=5000,
            sector_return_percent=2.0,
        ),
    ]
    news = [
        NewsItem(
            topic="半导体",
            title=f"新闻{i}",
            snippet="x" * 200,
            is_today=True,
        )
        for i in range(10)
    ]
    profile = InvestorProfile()
    request = AnalysisRequest(holdings=holdings, profile=profile, ocr_text="ocr blob")
    risk = evaluate_portfolio_risk(holdings, profile)

    slim = build_user_payload(request, risk, [], news, analysis_mode="deep")
    legacy_size = len(
        json.dumps(
            {
                "holdings": [h.model_dump() for h in holdings],
                "risk": risk.model_dump(),
                "prefetched_news": [n.model_dump() for n in news],
                "ocr_text": request.ocr_text,
                "requirements": ["x"] * 20,
            },
            ensure_ascii=False,
        )
    )
    slim_size = len(json.dumps(slim, ensure_ascii=False))
    assert slim_size < legacy_size
