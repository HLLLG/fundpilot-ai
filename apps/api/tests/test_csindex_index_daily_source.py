"""中证指数官方日线源，以及被它解开的三处基准链路缺陷。

回归背景（2026-08-11 从生产服务器逐源实测）：

`index_daily_client` 原来只有东财 kline 与新浪两条路。东财 kline 从这台机器整体不可用
（`push2his` / `18.push2his` / `49.push2his` 全部 `RemoteDisconnected`），而新浪与腾讯
`gtimg` 只报**交易所挂牌**代码——`000300`/`399989` 有数据，`930598`（中证稀土产业）
`931582`（中证数字经济主题）`930713`/`931994` 一律 0 行，因为这些中证指数没有交易所行情
代码。于是：

* `fund_benchmark_research` 每只持仓的基准腿都拿不到序列（当时报
  `index:9xxxxx_snapshot_envelope_missing`），跟踪指标恒为不可用；
* canonical 日 K 对指数类主题只能退到板块资金流**代理**（成分篮子不同）或整条为空；
* 风电(931672) 与港股银行(930792) 被判定为"确实无源"。

中证指数公司官方接口 `www.csindex.com.cn/csindex-home/perf/index-perf` 能报全部代码族
（9xxxxx、H3xxxx、000xxx、399xxx，含 931672 与 930792），0.1s 量级，两年 485 行 0.22s，
且它是这些指数的发布方。本文件锁住：

1. 官方源的解析与代码形态守卫（不能把个股符号误当指数代码）；
2. 它在 `index_daily_client` 里排第一，且不拦住非中证符号；
3. `default_benchmark_fetcher` 不再把中证 `H` 系列挡在门外；
4. 契约冻结与消费两侧的时间戳口径、以及"明确不可用"原因的透传。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from app.services import csindex_daily_client as csindex
from app.services import index_daily_client as index_client
from app.services.benchmark_fee_evaluation import default_benchmark_fetcher
from app.services.benchmark_mapping_service import (
    _cached_benchmark_evidence_by_code,
    freeze_fund_benchmark_spec,
)
from app.services.fund_peer_ranking import resolve_benchmark_comparison

_DECISION = datetime(2026, 8, 11, 15, 0, tzinfo=timezone.utc)


class _Response:
    status_code = 200

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


def _csindex_rows(count: int, *, start: date = date(2026, 3, 2)) -> list[dict[str, Any]]:
    """连续 `count` 个工作日的官方行，`tradeDate` 是 YYYYMMDD。"""
    rows: list[dict[str, Any]] = []
    day = start
    while len(rows) < count:
        if day.weekday() < 5:
            index = len(rows)
            rows.append(
                {
                    "tradeDate": day.strftime("%Y%m%d"),
                    "indexCode": "930598",
                    "close": 1000.0 + index,
                    "open": 999.0 + index,
                    "high": 1002.0 + index,
                    "low": 998.0 + index,
                }
            )
        day += timedelta(days=1)
    return rows


@pytest.fixture(autouse=True)
def _clean_slate(monkeypatch) -> None:
    with index_client._INDEX_TTL_CACHE_LOCK:
        index_client._INDEX_TTL_CACHE.clear()
    csindex.reset_csindex_rate_state()
    # 默认关掉限速睡眠、持久缓存与跨进程熔断，逐条用例自己决定要不要打开。
    # 跨进程熔断走的是真实的 `sector_quote_cache`，不 stub 会让"打开熔断"的用例
    # 把状态写进共享缓存表、污染同一进程里后面的用例。
    monkeypatch.setattr(csindex, "CSINDEX_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(csindex, "_load_cached", lambda _code: None)
    monkeypatch.setattr(csindex, "_save_cached", lambda _code, _payload: None)
    monkeypatch.setattr(csindex, "_publish_durable_breaker", lambda: None)
    monkeypatch.setattr(csindex, "_durable_breaker_is_open", lambda: False)
    # 本文件测的是中证那一路。雪球排在它前面，默认让它空掉，让链路走到中证。
    monkeypatch.setattr(
        index_client, "fetch_xueqiu_index_daily_history", lambda *_a, **_k: None
    )
    yield
    with index_client._INDEX_TTL_CACHE_LOCK:
        index_client._INDEX_TTL_CACHE.clear()
    csindex.reset_csindex_rate_state()


# --- 1. 官方源本体 -----------------------------------------------------------


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("930598", True),
        ("931672", True),
        ("930792", True),
        ("000300", True),
        ("399989", True),
        ("H30202", True),
        ("h30054", True),
        ("H1100", True),
        ("sh600519", False),
        ("sz000001", False),
        ("AU9999", False),
        ("90.BK0727", False),
        ("118.AU9999", False),
        ("", False),
        ("12345", False),
    ],
)
def test_only_csindex_shaped_codes_reach_the_official_endpoint(symbol, expected) -> None:
    """守卫的关键作用是别把个股符号当指数代码。

    `sector_constituent_proxy` 走的是同一个 `fetch_index_daily_history`，传进来的是
    `sh600519` 这种带交易所前缀的**个股**符号。裸 6 位数字在那条路上不会出现，所以
    `000001` 不会被当成上证指数去取。
    """
    assert csindex.is_csindex_code(symbol) is expected


def test_official_payload_is_normalized_to_the_shared_shape(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _Response({"code": "200", "msg": "Success", "data": _csindex_rows(5)})

    monkeypatch.setattr(csindex.requests, "get", fake_get)
    result = csindex.fetch_csindex_daily_history("930598", trading_days=40)

    assert captured["url"] == csindex.CSINDEX_URL
    assert captured["params"]["indexCode"] == "930598"
    assert len(captured["params"]["startDate"]) == 8
    assert result is not None
    assert result["source"] == "csindex"
    assert result["data"][0] == {
        "date": "2026-03-02",
        "close": 1000.0,
        "open": 999.0,
        "high": 1002.0,
        "low": 998.0,
    }
    assert [row["date"] for row in result["data"]] == sorted(
        row["date"] for row in result["data"]
    )


def test_unknown_code_returns_none_instead_of_an_empty_series(monkeypatch) -> None:
    """未知代码官方接口返回 `code=200, data=[]`，必须转成 None 好让下游继续兜底。"""
    monkeypatch.setattr(
        csindex.requests,
        "get",
        lambda *_a, **_k: _Response({"code": "200", "msg": "Success", "data": []}),
    )
    assert csindex.fetch_csindex_daily_history("999999", trading_days=40) is None


def test_provider_failure_is_swallowed_into_none(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(csindex.requests, "get", boom)
    assert csindex.fetch_csindex_daily_history("930598", trading_days=40) is None


def test_trading_days_trims_from_the_recent_end(monkeypatch) -> None:
    rows = _csindex_rows(30)
    monkeypatch.setattr(
        csindex.requests,
        "get",
        lambda *_a, **_k: _Response({"code": "200", "data": rows}),
    )
    result = csindex.fetch_csindex_daily_history("930598", trading_days=20)
    assert result is not None
    assert len(result["data"]) == 20
    assert result["data"][-1]["date"] == csindex._iso_day(rows[-1]["tradeDate"])


# --- 2. 在 index_daily_client 里的位置 ---------------------------------------


def test_csindex_is_the_last_resort_not_the_first_choice(monkeypatch) -> None:
    """新浪能报的代码不得消耗中证配额——它是稀缺资源（约 50 次就 403 且不快恢复）。"""
    monkeypatch.setattr(
        index_client,
        "fetch_csindex_daily_history",
        lambda *_a, **_k: pytest.fail("csindex must not be spent when sina answers"),
    )
    monkeypatch.setattr(
        index_client, "_fetch_eastmoney_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        index_client,
        "_fetch_sina_daily_history",
        lambda *_a, **_k: {"data": [{"date": "2026-07-01", "close": 1.0}], "source": "sina"},
    )
    result = index_client.fetch_index_daily_history("399989", trading_days=40)
    assert result is not None and result["source"] == "sina"


def test_csindex_covers_codes_no_exchange_source_can_serve(monkeypatch) -> None:
    """9xxxxx 没有交易所行情代码，前两路必然空，这时才该动用官方接口。"""
    monkeypatch.setattr(
        index_client, "_fetch_eastmoney_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(index_client, "_fetch_sina_daily_history", lambda *_a, **_k: None)
    monkeypatch.setattr(
        index_client,
        "fetch_csindex_daily_history",
        lambda symbol, trading_days=252: {
            "data": [{"date": "2026-07-01", "close": 1.0}] * 2,
            "source": "csindex",
        },
    )
    result = index_client.fetch_index_daily_history("930598", trading_days=40)
    assert result is not None and result["source"] == "csindex"


def test_non_csindex_symbols_never_reach_the_official_endpoint(monkeypatch) -> None:
    """个股符号不得走中证接口，否则成分股代理每只都要白花一次配额。"""
    monkeypatch.setattr(
        index_client,
        "fetch_csindex_daily_history",
        lambda *_a, **_k: pytest.fail("csindex must not be called for stock symbols"),
    )
    monkeypatch.setattr(
        index_client, "_fetch_eastmoney_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(index_client, "_fetch_sina_daily_history", lambda *_a, **_k: None)
    assert index_client.fetch_index_daily_history("sh600519", trading_days=40) is None


# --- 3. 基准成分腿：H 系列不再被挡 ------------------------------------------


def test_h_series_components_now_reach_a_provider(monkeypatch) -> None:
    """`symbol.isdigit()` 曾把整个中证 H 系列挡死，基准腿因此永远没有序列。"""
    seen: list[str] = []
    monkeypatch.setattr(
        "app.services.index_daily_client.fetch_index_daily_history",
        lambda symbol, trading_days=252: seen.append(symbol)
        or {"data": [{"date": "2026-07-01", "close": 1.0}], "source": "csindex"},
    )
    payload = default_benchmark_fetcher(
        {"component_type": "index", "benchmark_code": "H30202"},
        start_date="2025-01-01",
        end_date="2026-08-11",
    )
    assert seen == ["H30202"]
    assert payload is not None


def test_non_index_symbols_stay_unavailable(monkeypatch) -> None:
    """上金所黄金现货不是指数，如实缺席，不去猜一个替身。"""
    monkeypatch.setattr(
        "app.services.index_daily_client.fetch_index_daily_history",
        lambda *_a, **_k: pytest.fail("must not fetch a non-index symbol"),
    )
    assert (
        default_benchmark_fetcher(
            {"component_type": "index", "benchmark_code": "AU9999"},
            start_date="2025-01-01",
            end_date="2026-08-11",
        )
        is None
    )


# --- 4. 契约不可用原因的透传与时间戳口径 ------------------------------------


def test_declared_unavailable_reason_is_not_rewritten_as_a_timestamp_problem() -> None:
    """明确不可用的契约压根没有 available_at，不能把真实原因改写成时间戳问题。"""
    spec = {
        "schema_version": "fund_benchmark_mapping.v1",
        "tier": "unavailable",
        "status": "unavailable",
        "formal_excess_eligible": False,
        "mapping_id": None,
        "contract_verification_kind": None,
        "available_at": None,
        "reason": "point_in_time_benchmark_mapping_unavailable",
        "components": [],
    }
    comparison = resolve_benchmark_comparison(spec, decision_at=_DECISION)
    assert comparison["comparison_role"] == "unavailable"
    assert comparison["reason"] == "point_in_time_benchmark_mapping_unavailable"


def test_a_genuinely_broken_timestamp_is_still_reported_as_such() -> None:
    """反面：契约声称完整但时间戳坏了，那就该说时间戳。"""
    spec = {
        "schema_version": "fund_benchmark_mapping.v1",
        "tier": "tracked_index_exact",
        "status": "complete",
        "benchmark_code": "930598",
        "benchmark_name": "中证稀土产业",
        "mapping_id": "fbm_x",
        "available_at": "not-a-timestamp",
        "reason": "tracking_index_is_reference_only",
        "components": [],
    }
    comparison = resolve_benchmark_comparison(spec, decision_at=_DECISION)
    assert comparison["reason"] == "benchmark_available_at_missing_or_invalid"


def test_naive_cache_timestamps_are_frozen_as_timezone_aware() -> None:
    """冻结方与消费方的时间解析宽严不一致，会让完整契约在下游被判成时间戳坏了。"""
    evidence = {
        "fund_code": "011036",
        "source": "precompute_benchmark",
        "available_at": "2026-08-11 05:16:53",  # 无时区
        "source_ref": "test",
        "detail": {
            "index_code": "930598",
            "index_name": "中证稀土产业",
            "benchmark_text": "中证稀土产业指数收益率×95%+银行活期存款利率(税后)×5%",
            "benchmark_text_kind": "performance_benchmark",
            "benchmark_text_source_kind": "xq_akshare_aggregator",
            "benchmark_text_truncated": False,
        },
    }
    spec, mapping = freeze_fund_benchmark_spec(
        fund_code="011036",
        decision_at=_DECISION.isoformat(),
        user_id=1,
        connection=object(),
        _preloaded_evidence=evidence,
    )
    assert spec["tier"] == "tracked_index_exact"
    assert spec["available_at"].endswith("+00:00")
    assert mapping is not None and mapping["valid_from"] == "2026-08-11"
    comparison = resolve_benchmark_comparison(spec, decision_at=_DECISION)
    assert comparison["reason"] != "benchmark_available_at_missing_or_invalid"
    assert comparison["comparison_role"] == "tracking_reference"


# --- 5. 缓存行择优：空明细不得压过完整明细 ----------------------------------


class _FakeConnection:
    """按 SQL 文本分派的只读假连接。"""

    def __init__(self, local_rows: list[dict], global_rows: list[dict]) -> None:
        self._local = local_rows
        self._global = global_rows

    def execute(self, sql: str, _params: tuple = ()) -> "_FakeConnection":
        self._last = self._global if "fund_primary_sectors_global" in sql else self._local
        return self

    def fetchall(self) -> list[dict]:
        return list(self._last)


def test_a_newer_empty_row_must_not_shadow_an_older_complete_one() -> None:
    """002610 线上实测：较新但空的本地行压过带 AU9999 的较旧全局行。"""
    connection = _FakeConnection(
        local_rows=[
            {
                "fund_code": "002610",
                "sector_name": "黄金",
                "intraday_index_name": None,
                "source": "precompute_benchmark",
                "confidence": None,
                "detail": {"fund_name": "某黄金基金"},
                "updated_at": "2026-08-11T05:16:53.355962+00:00",
            }
        ],
        global_rows=[
            {
                "fund_code": "002610",
                "sector_name": "黄金",
                "intraday_index_name": None,
                "source": "precompute_benchmark",
                "confidence": None,
                "detail": {
                    "index_code": "AU9999",
                    "index_name": "上海金",
                    "benchmark_text": "上海黄金交易所AU99.99收益率×95%+银行活期存款税后利率×5%",
                },
                "resolved_at": "2026-08-05T06:01:34.509244+00:00",
            }
        ],
    )
    selected = _cached_benchmark_evidence_by_code(
        connection,
        user_id=1,
        fund_codes=("002610",),
        decision_at=_DECISION.isoformat(),
    )["002610"]
    assert selected is not None
    assert (selected.get("detail") or {}).get("index_code") == "AU9999"


def test_recency_still_wins_between_two_complete_rows() -> None:
    """两行都完整时仍按来源等级与时间排，择优规则只用来排除"空"。"""
    detail = {
        "index_code": "930598",
        "benchmark_text": "中证稀土产业指数收益率×95%+银行活期存款利率(税后)×5%",
    }
    connection = _FakeConnection(
        local_rows=[
            {
                "fund_code": "011036",
                "source": "precompute_benchmark",
                "detail": detail,
                "updated_at": "2026-08-11T05:16:53+00:00",
            }
        ],
        global_rows=[
            {
                "fund_code": "011036",
                "source": "precompute_benchmark",
                "detail": detail,
                "resolved_at": "2026-07-25T12:52:14+00:00",
            }
        ],
    )
    selected = _cached_benchmark_evidence_by_code(
        connection,
        user_id=1,
        fund_codes=("011036",),
        decision_at=_DECISION.isoformat(),
    )["011036"]
    assert selected is not None
    assert selected["available_at"].startswith("2026-08-11")


# --- 6. 稀缺资源治理：限速 / 熔断 / 持久缓存 --------------------------------
#
# 2026-08-11 实测：连续约 50 次请求后该站返回 403，且每 20s 探一次连续 3 分钟仍未恢复。
# 所以这一路必须"省着用"，下面几条锁的就是省的方式。


def _counting_get(calls: list[str], payload: object = None, status: int = 200):
    class _Resp(_Response):
        status_code = status

        def raise_for_status(self) -> None:
            if status >= 400:
                raise RuntimeError(f"http {status}")

    def fake_get(_url, **kwargs):
        calls.append(str((kwargs.get("params") or {}).get("indexCode")))
        return _Resp(payload if payload is not None else {"code": "200", "data": _csindex_rows(5)})

    return fake_get


def test_live_budget_caps_how_many_codes_one_batch_can_fetch(monkeypatch) -> None:
    """一次批量刷新有几十个板块，不能变成几十次突发请求。"""
    calls: list[str] = []
    monkeypatch.setattr(csindex.requests, "get", _counting_get(calls))
    monkeypatch.setattr(csindex, "CSINDEX_MAX_LIVE_PER_WINDOW", 3)

    results = [
        csindex.fetch_csindex_daily_history(f"93{index:04d}", trading_days=40)
        for index in range(8)
    ]
    assert len(calls) == 3
    assert sum(1 for item in results if item is not None) == 3
    # 超预算的那几个如实为空，让调用方继续兜底，而不是排队把请求堆起来。
    assert results[3:] == [None] * 5


def test_a_403_opens_the_breaker_and_stops_all_further_requests(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(csindex.requests, "get", _counting_get(calls, status=403))

    assert csindex.fetch_csindex_daily_history("930598", trading_days=40) is None
    assert csindex.csindex_blocked_seconds_remaining() > 0
    assert csindex.fetch_csindex_daily_history("931582", trading_days=40) is None
    assert len(calls) == 1  # 被限流后一次都不再打


def test_breaker_still_serves_the_persisted_series(monkeypatch) -> None:
    """熔断期间有旧数据就给旧的——它的 date 自己说明截止到哪天。"""
    monkeypatch.setattr(csindex.requests, "get", _counting_get([], status=403))
    csindex.fetch_csindex_daily_history("930598", trading_days=40)
    assert csindex.csindex_blocked_seconds_remaining() > 0

    cached = {
        "data": [{"date": f"2026-06-{i + 1:02d}", "close": 100.0 + i} for i in range(30)],
        "source": "csindex",
        "data_end_date": "2026-06-30",
    }
    monkeypatch.setattr(csindex, "_load_cached", lambda _code: cached)
    result = csindex.fetch_csindex_daily_history("930598", trading_days=40)
    assert result is not None
    assert result["source"] == "csindex"
    assert len(result["data"]) == 30


def test_a_current_snapshot_costs_zero_requests(monkeypatch) -> None:
    """日线收盘一个交易日只变一次，同一交易日不该再回源。"""
    monkeypatch.setattr(
        csindex.requests,
        "get",
        lambda *_a, **_k: pytest.fail("must not hit the network with a current snapshot"),
    )
    monkeypatch.setattr(
        csindex,
        "_load_cached",
        lambda _code: {
            "data": [
                {"date": f"2026-08-{i + 1:02d}", "close": 100.0 + i} for i in range(30)
            ],
            "source": "csindex",
            "data_end_date": "2026-08-30",
        },
    )
    monkeypatch.setattr(csindex, "_cached_is_current", lambda _payload: True)
    result = csindex.fetch_csindex_daily_history("930598", trading_days=20)
    assert result is not None
    assert len(result["data"]) == 20
    assert result["data"][-1]["date"] == "2026-08-30"


def test_one_long_capture_serves_both_short_and_long_requests(monkeypatch) -> None:
    """取满长窗口落一次缓存，短窗口从同一份裁，不为每个窗口各打一次。"""
    calls: list[str] = []
    monkeypatch.setattr(csindex.requests, "get", _counting_get(calls, {"code": "200", "data": _csindex_rows(120)}))
    stored: dict[str, dict] = {}
    monkeypatch.setattr(csindex, "_save_cached", lambda code, payload: stored.__setitem__(code, payload))
    monkeypatch.setattr(csindex, "_load_cached", lambda code: stored.get(code))
    monkeypatch.setattr(csindex, "_cached_is_current", lambda _payload: True)

    first = csindex.fetch_csindex_daily_history("930598", trading_days=30)
    second = csindex.fetch_csindex_daily_history("930598", trading_days=100)
    assert len(calls) == 1
    assert first is not None and len(first["data"]) == 30
    assert second is not None and len(second["data"]) == 100
    # 落进缓存的是长窗口原始序列，不是被裁过的视图。
    assert len(stored["930598"]["data"]) == 120


def test_a_failed_attempt_is_not_retried_immediately_for_the_same_code(monkeypatch) -> None:
    """收盘数据发布有延迟，缓存里还没有当日收盘时不能每次调用都白打一次。"""
    calls: list[str] = []
    monkeypatch.setattr(
        csindex.requests, "get", _counting_get(calls, {"code": "200", "data": []})
    )
    assert csindex.fetch_csindex_daily_history("930598", trading_days=40) is None
    assert csindex.fetch_csindex_daily_history("930598", trading_days=40) is None
    assert len(calls) == 1
    # 别的代码不受影响。
    assert csindex.fetch_csindex_daily_history("931582", trading_days=40) is None
    assert len(calls) == 2


def test_min_interval_serializes_live_calls(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(csindex.requests, "get", _counting_get(calls))
    monkeypatch.setattr(csindex, "CSINDEX_MIN_INTERVAL_SECONDS", 0.05)
    slept: list[float] = []
    monkeypatch.setattr(csindex.time, "sleep", lambda seconds: slept.append(seconds))

    csindex.fetch_csindex_daily_history("930598", trading_days=40)
    csindex.fetch_csindex_daily_history("931582", trading_days=40)
    assert len(calls) == 2
    assert slept, "second call must wait for the minimum interval"


def test_a_throttled_miss_is_not_negative_cached_for_an_hour(monkeypatch) -> None:
    """限速跳过是**本次**的事，不能被外层 TTL 冻成一小时"没有数据"。

    否则一次批量刷新耗尽配额后，后续几轮连补齐的机会都没有。
    """
    attempts: list[int] = []

    def flaky(symbol, trading_days=252):
        attempts.append(len(attempts))
        if len(attempts) == 1:
            return None  # 第一次：配额耗尽/熔断中
        return {"data": [{"date": "2026-07-01", "close": 1.0}] * 2, "source": "csindex"}

    monkeypatch.setattr(
        index_client, "_fetch_eastmoney_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(index_client, "_fetch_sina_daily_history", lambda *_a, **_k: None)
    monkeypatch.setattr(index_client, "fetch_csindex_daily_history", flaky)

    assert index_client.fetch_index_daily_history("930598", trading_days=40) is None
    second = index_client.fetch_index_daily_history("930598", trading_days=40)
    assert second is not None and second["source"] == "csindex"
    assert len(attempts) == 2


def test_a_successful_series_is_still_memo_cached(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        index_client, "_fetch_eastmoney_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(index_client, "_fetch_sina_daily_history", lambda *_a, **_k: None)
    monkeypatch.setattr(
        index_client,
        "fetch_csindex_daily_history",
        lambda symbol, trading_days=252: calls.append(symbol)
        or {"data": [{"date": "2026-07-01", "close": 1.0}] * 2, "source": "csindex"},
    )
    index_client.fetch_index_daily_history("930598", trading_days=40)
    index_client.fetch_index_daily_history("930598", trading_days=40)
    assert len(calls) == 1


def test_a_ban_seen_by_another_container_stops_this_one_too(monkeypatch) -> None:
    """限流是按 IP 的，而生产上 api 与 worker 是两个容器共用同一出口 IP。"""
    monkeypatch.setattr(
        csindex.requests,
        "get",
        lambda *_a, **_k: pytest.fail("must not fetch while banned elsewhere"),
    )
    monkeypatch.setattr(csindex, "_durable_breaker_is_open", lambda: True)
    assert csindex.fetch_csindex_daily_history("930598", trading_days=40) is None


def test_opening_the_breaker_publishes_it_for_the_other_containers(monkeypatch) -> None:
    published: list[bool] = []
    monkeypatch.setattr(csindex.requests, "get", _counting_get([], status=403))
    monkeypatch.setattr(csindex, "_publish_durable_breaker", lambda: published.append(True))
    csindex.fetch_csindex_daily_history("930598", trading_days=40)
    assert published == [True]


def test_durable_breaker_payload_is_read_as_an_expiry(monkeypatch) -> None:
    """共享熔断读的是绝对到期时刻，过期的行不该继续拦住请求。"""
    monkeypatch.undo()  # 恢复被 fixture stub 掉的 _durable_breaker_is_open
    csindex.reset_csindex_rate_state()
    payloads: dict[str, object] = {}
    monkeypatch.setattr(
        "app.services.sector_quote_cache.get_spot_snapshot_revalidated",
        lambda key, **_kwargs: payloads.get(key),
    )

    payloads[csindex.CSINDEX_BREAKER_CACHE_KEY] = {
        "blocked_until_epoch": time.time() + 300
    }
    assert csindex._durable_breaker_is_open() is True

    csindex.reset_csindex_rate_state()
    payloads[csindex.CSINDEX_BREAKER_CACHE_KEY] = {
        "blocked_until_epoch": time.time() - 300
    }
    assert csindex._durable_breaker_is_open() is False

    csindex.reset_csindex_rate_state()
    payloads[csindex.CSINDEX_BREAKER_CACHE_KEY] = {"blocked_until_epoch": "junk"}
    assert csindex._durable_breaker_is_open() is False


def test_shared_state_is_read_with_revalidation_not_any_age(monkeypatch) -> None:
    """共享熔断与日线快照都必须走复验读。

    `get_spot_snapshot_any_age` 命中进程内存后就再也不回持久行：熔断会退化成"每进程
    一次性"（别的容器后来开的熔断看不见），日线快照会把昨天的序列钉死、白花本就稀缺的
    请求配额去取别的容器已经写好的数据。这条用例直接钉住"用的是哪个读接口"。
    """
    monkeypatch.undo()
    csindex.reset_csindex_rate_state()
    revalidated: list[str] = []
    monkeypatch.setattr(
        "app.services.sector_quote_cache.get_spot_snapshot_revalidated",
        lambda key, **_kwargs: revalidated.append(key) or None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_cache.get_spot_snapshot_any_age",
        lambda key: pytest.fail(f"{key} must be read with revalidation"),
    )

    assert csindex._load_cached("930598") is None
    assert csindex._durable_breaker_is_open() is False
    assert revalidated == [
        f"{csindex.CSINDEX_CACHE_KEY_PREFIX}:930598",
        csindex.CSINDEX_BREAKER_CACHE_KEY,
    ]
