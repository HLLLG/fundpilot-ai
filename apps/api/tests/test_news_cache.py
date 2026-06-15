from app.config import get_settings
from app.models import NewsItem
from app.services.news_cache import get_cached_news, save_cached_news
from app.services.news_service import NewsService


def _use_tmp_db(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    get_settings.cache_clear()


def test_news_cache_roundtrip(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    items = [
        NewsItem(topic="半导体", title="缓存新闻", published_at="2026-06-02", source="eastmoney"),
    ]
    save_cached_news("半导体", items)
    cached = get_cached_news("半导体")
    assert cached is not None
    assert cached[0].title == "缓存新闻"


def test_news_service_uses_cache(tmp_path, monkeypatch):
    _use_tmp_db(tmp_path, monkeypatch)
    calls = {"count": 0}

    def fake_from_eastmoney(self, topic: str, limit: int):
        calls["count"] += 1
        return [
            NewsItem(topic=topic, title="实时新闻", published_at="2026-06-02", source="eastmoney"),
        ]

    monkeypatch.setattr(NewsService, "_from_eastmoney", fake_from_eastmoney)
    monkeypatch.setattr(NewsService, "_from_fund_announcements", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "app.services.news_service._news_cache_max_age_seconds",
        lambda: None,
    )

    service = NewsService()
    topic = "半导体-service-cache"
    first = service.search(topic, limit=3)
    second = service.search(topic, limit=3)

    assert first[0].title == "实时新闻"
    assert second[0].title == "实时新闻"
    assert calls["count"] == 1
