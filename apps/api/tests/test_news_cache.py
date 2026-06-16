from app.config import get_settings
from app.models import NewsItem
from app.services.news_cache import get_cached_news, save_cached_news


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
