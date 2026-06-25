"""基金诊断与开放式基金排行榜共享缓存。"""

from app.services.fund_diagnostics_cache import (
    diagnostics_cache_key,
    get_cached_fund_diagnostics,
    load_fund_diagnostics,
    save_cached_fund_diagnostics,
)
from app.services.fund_rank_cache import (
    fetch_open_fund_rank_cached,
    get_cached_open_fund_rank,
    rank_cache_key,
    save_cached_open_fund_rank,
)


def test_fund_diagnostics_cache_roundtrip():
    payload = {
        "fund_type": "混合型",
        "management_fee": 1.5,
        "return_1y_percent": 12.3,
    }
    save_cached_fund_diagnostics("519674", payload)
    cached = get_cached_fund_diagnostics("519674")
    assert cached == payload
    assert diagnostics_cache_key("519674") == "fund:diagnostics:v1:519674"


def test_load_fund_diagnostics_uses_cache(monkeypatch):
    save_cached_fund_diagnostics(
        "008586",
        {"fund_type": "股票型", "return_1y_percent": 5.0},
    )

    def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("should not fetch when cache warm")

    monkeypatch.setattr(
        "app.services.fund_diagnostics_cache._fetch_fund_diagnostics_via_akshare",
        _should_not_fetch,
    )
    result = load_fund_diagnostics("008586")
    assert result["fund_type"] == "股票型"


def test_fund_rank_cache_roundtrip():
    rows = [{"fund_code": "519674", "fund_name": "银河创新成长"}]
    save_cached_open_fund_rank(limit=300, rows=rows)
    cached = get_cached_open_fund_rank(limit=300)
    assert cached == rows
    assert rank_cache_key(300) == "fund:open_rank:v1:300"


def test_fetch_open_fund_rank_cached_skips_fetch(monkeypatch):
    rows = [{"fund_code": "008586", "fund_name": "测试"}]
    save_cached_open_fund_rank(limit=300, rows=rows)

    def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("should not fetch when cache warm")

    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_open_fund_rank",
        _should_not_fetch,
    )
    assert fetch_open_fund_rank_cached(limit=300) == rows
