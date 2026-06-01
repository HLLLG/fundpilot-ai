from app.models import FundRecommendation, NewsItem
from app.services.news_citation import apply_news_citation_guards


def test_strips_uncited_headlines_and_uses_placeholder():
    news = [
        NewsItem(topic="半导体", title="半导体板块午后拉升", is_today=True),
    ]
    rec = FundRecommendation(
        fund_code="015608",
        fund_name="测试",
        action="观察",
        news_bullish=["完全虚构的新闻标题"],
        news_bearish=["另一条假新闻"],
    )

    guarded = apply_news_citation_guards([rec], news)[0]

    assert guarded.news_bullish == ["暂无明确利好"]
    assert guarded.news_bearish == ["暂无明确利空"]


def test_keeps_matching_headline():
    title = "半导体板块午后拉升"
    news = [NewsItem(topic="半导体", title=title, is_today=True)]
    rec = FundRecommendation(
        fund_code="015608",
        fund_name="测试",
        action="观察",
        news_bullish=[f"{title}（2026-06-01）"],
        news_bearish=[],
    )

    guarded = apply_news_citation_guards([rec], news)[0]

    assert guarded.news_bullish[0].startswith(title)
