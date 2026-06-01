from app.models import FundRecommendation, NewsItem, TopicBrief, TopicBriefPoint
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


def test_accepts_headline_from_topic_brief_source_titles():
    news = [NewsItem(topic="半导体", title="半导体板块午后拉升", is_today=True)]
    briefs = [
        TopicBrief(
            topic="半导体",
            summary="板块走弱",
            points=[
                TopicBriefPoint(
                    headline="午后走弱",
                    sentiment="bearish",
                    source_titles=["半导体板块午后拉升"],
                )
            ],
        )
    ]
    rec = FundRecommendation(
        fund_code="015608",
        fund_name="测试",
        action="观察",
        news_bearish=["半导体板块午后拉升（半导体）"],
    )
    guarded = apply_news_citation_guards([rec], news, briefs)[0]
    assert "半导体板块午后拉升" in guarded.news_bearish[0]
