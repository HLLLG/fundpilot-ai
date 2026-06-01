from app.config import refresh_settings
from app.models import Holding
from app.services.news_service import NewsService


def test_topics_from_holdings_includes_macro_when_enabled(monkeypatch):
    monkeypatch.setenv("FUND_AI_NEWS_SOURCES", "eastmoney,macro")
    monkeypatch.setenv("FUND_AI_NEWS_MACRO_TOPIC", "上证指数")
    refresh_settings()

    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=1000,
            sector_name="中证人工智能",
        )
    ]
    topics = NewsService().topics_from_holdings(holdings, max_topics=5)
    assert topics[0] == "上证指数"


def test_topics_from_holdings_skips_macro_when_disabled(monkeypatch):
    monkeypatch.setenv("FUND_AI_NEWS_SOURCES", "eastmoney,announcement")
    refresh_settings()

    holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=1000,
            sector_name="中证人工智能",
        )
    ]
    topics = NewsService().topics_from_holdings(holdings, max_topics=5)
    assert "上证指数" not in topics
