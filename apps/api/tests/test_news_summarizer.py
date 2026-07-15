from __future__ import annotations

import time

from app.config import get_settings
from app.models import NewsItem
from app.services.news_summarizer import (
    _parse_topic_brief_response,
    build_topic_briefs_offline,
    summarize_all_topics,
)


def _item(topic: str, title: str) -> NewsItem:
    return NewsItem(
        topic=topic,
        title=title,
        published_at="2026-06-25 10:00",
        source="test",
        is_today=True,
    )


def test_summarize_all_topics_total_timeout_falls_back_without_waiting_for_blocked_workers(
    monkeypatch,
):
    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "sk-" + "a" * 32)
    monkeypatch.setattr(settings, "news_summarize", True)
    monkeypatch.setattr(settings, "news_summarize_timeout_seconds", 0.05)
    items = [
        _item("半导体", "半导体新闻"),
        _item("白酒", "白酒新闻"),
        _item("商业航天", "商业航天新闻"),
        _item("人工智能", "人工智能新闻"),
    ]

    def blocked_summary(topic, group_items, resolved):
        time.sleep(1.0)
        return build_topic_briefs_offline(topic, group_items)

    monkeypatch.setattr(
        "app.services.news_summarizer._summarize_topic_with_flash",
        blocked_summary,
    )
    start = time.monotonic()
    briefs = summarize_all_topics(items, settings)
    elapsed = time.monotonic() - start

    assert elapsed < 0.3, f"摘要总超时应快速降级，实际 {elapsed:.2f}s"
    assert {brief.topic for brief in briefs} == {"半导体", "白酒", "商业航天", "人工智能"}
    assert all(brief.provider == "rule-fallback" for brief in briefs)


def test_topic_brief_today_flag_is_derived_from_validated_sources() -> None:
    settings = get_settings()
    items = [
        NewsItem(
            topic="chip",
            title="stale source",
            published_at="2026-07-12",
            source="test",
            is_today=False,
        ),
        NewsItem(
            topic="chip",
            title="today source",
            published_at="2026-07-13 10:00",
            source="test",
            is_today=True,
        ),
    ]
    parsed = {
        "summary": "source-backed brief",
        "points": [
            {
                "headline": "model promotes stale source",
                "sentiment": "bullish",
                "is_today": True,
                "source_titles": ["stale source"],
            },
            {
                "headline": "model demotes today's source",
                "sentiment": "neutral",
                "is_today": "false",
                "source_titles": ["today source"],
            },
        ],
    }

    brief = _parse_topic_brief_response("chip", items, parsed, settings)

    assert [point.is_today for point in brief.points] == [False, True]
