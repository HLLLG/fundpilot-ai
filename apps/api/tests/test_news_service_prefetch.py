"""F3 回归：NewsService.prefetch_topics 多主题并发拉取。"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from app.config import refresh_settings
from app.models import NewsItem
from app.services.news_service import NewsService


@pytest.fixture
def news_prefetch_enabled(monkeypatch):
    """CI 默认 FUND_AI_NEWS_ENABLED=false，预取单测需显式打开。"""
    monkeypatch.setenv("FUND_AI_NEWS_ENABLED", "true")
    refresh_settings()


def _make_item(topic: str, title: str, today: bool = True) -> NewsItem:
    return NewsItem(
        topic=topic,
        title=title,
        published_at="2026-06-25 10:00",
        source="test",
        is_today=today,
    )


def test_prefetch_topics_runs_topics_in_parallel(news_prefetch_enabled):
    """5 个主题每个 sleep 0.2s，并发执行总耗时应远小于串行 1s。"""
    service = NewsService()

    def slow_search(topic: str, limit: int | None = None):
        time.sleep(0.2)
        return [_make_item(topic, f"{topic} title")]

    topics = ["半导体", "商业航天", "新能源车", "医药", "银行"]

    with patch.object(service, "search", side_effect=slow_search):
        start = time.monotonic()
        result = service.prefetch_topics(topics)
        elapsed = time.monotonic() - start

    assert elapsed < 0.6, f"并发执行应远快于串行 1s，实际 {elapsed:.2f}s"
    titles = [item.title for item in result]
    assert set(titles) == {f"{t} title" for t in topics}


def test_prefetch_topics_dedupes_and_ranks_today_first(news_prefetch_enabled):
    """同主题内重复标题应 dedupe；当日新闻应排在前面。

    注意：_dedupe_news 的 key 是 `url or f"{topic}:{title}"`，所以不同主题相同
    标题不会去重——这是当前行为，本测试只验证同主题内的去重。
    """
    service = NewsService()

    def fake_search(topic, limit=None):
        if topic == "半导体":
            return [
                _make_item("半导体", "重复标题", today=False),
                _make_item("半导体", "重复标题", today=False),  # 同主题重复，必须 dedupe
                _make_item("半导体", "新标题", today=True),
            ]
        return [_make_item(topic, "其他主题标题", today=False)]

    with patch.object(service, "search", side_effect=fake_search):
        result = service.prefetch_topics(["半导体", "商业航天"])

    titles_in_order = [item.title for item in result]
    # 当日新闻应排在前面（_rank_news_by_recency）
    assert titles_in_order[0] == "新标题"
    # 同主题内"重复标题"经 _dedupe_news 仅保留一次
    assert titles_in_order.count("重复标题") == 1


def test_prefetch_topics_single_topic_skips_threadpool(news_prefetch_enabled):
    service = NewsService()
    invoked_thread_ids: list[int] = []
    main_ident = threading.get_ident()

    def fake_search(topic, limit=None):
        invoked_thread_ids.append(threading.get_ident())
        return [_make_item(topic, "x")]

    with patch.object(service, "search", side_effect=fake_search):
        result = service.prefetch_topics(["半导体"])

    assert invoked_thread_ids == [main_ident], "单主题应不起线程池，直接主线程跑"
    assert result[0].title == "x"


def test_prefetch_topics_disabled_returns_empty(monkeypatch):
    service = NewsService()
    monkeypatch.setattr(service.settings, "news_enabled", False)
    assert service.prefetch_topics(["半导体"]) == []


def test_prefetch_topics_respects_total_timeout(news_prefetch_enabled, monkeypatch):
    service = NewsService()
    monkeypatch.setattr(service.settings, "news_prefetch_total_timeout_seconds", 0.05)

    def slow_search(topic: str, limit: int | None = None):
        time.sleep(0.3)
        return [_make_item(topic, f"{topic} title")]

    topics = ["半导体", "商业航天", "新能源车", "医药", "银行"]
    with patch.object(service, "search", side_effect=slow_search):
        start = time.monotonic()
        result = service.prefetch_topics(topics)
        elapsed = time.monotonic() - start

    assert elapsed < 0.35, f"总超时应尽快返回，实际 {elapsed:.2f}s"
    assert len(result) <= len(topics)
