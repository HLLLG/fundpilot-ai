from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.config import refresh_settings
from app.models import NewsItem
from app.services.eastmoney_news_client import (
    EastmoneyNewsFetchResult,
    _latest_announcement_rows,
    _run_akshare_script_result,
    fetch_fund_announcement_report_em,
)
from app.services.news_service import NewsService

NOW = datetime(2026, 7, 14, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


@pytest.fixture
def announcement_service(monkeypatch):
    monkeypatch.setenv("FUND_AI_NEWS_ENABLED", "true")
    monkeypatch.setenv("FUND_AI_NEWS_SOURCES", "announcement")
    refresh_settings()
    service = NewsService()
    cache: dict[tuple[str, str], list[NewsItem]] = {}

    def fake_get(topic, cache_date=None, **_kwargs):
        value = cache.get((topic, str(cache_date)))
        return None if value is None else list(value)

    def fake_save(topic, items, cache_date=None, **_kwargs):
        cache[(topic, str(cache_date))] = list(items)

    monkeypatch.setattr("app.services.news_service.get_cached_news", fake_get)
    monkeypatch.setattr("app.services.news_service.save_cached_news", fake_save)
    return service, cache


def _row(code: str, suffix: str, published: str) -> dict[str, str]:
    return {
        "公告标题": f"{code} 公告 {suffix}",
        "公告日期": published,
        "基金名称": f"基金 {code}",
    }


def test_announcement_budget_is_independent_from_topic_budget_and_keeps_latest(
    announcement_service,
    monkeypatch,
):
    service, _cache = announcement_service
    monkeypatch.setattr(service.settings, "news_max_topics", 1)
    monkeypatch.setattr(service.settings, "news_announcement_max_funds", 2)
    monkeypatch.setattr(service.settings, "news_announcement_per_fund", 2)
    calls: list[tuple[str, int]] = []

    def fake_fetch(code: str, *, limit: int):
        calls.append((code, limit))
        return EastmoneyNewsFetchResult(
            status="ok",
            items=[
                _row(code, "旧", "2026-07-12"),
                _row(code, "新", "2026-07-14 09:00"),
                _row(code, "超预算", "2026-07-14 08:00"),
            ],
        )

    monkeypatch.setattr(
        "app.services.eastmoney_news_client.fetch_fund_announcement_report_result_em",
        fake_fetch,
    )

    result = service.prefetch_fund_announcements(
        ["000001", "bad", "000000", "000001", "000002", "000003"],
        now=NOW,
    )

    assert result["requested_codes"] == ["000001", "000002"]
    assert result["input_count"] == 6
    assert result["eligible_fund_count"] == 3
    assert result["requested"] == 2
    assert result["skipped_by_limit"] == 1
    assert result["budget_coverage"] == pytest.approx(2 / 3, abs=0.0001)
    assert result["ok"] == 2
    assert result["coverage"] == 1.0
    assert result["evidence_coverage"] == 1.0
    assert result["fetched_at"] == "2026-07-14T09:30:00+08:00"
    assert set(calls) == {("000001", 2), ("000002", 2)}
    assert len(result["items"]) == 4
    by_code = {row["fund_code"]: row for row in result["funds"]}
    assert by_code["000001"]["latest_published_at"] == "2026-07-14 09:00"
    assert by_code["000002"]["item_count"] == 2


def test_partial_provider_failures_keep_success_and_distinguish_empty(
    announcement_service,
    monkeypatch,
):
    service, cache = announcement_service
    monkeypatch.setattr(service.settings, "news_announcement_max_funds", 4)
    statuses = {
        "000001": EastmoneyNewsFetchResult(
            status="ok",
            items=[_row("000001", "成功", "2026-07-14")],
        ),
        "000002": EastmoneyNewsFetchResult(status="empty", items=[]),
        "000004": EastmoneyNewsFetchResult(status="timeout", items=[]),
    }

    def fake_fetch(code: str, *, limit: int):
        if code == "000003":
            raise OSError("mock provider failure")
        return statuses[code]

    monkeypatch.setattr(
        "app.services.eastmoney_news_client.fetch_fund_announcement_report_result_em",
        fake_fetch,
    )

    result = service.prefetch_fund_announcements(
        ["000001", "000002", "000003", "000004"],
        now=NOW,
    )

    assert {key: result[key] for key in ("ok", "empty", "error", "timeout")} == {
        "ok": 1,
        "empty": 1,
        "error": 1,
        "timeout": 1,
    }
    assert result["coverage"] == 0.5
    assert result["evidence_coverage"] == 0.25
    assert [item.topic for item in result["items"]] == ["000001"]
    assert ("fund-announcement:000001", "announcement-v1") in cache
    assert cache[("fund-announcement:000002", "announcement-v1")] == []
    assert ("fund-announcement:000003", "announcement-v1") not in cache
    assert ("fund-announcement:000004", "announcement-v1") not in cache


def test_empty_cache_is_reused_without_collapsing_into_provider_failure(
    announcement_service,
    monkeypatch,
):
    service, _cache = announcement_service
    calls = 0

    def fake_fetch(code: str, *, limit: int):
        nonlocal calls
        calls += 1
        return EastmoneyNewsFetchResult(status="empty", items=[])

    monkeypatch.setattr(
        "app.services.eastmoney_news_client.fetch_fund_announcement_report_result_em",
        fake_fetch,
    )

    first = service.prefetch_fund_announcements(["000001"], now=NOW)
    second = service.prefetch_fund_announcements(["000001"], now=NOW)

    assert calls == 1
    assert first["empty"] == second["empty"] == 1
    assert first["funds"][0]["from_cache"] is False
    assert second["funds"][0]["from_cache"] is True


def test_announcement_cache_uses_its_own_ttl_and_recomputes_today(
    announcement_service,
    monkeypatch,
):
    service, _cache = announcement_service
    monkeypatch.setattr(service.settings, "news_announcement_cache_ttl_seconds", 1234)
    observed: dict[str, object] = {}
    cached = NewsItem(
        topic="000001",
        title="昨日缓存公告",
        published_at="2026-07-13 15:00",
        source="fund-announcement",
        is_today=True,
    )

    def fake_get(topic, cache_date=None, *, max_age_seconds=None, now=None):
        observed.update(
            topic=topic,
            cache_date=cache_date,
            max_age_seconds=max_age_seconds,
            now=now,
        )
        return [cached]

    monkeypatch.setattr("app.services.news_service.get_cached_news", fake_get)
    monkeypatch.setattr(
        "app.services.eastmoney_news_client.fetch_fund_announcement_report_result_em",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit must not call provider")
        ),
    )

    result = service.prefetch_fund_announcements(["000001"], now=NOW)

    assert observed == {
        "topic": "fund-announcement:000001",
        "cache_date": "announcement-v1",
        "max_age_seconds": 1234,
        "now": NOW,
    }
    assert result["items"][0].is_today is False
    assert result["funds"][0]["from_cache"] is True


def test_total_timeout_returns_promptly_and_marks_only_pending_fund_timeout(
    announcement_service,
    monkeypatch,
):
    service, _cache = announcement_service
    monkeypatch.setattr(
        service.settings,
        "news_announcement_prefetch_total_timeout_seconds",
        0.05,
    )
    release = threading.Event()

    def fake_fetch(code: str, *, limit: int):
        if code == "000002":
            release.wait(timeout=1.0)
        return EastmoneyNewsFetchResult(
            status="ok",
            items=[_row(code, "结果", "2026-07-14")],
        )

    monkeypatch.setattr(
        "app.services.eastmoney_news_client.fetch_fund_announcement_report_result_em",
        fake_fetch,
    )

    started = time.monotonic()
    result = service.prefetch_fund_announcements(["000001", "000002"], now=NOW)
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 0.25
    assert result["ok"] == 1
    assert result["timeout"] == 1
    assert [item.topic for item in result["items"]] == ["000001"]


def test_generic_announcement_titles_from_different_funds_are_not_deduped(
    announcement_service,
    monkeypatch,
):
    service, _cache = announcement_service

    def fake_fetch(code: str, *, limit: int):
        return EastmoneyNewsFetchResult(
            status="ok",
            items=[
                {
                    "公告标题": "季度报告提示性公告",
                    "公告日期": "2026-07-14",
                    "基金名称": code,
                }
            ],
        )

    monkeypatch.setattr(
        "app.services.eastmoney_news_client.fetch_fund_announcement_report_result_em",
        fake_fetch,
    )

    result = service.prefetch_fund_announcements(["000001", "000002"], now=NOW)

    assert len(result["items"]) == 2
    assert {item.topic for item in result["items"]} == {"000001", "000002"}


def test_nonempty_but_unusable_provider_rows_are_error_not_empty(
    announcement_service,
    monkeypatch,
):
    service, cache = announcement_service
    monkeypatch.setattr(
        "app.services.eastmoney_news_client.fetch_fund_announcement_report_result_em",
        lambda *_args, **_kwargs: EastmoneyNewsFetchResult(
            status="ok",
            items=[{"公告日期": "2026-07-14", "基金名称": "缺标题"}],
        ),
    )

    result = service.prefetch_fund_announcements(["000001"], now=NOW)

    assert result["error"] == 1
    assert result["empty"] == 0
    assert result["coverage"] == 0.0
    assert cache == {}


@pytest.mark.parametrize(
    ("payload", "returncode", "expected"),
    [
        ({"items": [{"title": "x"}]}, 0, "ok"),
        ({"items": []}, 0, "empty"),
        ({"items": [], "error": "provider failed"}, 0, "error"),
        ({"items": []}, 1, "error"),
    ],
)
def test_provider_outcome_classification_has_no_real_network(
    monkeypatch,
    payload,
    returncode,
    expected,
):
    monkeypatch.setattr(
        "app.services.eastmoney_news_client.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=returncode,
            stdout=json.dumps(payload),
            stderr="mock stderr",
        ),
    )

    result = _run_akshare_script_result("print('mock')", label="test")

    assert result.status == expected


def test_provider_timeout_is_structured_and_legacy_list_remains_compatible(monkeypatch):
    monkeypatch.setattr(
        "app.services.eastmoney_news_client.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd="python", timeout=1)
        ),
    )

    structured = _run_akshare_script_result("print('mock')", label="test")
    legacy = fetch_fund_announcement_report_em("000001", limit=1)

    assert structured.status == "timeout"
    assert legacy == []


def test_provider_rows_are_sorted_newest_first_before_limit():
    rows = [
        {"公告日期": "2014-08-27", "公告标题": "最旧"},
        {"公告日期": "2026-03-28", "公告标题": "次新"},
        {"公告日期": "invalid", "公告标题": "日期无效一"},
        {"公告日期": "2026-04-21", "公告标题": "最新"},
        {"公告日期": "invalid", "公告标题": "日期无效二"},
    ]

    ordered = _latest_announcement_rows(rows, limit=3)

    assert [row["公告标题"] for row in ordered] == ["最新", "次新", "最旧"]
