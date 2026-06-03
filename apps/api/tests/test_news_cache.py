import json
from pathlib import Path

import pytest

from app.models import NewsItem
from app.services.news_cache import get_cached_news, save_cached_news
from app.services.news_service import NewsService


def test_news_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    items = [
        NewsItem(topic="半导体", title="缓存新闻", published_at="2026-06-02", source="eastmoney"),
    ]
    save_cached_news("半导体", items)
    cached = get_cached_news("半导体")
    assert cached is not None
    assert cached[0].title == "缓存新闻"


def test_news_service_uses_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    calls = {"count": 0}

    def fake_from_eastmoney(self, topic: str, limit: int):
        calls["count"] += 1
        return [
            NewsItem(topic=topic, title="实时新闻", published_at="2026-06-02", source="eastmoney"),
        ]

    monkeypatch.setattr(NewsService, "_from_eastmoney", fake_from_eastmoney)
    monkeypatch.setattr(NewsService, "_from_fund_announcements", lambda *args, **kwargs: [])

    service = NewsService()
    first = service.search("半导体", limit=3)
    second = service.search("半导体", limit=3)

    assert first[0].title == "实时新闻"
    assert second[0].title == "实时新闻"
    assert calls["count"] == 1
