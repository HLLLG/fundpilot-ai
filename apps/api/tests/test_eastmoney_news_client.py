from __future__ import annotations

import subprocess
from unittest.mock import patch

from app.services.eastmoney_news_client import fetch_stock_news_em
from app.services.news_summarizer import summarize_all_topics
from app.models import NewsItem


def test_fetch_stock_news_em_returns_empty_on_timeout():
    with patch(
        "app.services.eastmoney_news_client.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="python", timeout=20),
    ):
        assert fetch_stock_news_em("半导体", limit=5) == []


def test_summarize_all_topics_offline_only_skips_llm():
    items = [
        NewsItem(
            topic="半导体",
            title="芯片板块走强",
            published_at="2026-06-26 10:00",
            source="test",
            is_today=True,
        )
    ]
    briefs = summarize_all_topics(items, offline_only=True)
    assert len(briefs) == 1
    assert briefs[0].provider == "rule-fallback"
