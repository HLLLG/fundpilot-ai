from __future__ import annotations

from datetime import datetime
from itertools import permutations
import json
from subprocess import CompletedProcess
from zoneinfo import ZoneInfo

import pytest

from app.config import refresh_settings
from app.models import Holding, NewsItem
from app.services.analysis_payload import compact_news_titles
from app.services.cls_news_client import fetch_cls_headlines, search_cls_news
from app.services.news_freshness import (
    build_news_pipeline_context,
    is_news_published_today,
    parse_news_published_at,
)
from app.services.news_service import (
    NewsService,
    _dedupe_news,
    _prepare_news,
    _rank_news_by_recency,
)
from app.services.news_summarizer import group_news_by_topic
from app.services.recommendations import classify_sector_news

CN_TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 13, 15, 0, tzinfo=CN_TZ)


@pytest.fixture
def news_enabled(monkeypatch):
    monkeypatch.setenv("FUND_AI_NEWS_ENABLED", "true")
    refresh_settings()


def _item(
    title: str,
    published_at: str | None,
    *,
    topic: str = "半导体",
    source: str = "test",
    url: str | None = None,
) -> NewsItem:
    return NewsItem(
        topic=topic,
        title=title,
        published_at=published_at,
        source=source,
        url=url,
    )


def test_timestamp_normalization_supports_offsets_z_shanghai_date_and_invalid():
    utc_z = parse_news_published_at("2026-07-13T06:20:00Z")
    offset = parse_news_published_at("2026-07-13T14:20:00+08:00")
    local = parse_news_published_at("2026-07-13 14:20")
    date_only = parse_news_published_at("2026-07-13")
    invalid = parse_news_published_at("unknown")

    assert utc_z.moment == offset.moment == local.moment
    assert utc_z.moment == datetime(2026, 7, 13, 14, 20, tzinfo=CN_TZ)
    assert utc_z.has_time is True
    assert date_only.calendar_date.isoformat() == "2026-07-13"
    assert date_only.moment is None
    assert date_only.has_time is False
    assert invalid.calendar_date is None
    assert invalid.moment is None


def test_today_uses_shanghai_calendar_across_utc_boundary():
    assert is_news_published_today("2026-07-12T16:01:00Z", NOW) is True
    assert is_news_published_today("2026-07-12T15:59:00Z", NOW) is False
    assert is_news_published_today("2026-07-13", NOW) is True
    assert is_news_published_today("invalid", NOW) is False


def test_cls_today_uses_the_same_shanghai_decision_clock(monkeypatch):
    monkeypatch.setattr(
        "app.services.cls_news_client.fetch_cls_headlines",
        lambda limit=60: [
            {
                "title": "chip after Shanghai midnight",
                "date": "2026-07-12T16:01:00Z",
            },
            {
                "title": "chip before Shanghai midnight",
                "date": "2026-07-12T15:59:00Z",
            },
        ],
    )

    items = search_cls_news("chip", now=NOW)

    assert [item.is_today for item in items] == [True, False]


def test_cls_merges_real_split_publication_date_and_time(monkeypatch):
    monkeypatch.setattr(
        "app.services.cls_news_client.fetch_cls_headlines",
        lambda limit=60: [
            {
                "标题": "半导体盘中快讯",
                "内容": "半导体板块出现新动态",
                "发布日期": "2026-07-13",
                "发布时间": "14:20:00",
            }
        ],
    )

    items = search_cls_news("半导体", now=NOW)

    assert len(items) == 1
    assert items[0].published_at == "2026-07-13 14:20:00"
    assert items[0].is_today is True


def test_cls_filters_all_matches_before_newest_top_k(monkeypatch):
    monkeypatch.setattr(
        "app.services.cls_news_client.fetch_cls_headlines",
        lambda limit=60: [
            {"title": "chip early", "date": "2026-07-13 09:30"},
            {"title": "chip latest", "date": "2026-07-13 14:20"},
        ],
    )

    items = search_cls_news("chip", limit=1, now=NOW)

    assert [item.title for item in items] == ["chip latest"]


def test_cls_fetch_refreshes_provider_and_ranks_before_limit(monkeypatch):
    payloads = iter(
        [
            {
                "items": [
                    {"title": "early", "date": "2026-07-13 09:30"},
                    {"title": "latest", "date": "2026-07-13 14:20"},
                ]
            },
            {"items": [{"title": "refreshed", "date": "2026-07-13 14:30"}]},
        ]
    )
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return CompletedProcess(
            args=["python"],
            returncode=0,
            stdout=json.dumps(next(payloads)),
            stderr="",
        )

    monkeypatch.setattr("app.services.cls_news_client.subprocess.run", fake_run)

    first = fetch_cls_headlines(limit=1)
    second = fetch_cls_headlines(limit=1)

    assert [row["title"] for row in first] == ["latest"]
    assert [row["title"] for row in second] == ["refreshed"]
    assert len(calls) == 2


def test_news_service_threads_decision_clock_into_cls(monkeypatch):
    captured = {}

    def fake_search(topic, limit=5, *, now=None):
        captured.update(topic=topic, limit=limit, now=now)
        return []

    monkeypatch.setattr(
        "app.services.cls_news_client.search_cls_news",
        fake_search,
    )

    NewsService()._from_cls("chip", 3, now=NOW)

    assert captured == {"topic": "chip", "limit": 3, "now": NOW}


def test_pipeline_context_recomputes_today_and_preserves_related_topic_evidence():
    news = _item("跨时区头条", "2026-07-13T06:20:00Z", topic="半导体")
    news = news.model_copy(update={"related_topics": ["人工智能"]})
    context = build_news_pipeline_context([news], now=NOW)

    assert context["calendar_date"] == "2026-07-13"
    assert context["today_items"] == 1
    assert context["median_age_minutes"] == 40
    topics = {row["topic"]: row for row in context["topics"]}
    assert set(topics) == {"半导体", "人工智能"}
    assert topics["人工智能"]["latest_published_at"] == "2026-07-13T06:20:00Z"


def test_related_topics_remain_visible_to_briefs_and_holding_news_matching():
    article = NewsItem(
        topic="半导体",
        related_topics=["人工智能", "半导体"],
        title="人工智能产业链走强",
        published_at="2026-07-13 14:20",
        source="test",
        is_today=True,
    )

    grouped = group_news_by_topic([article])
    assert list(grouped) == ["半导体", "人工智能"]
    assert grouped["半导体"] == [article]
    assert grouped["人工智能"] == [article]

    holding = Holding(
        fund_code="000001",
        fund_name="人工智能主题基金",
        holding_amount=10_000,
        sector_name="人工智能",
    )
    bullish, bearish = classify_sector_news(holding, [article])
    assert bullish == ["人工智能产业链走强（2026-07-13 14:20）"]
    assert bearish == []

    assert compact_news_titles([article], min_items=0) == [
        {
            "topic": "半导体",
            "related_topics": ["人工智能", "半导体"],
            "title": "人工智能产业链走强",
            "is_today": True,
            "published_at": "2026-07-13 14:20",
            "source": "test",
        }
    ]


def test_rank_today_known_times_newest_first_then_today_unknown_time():
    items = [
        _item("今日未知时间", "2026-07-13"),
        _item("09:30", "2026-07-13 09:30"),
        _item("昨日", "2026-07-12 23:59"),
        _item("14:20", "2026-07-13T06:20:00Z"),
        _item("11:00", "2026-07-13 11:00:00+08:00"),
        _item("无效时间", "unknown"),
    ]

    ranked = _rank_news_by_recency(items, now=NOW)

    assert [item.title for item in ranked] == [
        "14:20",
        "11:00",
        "09:30",
        "今日未知时间",
        "昨日",
        "无效时间",
    ]
    assert [item.is_today for item in ranked[:4]] == [True, True, True, True]


def test_newer_date_only_news_beats_older_timestamp_and_sets_topic_latest():
    items = [
        _item("较旧但有时分", "2026-07-10 12:00"),
        _item("较新但只有日期", "2026-07-12"),
    ]

    assert [item.title for item in _rank_news_by_recency(items, now=NOW)] == [
        "较新但只有日期",
        "较旧但有时分",
    ]
    context = build_news_pipeline_context(items, now=NOW)
    topic = next(row for row in context["topics"] if row["topic"] == "半导体")
    assert topic["latest_published_at"] == "2026-07-12"


def test_direct_dedupe_call_preserves_newest_first_instead_of_identity_key_order():
    items = [
        _item("A identity but old", "2026-07-10 12:00", url="https://a.example/1"),
        _item("Z identity but new", "2026-07-13 14:20", url="https://z.example/1"),
    ]

    assert [item.title for item in _dedupe_news(items, now=NOW)] == [
        "Z identity but new",
        "A identity but old",
    ]


def test_cross_topic_url_dedupe_merges_topics_and_is_order_independent():
    first = _item(
        "同一事件",
        "2026-07-13 14:20",
        topic="半导体",
        source="测试源",
        url="HTTPS://Example.COM/news/1/?utm_source=x#detail",
    )
    second = _item(
        "同一事件",
        "2026-07-13T06:20:00Z",
        topic="人工智能",
        source="测试源",
        url="https://example.com/news/1",
    )

    forward = _dedupe_news([first, second], now=NOW)
    reverse = _dedupe_news([second, first], now=NOW)

    assert len(forward) == len(reverse) == 1
    assert forward[0].model_dump() == reverse[0].model_dump()
    assert forward[0].related_topics == ["人工智能", "半导体"]


def test_cross_topic_title_source_fallback_dedupe_ignores_topic():
    items = [
        _item("  芯片  行业利好 ", "2026-07-13 11:00", topic="芯片", source="财联社"),
        _item("芯片 行业利好", "2026-07-13 11:00", topic="人工智能", source=" 财联社 "),
    ]

    deduped = _dedupe_news(items, now=NOW)

    assert len(deduped) == 1
    assert deduped[0].related_topics == ["人工智能", "芯片"]


def test_dedupe_happens_before_top_k():
    items = [
        _item("头条", "2026-07-13 14:20", url="https://example.com/1"),
        _item(
            "头条转载",
            "2026-07-13 14:19",
            topic="人工智能",
            url="https://example.com/1#copy",
        ),
        _item("次条", "2026-07-13 13:00", url="https://example.com/2"),
    ]

    prepared = _prepare_news(items, limit=2, now=NOW)

    assert [item.title for item in prepared] == ["头条", "次条"]
    assert prepared[0].related_topics == ["人工智能", "半导体"]


def test_stable_tie_breakers_do_not_depend_on_completion_order():
    items = [
        _item("B 标题", "2026-07-13 10:00", topic="银行", source="source-b"),
        _item("A 标题", "2026-07-13 10:00", topic="医药", source="source-a"),
        _item("C 标题", "2026-07-13 10:00", topic="军工", source="source-a"),
    ]
    expected = [item.model_dump() for item in _prepare_news(items, now=NOW)]

    for permuted in permutations(items):
        assert [item.model_dump() for item in _prepare_news(list(permuted), now=NOW)] == expected


def test_cached_news_reuses_normalize_dedupe_rank_before_limit(
    news_enabled, monkeypatch
):
    cached = [
        _item("次条", "2026-07-13 13:00", url="https://example.com/2"),
        _item("头条转载", "2026-07-13 14:19", url="https://example.com/1#copy"),
        _item("头条", "2026-07-13 14:20", url="https://example.com/1"),
    ]
    monkeypatch.setattr(
        "app.services.news_service.get_cached_news",
        lambda *_args, **_kwargs: cached,
    )
    service = NewsService()

    result = service.search("半导体", limit=2, now=NOW)

    assert [item.title for item in result] == ["头条", "次条"]


def test_small_limit_does_not_poison_later_larger_cached_request(
    news_enabled, monkeypatch
):
    service = NewsService()
    monkeypatch.setattr(service.settings, "news_sources", "eastmoney")
    cache: dict[str, list[NewsItem]] = {}
    provider_calls = []
    source_items = [
        _item("first", "2026-07-13 14:20", url="https://example.com/1"),
        _item("second", "2026-07-13 14:10", url="https://example.com/2"),
        _item("third", "2026-07-13 14:00", url="https://example.com/3"),
    ]

    monkeypatch.setattr(
        "app.services.news_service.get_cached_news",
        lambda topic, **_kwargs: cache.get(topic),
    )

    def fake_save(topic, items, **_kwargs):
        cache[topic] = list(items)

    def fake_eastmoney(topic, limit, *, now=None):
        provider_calls.append((topic, limit, now))
        return source_items

    monkeypatch.setattr("app.services.news_service.save_cached_news", fake_save)
    monkeypatch.setattr(service, "_from_eastmoney", fake_eastmoney)

    first = service.search("chip", limit=1, now=NOW)
    later = service.search("chip", limit=3, now=NOW)

    assert [item.title for item in first] == ["first"]
    assert [item.title for item in later] == ["first", "second", "third"]
    assert len(cache["chip"]) == 3
    assert len(provider_calls) == 1
