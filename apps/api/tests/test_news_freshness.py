from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import NewsItem
from app.services.news_freshness import build_news_pipeline_context

CN_TZ = ZoneInfo("Asia/Shanghai")


def test_build_news_pipeline_context_fresh_today():
    now = datetime.now(CN_TZ)
    recent = now.strftime("%Y-%m-%d %H:%M:%S")
    items = [
        NewsItem(topic="半导体", title="半导体走强", published_at=recent, is_today=True),
        NewsItem(topic="半导体", title="旧闻", published_at="2026-06-08 10:00:00", is_today=False),
    ]
    ctx = build_news_pipeline_context(items)
    assert ctx["today_items"] == 1
    assert ctx["freshness_label"] == "fresh"
    assert ctx["has_today_signal"] is True


def test_build_news_pipeline_context_stale_when_no_today():
    items = [
        NewsItem(topic="白酒", title="旧闻", published_at="2026-06-08 10:00:00", is_today=False),
    ]
    ctx = build_news_pipeline_context(items)
    assert ctx["freshness_label"] == "stale"
    assert ctx["has_today_signal"] is False
