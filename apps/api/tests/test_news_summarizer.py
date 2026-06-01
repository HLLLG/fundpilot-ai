import json
from pathlib import Path

from app.config import refresh_settings
from app.models import NewsItem
from app.services.news_summarizer import (
    build_topic_briefs_offline,
    group_news_by_topic,
    summarize_all_topics,
    summarize_topic,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_group_news_by_topic():
    items = [
        NewsItem(topic="半导体", title="A"),
        NewsItem(topic="半导体", title="B"),
        NewsItem(topic="人工智能", title="C"),
    ]
    grouped = group_news_by_topic(items)
    assert len(grouped["半导体"]) == 2
    assert len(grouped["人工智能"]) == 1


def test_build_topic_briefs_offline():
    items = [
        NewsItem(topic="半导体", title="半导体走弱", is_today=True),
        NewsItem(topic="半导体", title="设备商获订单"),
    ]
    brief = build_topic_briefs_offline("半导体", items)
    assert brief.provider == "rule-fallback"
    assert brief.news_count == 2
    assert brief.points


def test_summarize_topic_uses_flash_response(monkeypatch):
    from app.models import TopicBrief, TopicBriefPoint
    from app.services import news_summarizer

    payload = json.loads(
        (FIXTURES / "news_summarizer_flash_response.json").read_text(encoding="utf-8")
    )

    def fake_flash(topic: str, items, settings):
        return TopicBrief(
            topic=topic,
            summary=payload["summary"],
            points=[
                TopicBriefPoint(
                    headline=point["headline"],
                    sentiment=point["sentiment"],
                    is_today=point.get("is_today", False),
                    source_titles=point["source_titles"],
                )
                for point in payload["points"]
            ],
            news_count=len(items),
            provider="deepseek-v4-flash",
        )

    monkeypatch.setattr(news_summarizer, "_summarize_topic_with_flash", fake_flash)
    monkeypatch.setenv("FUND_AI_NEWS_SUMMARIZE", "true")
    monkeypatch.setenv("FUND_AI_DEEPSEEK_API_KEY", "k" * 32)
    refresh_settings()

    items = [NewsItem(topic="半导体", title="半导体板块午后走弱", is_today=True)]
    brief = summarize_topic("半导体", items)
    assert brief.provider == "deepseek-v4-flash"
    assert brief.points[0].sentiment == "bearish"
    assert "半导体板块午后走弱" in brief.points[0].source_titles


def test_summarize_all_topics_offline_when_no_key(monkeypatch):
    monkeypatch.delenv("FUND_AI_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("FUND_AI_NEWS_SUMMARIZE", "true")
    refresh_settings()

    def force_offline(topic: str, items, settings=None):
        return build_topic_briefs_offline(topic, items)

    monkeypatch.setattr(
        "app.services.news_summarizer.summarize_topic",
        force_offline,
    )
    items = [NewsItem(topic="半导体", title="测试新闻")]
    briefs = summarize_all_topics(items)
    assert len(briefs) == 1
    assert briefs[0].provider == "rule-fallback"
