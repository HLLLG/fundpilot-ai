from datetime import datetime, timezone

from app.config import get_settings
from app.models import NewsItem
from app.services.news_cache import NEWS_CACHE_STALE_SECONDS, get_cached_news, save_cached_news


def test_news_cache_expires_when_max_age_exceeded(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    get_settings.cache_clear()
    items = [
        NewsItem(topic="半导体", title="缓存新闻", published_at="2026-06-02", source="eastmoney"),
    ]
    save_cached_news("半导体", items)

    from app.database import _connect
    from app.services import news_cache

    with _connect() as connection:
        news_cache._ensure_cache_table(connection)
        stale_time = datetime.now(timezone.utc).timestamp() - NEWS_CACHE_STALE_SECONDS - 60
        stale_iso = datetime.fromtimestamp(stale_time, timezone.utc).isoformat()
        connection.execute(
            "UPDATE news_cache SET updated_at = ? WHERE cache_key LIKE ?",
            (stale_iso, "半导体:%"),
        )
        connection.commit()

    assert get_cached_news("半导体", max_age_seconds=NEWS_CACHE_STALE_SECONDS) is None
    assert get_cached_news("半导体") is not None
