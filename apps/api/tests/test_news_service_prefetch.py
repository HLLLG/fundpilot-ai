"""F3 回归：NewsService.prefetch_topics 多主题并发拉取。"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

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
        published_at=("2026-07-13 10:00" if today else "2026-07-12 10:00"),
        source="test",
        is_today=today,
    )


def test_prefetch_topics_runs_topics_in_parallel(news_prefetch_enabled):
    """5 个主题每个 sleep 0.2s，并发执行总耗时应远小于串行 1s。"""
    service = NewsService()

    def slow_search(topic: str, limit: int | None = None, *, now=None):
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
    """重复新闻应跨主题 dedupe；上海当日新闻应排在前面。"""
    service = NewsService()

    def fake_search(topic, limit=None, *, now=None):
        if topic == "半导体":
            return [
                _make_item("半导体", "重复标题", today=False),
                _make_item("半导体", "重复标题", today=False),  # 同主题重复，必须 dedupe
                _make_item("半导体", "新标题", today=True),
            ]
        return [_make_item(topic, "其他主题标题", today=False)]

    with patch.object(service, "search", side_effect=fake_search):
        result = service.prefetch_topics(
            ["半导体", "商业航天"],
            now=datetime(2026, 7, 13, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    titles_in_order = [item.title for item in result]
    # 当日新闻应排在前面（_rank_news_by_recency）
    assert titles_in_order[0] == "新标题"
    # 同主题内"重复标题"经 _dedupe_news 仅保留一次
    assert titles_in_order.count("重复标题") == 1


def test_prefetch_topics_oversamples_before_cross_topic_top_k(
    news_prefetch_enabled, monkeypatch
):
    service = NewsService()
    monkeypatch.setattr(service.settings, "news_per_topic", 1)

    def fake_search(topic, limit=None, *, now=None):
        shared = NewsItem(
            topic=topic,
            title="共享头条",
            published_at="2026-07-13 14:20",
            source="test",
            url="https://example.test/shared",
        )
        unique = NewsItem(
            topic=topic,
            title=f"{topic}独有",
            published_at="2026-07-13 14:10",
            source="test",
            url=f"https://example.test/{topic}",
        )
        return [shared, unique][: int(limit or 1)]

    with patch.object(service, "search", side_effect=fake_search):
        result = service.prefetch_topics(
            ["半导体", "人工智能"],
            now=datetime(2026, 7, 13, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    assert len(result) == 2
    assert result[0].title == "共享头条"
    assert result[0].related_topics == ["人工智能", "半导体"]
    assert result[1].title in {"半导体独有", "人工智能独有"}


def test_prefetch_topics_reaches_unique_evidence_after_dense_cross_topic_duplicates(
    news_prefetch_enabled, monkeypatch
):
    service = NewsService()
    monkeypatch.setattr(service.settings, "news_per_topic", 5)
    topics = ["半导体", "人工智能", "商业航天", "医药", "银行"]
    observed_limits: list[int] = []

    def fake_search(topic, limit=None, *, now=None):
        observed_limits.append(int(limit or 0))
        shared = [
            NewsItem(
                topic=topic,
                title=f"共享转载 {index}",
                published_at=f"2026-07-13 14:{20 - index:02d}",
                source="test",
                url=f"https://example.test/shared/{index}",
            )
            for index in range(10)
        ]
        unique = NewsItem(
            topic=topic,
            title=f"{topic}独有证据",
            published_at="2026-07-13 14:00",
            source="test",
            url=f"https://example.test/unique/{topic}",
        )
        return [*shared, unique][: int(limit or 1)]

    with patch.object(service, "search", side_effect=fake_search):
        result = service.prefetch_topics(
            topics,
            now=datetime(2026, 7, 13, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        )

    titles = {item.title for item in result}
    assert observed_limits == [25] * len(topics)
    assert {f"{topic}独有证据" for topic in topics} <= titles


def test_prefetch_topics_single_topic_skips_threadpool(news_prefetch_enabled):
    service = NewsService()
    invoked_thread_ids: list[int] = []
    main_ident = threading.get_ident()

    def fake_search(topic, limit=None, *, now=None):
        invoked_thread_ids.append(threading.get_ident())
        return [_make_item(topic, "x")]

    with patch.object(service, "search", side_effect=fake_search):
        result = service.prefetch_topics(["半导体"])

    assert invoked_thread_ids == [main_ident], "单主题应不起线程池，直接主线程跑"
    assert result[0].title == "x"


def test_prefetch_topics_freezes_one_decision_clock_when_now_is_omitted(
    news_prefetch_enabled, monkeypatch
):
    service = NewsService()
    fixed_now = datetime(
        2026,
        7,
        14,
        0,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    )
    observed: list[datetime] = []
    monkeypatch.setattr(
        "app.services.news_service.normalize_news_now",
        lambda now=None: fixed_now if now is None else now,
    )

    def fake_search(topic, limit=None, *, now=None):
        observed.append(now)
        return [_make_item(topic, f"{topic} title")]

    with patch.object(service, "search", side_effect=fake_search):
        service.prefetch_topics(["半导体", "人工智能"])

    assert observed == [fixed_now, fixed_now]


def test_prefetch_topics_disabled_returns_empty(monkeypatch):
    service = NewsService()
    monkeypatch.setattr(service.settings, "news_enabled", False)
    assert service.prefetch_topics(["半导体"]) == []


def test_prefetch_topics_respects_total_timeout(news_prefetch_enabled, monkeypatch):
    service = NewsService()
    monkeypatch.setattr(service.settings, "news_prefetch_total_timeout_seconds", 0.05)

    def slow_search(topic: str, limit: int | None = None, *, now=None):
        time.sleep(0.3)
        return [_make_item(topic, f"{topic} title")]

    topics = ["半导体", "商业航天", "新能源车", "医药", "银行"]
    with patch.object(service, "search", side_effect=slow_search):
        start = time.monotonic()
        result = service.prefetch_topics(topics)
        elapsed = time.monotonic() - start

    assert elapsed < 0.35, f"总超时应尽快返回，实际 {elapsed:.2f}s"
    assert len(result) <= len(topics)


def test_prefetch_topics_total_timeout_does_not_wait_for_blocked_workers(
    news_prefetch_enabled, monkeypatch
):
    service = NewsService()
    monkeypatch.setattr(service.settings, "news_prefetch_total_timeout_seconds", 0.05)

    def blocked_search(topic: str, limit: int | None = None, *, now=None):
        time.sleep(1.0)
        return [_make_item(topic, f"{topic} title")]

    topics = ["半导体", "商业航天", "新能源车", "医药", "银行"]
    with patch.object(service, "search", side_effect=blocked_search):
        start = time.monotonic()
        result = service.prefetch_topics(topics)
        elapsed = time.monotonic() - start

    assert elapsed < 0.3, f"总超时应不等待阻塞 worker，实际 {elapsed:.2f}s"
    assert result == []
