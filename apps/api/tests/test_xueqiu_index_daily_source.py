"""雪球指数日线源：中证 9xxxxx / H3xxxx 的主源。

回归背景（2026-08-11 从生产服务器逐源实测）：能报**没有交易所行情代码**的中证指数的只有
两家——中证指数公司官网与雪球。东财 kline 全系主机 TCP 断连；新浪与腾讯 gtimg 只认交易所
挂牌代码，对 930598 / 931582 一律 0 行；同花顺 404；网易 502；和讯 DNS 不通。

中证官网有约 40~50 次/小时的配额（超了 403 且粘性，实测 25 分钟才恢复），而雪球 64 个代码
背靠背 2.5s 全部 200、无限流迹象，深度 `count=-1200` 给满 1200 行。两者数值逐位一致
（930598 收盘 2734.867 vs 官方 2734.87，成交量完全相同），所以雪球当主源不是拿精度换配额。

本文件锁三件容易静默出错的事：
1. **时间戳按北京时间解析**。`1786377600000` 按 UTC 是 2026-08-10，按 UTC+8 才是 08-11。
   偏一天不会报错，只会让下游的日期对齐悄悄错位。
2. **按列名取值，不按位置**。对方加一列就会整体错位，而错位的收盘价是静默的错数据。
3. **符号前缀按代码族显式映射**。前缀弄错不会报错，只会返回 0 行。
"""

from __future__ import annotations

import pytest

from app.services import index_daily_client as index_client
from app.services import xueqiu_index_daily_client as xq

_COLUMNS = [
    "timestamp",
    "volume",
    "open",
    "high",
    "low",
    "close",
    "chg",
    "percent",
    "turnoverrate",
    "amount",
    "volume_post",
    "amount_post",
]
# 2026-08-11 北京时间零点。按 UTC 解析会得到 2026-08-10。
_TS_20260811 = 1786377600000
_ONE_DAY_MS = 86_400_000


class _Response:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> object:
        return self._payload


def _payload(count: int = 5, *, columns: list[str] | None = None) -> dict:
    used = columns or _COLUMNS
    items = []
    for index in range(count):
        row_by_name = {
            "timestamp": _TS_20260811 - (count - 1 - index) * _ONE_DAY_MS,
            "volume": 2_413_138_701,
            "open": 2766.8208,
            "high": 2776.9909,
            "low": 2731.2652,
            "close": 2734.867 + index,
            "chg": -69.8725,
            "percent": -2.49,
            "turnoverrate": 0.0,
            "amount": 47_120_296_694.0,
            "volume_post": None,
            "amount_post": None,
        }
        items.append([row_by_name.get(name) for name in used])
    return {"data": {"symbol": "CSI930598", "column": used, "item": items}}


@pytest.fixture(autouse=True)
def _clean_slate(monkeypatch):
    xq.reset_xueqiu_session_state()
    with index_client._INDEX_TTL_CACHE_LOCK:
        index_client._INDEX_TTL_CACHE.clear()
    # 不做真实 cookie 握手。
    monkeypatch.setattr(xq, "_session", lambda: _FakeSession(_payload()))
    yield
    xq.reset_xueqiu_session_state()
    with index_client._INDEX_TTL_CACHE_LOCK:
        index_client._INDEX_TTL_CACHE.clear()


class _FakeSession:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.calls: list[dict] = []

    def get(self, _url, **kwargs):
        self.calls.append(dict(kwargs.get("params") or {}))
        return _Response(self.payload, self.status)


# --- 1. 符号映射 -------------------------------------------------------------


@pytest.mark.parametrize(
    "code, expected",
    [
        ("930598", "CSI930598"),
        ("931582", "CSI931582"),
        ("931672", "CSI931672"),
        ("930792", "CSI930792"),
        ("H30202", "CSIH30202"),
        ("h30054", "CSIH30054"),
        ("H11043", "CSIH11043"),
        ("399989", "SZ399989"),
        ("399262", "SZ399262"),
        ("000300", "SH000300"),
        ("000993", "SH000993"),
        # 无法确定的一律 None，不猜——前缀猜错只会静默返回 0 行。
        ("AU9999", None),
        ("HSTECH", None),
        ("sh600519", None),
        ("90.BK0727", None),
        ("", None),
    ],
)
def test_symbol_mapping_is_explicit_per_code_family(code, expected) -> None:
    assert xq.xueqiu_index_symbol(code) == expected


def test_unmappable_symbols_never_touch_the_network(monkeypatch) -> None:
    monkeypatch.setattr(
        xq, "_session", lambda: pytest.fail("must not request an unmappable symbol")
    )
    assert xq.fetch_xueqiu_index_daily_history("AU9999", trading_days=60) is None


# --- 2. 解析 -----------------------------------------------------------------


def test_timestamps_are_read_in_beijing_time(monkeypatch) -> None:
    """按 UTC 解析会把 2026-08-11 读成 08-10，整条序列偏一天。"""
    session = _FakeSession(_payload(3))
    monkeypatch.setattr(xq, "_session", lambda: session)
    result = xq.fetch_xueqiu_index_daily_history("930598", trading_days=60)
    assert result is not None
    assert [row["date"] for row in result["data"]] == [
        "2026-08-09",
        "2026-08-10",
        "2026-08-11",
    ]


def test_values_are_read_by_column_name_not_position(monkeypatch) -> None:
    """对方加一列时按位置取值会整体错位，而错位的收盘价是静默的错数据。"""
    shuffled = ["timestamp", "close", "open", "high", "low", "volume"]
    session = _FakeSession(_payload(3, columns=shuffled))
    monkeypatch.setattr(xq, "_session", lambda: session)
    result = xq.fetch_xueqiu_index_daily_history("930598", trading_days=60)
    assert result is not None
    assert result["data"][0]["close"] == 2734.867
    assert result["data"][0]["open"] == 2766.8208


def test_missing_required_columns_fail_closed(monkeypatch) -> None:
    session = _FakeSession(_payload(3, columns=["timestamp", "open", "high"]))
    monkeypatch.setattr(xq, "_session", lambda: session)
    assert xq.fetch_xueqiu_index_daily_history("930598", trading_days=60) is None


def test_shape_matches_the_shared_index_history_contract(monkeypatch) -> None:
    session = _FakeSession(_payload(5))
    monkeypatch.setattr(xq, "_session", lambda: session)
    result = xq.fetch_xueqiu_index_daily_history("930598", trading_days=60)
    assert result is not None
    assert result["source"] == "xueqiu"
    assert set(result["data"][0]) == {"date", "close", "open", "high", "low"}
    assert [row["date"] for row in result["data"]] == sorted(
        row["date"] for row in result["data"]
    )


def test_requested_days_are_passed_through_and_trimmed(monkeypatch) -> None:
    session = _FakeSession(_payload(40))
    monkeypatch.setattr(xq, "_session", lambda: session)
    result = xq.fetch_xueqiu_index_daily_history("930598", trading_days=25)
    assert result is not None
    assert len(result["data"]) == 25
    assert session.calls[0]["count"] == -25
    assert session.calls[0]["period"] == "day"


# --- 3. 失败处理 -------------------------------------------------------------


def test_an_empty_result_does_not_open_the_breaker(monkeypatch) -> None:
    """雪球没有这个冷门代码 ≠ 这一路坏了；一个冷门代码不能关掉整条源。"""
    session = _FakeSession({"data": {"column": _COLUMNS, "item": []}})
    monkeypatch.setattr(xq, "_session", lambda: session)
    for _ in range(xq.XUEQIU_FAILURE_THRESHOLD + 2):
        assert xq.fetch_xueqiu_index_daily_history("930598", trading_days=60) is None
    assert xq.xueqiu_blocked_seconds_remaining() == 0


def test_a_bad_status_retries_once_with_a_fresh_session(monkeypatch) -> None:
    """cookie 过期表现为 4xx；先重建会话再试一次，而不是直接放弃。"""
    sessions: list[_FakeSession] = []

    def make():
        session = _FakeSession(_payload(3), status=200 if sessions else 403)
        sessions.append(session)
        return session

    monkeypatch.setattr(xq, "_session", make)
    result = xq.fetch_xueqiu_index_daily_history("930598", trading_days=60)
    assert result is not None
    assert len(sessions) == 2


def test_repeated_hard_failures_open_the_breaker(monkeypatch) -> None:
    session = _FakeSession({"error_description": "nope"}, status=403)
    monkeypatch.setattr(xq, "_session", lambda: session)
    for _ in range(xq.XUEQIU_FAILURE_THRESHOLD):
        assert xq.fetch_xueqiu_index_daily_history("930598", trading_days=60) is None
    assert xq.xueqiu_blocked_seconds_remaining() > 0
    # 熔断后一次请求都不再发。
    before = len(session.calls)
    assert xq.fetch_xueqiu_index_daily_history("931582", trading_days=60) is None
    assert len(session.calls) == before


# --- 4. 在 index_daily_client 里的位置 --------------------------------------


def test_xueqiu_sits_ahead_of_the_quota_limited_official_endpoint(monkeypatch) -> None:
    """两者数值一致，所以优先用没有配额的那一路，把权威的留作兜底。"""
    monkeypatch.setattr(
        index_client, "_fetch_eastmoney_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(index_client, "_fetch_sina_daily_history", lambda *_a, **_k: None)
    monkeypatch.setattr(
        index_client,
        "fetch_xueqiu_index_daily_history",
        lambda symbol, trading_days=252: {
            "data": [{"date": "2026-08-11", "close": 1.0}] * 2,
            "source": "xueqiu",
        },
    )
    monkeypatch.setattr(
        index_client,
        "fetch_csindex_daily_history",
        lambda *_a, **_k: pytest.fail("csindex quota must not be spent when xueqiu answers"),
    )
    result = index_client.fetch_index_daily_history("930598", trading_days=60)
    assert result is not None and result["source"] == "xueqiu"


def test_csindex_still_backs_xueqiu_up(monkeypatch) -> None:
    """雪球要 cookie 握手、是聚合站；它变了以后链路不能整条断掉。"""
    monkeypatch.setattr(
        index_client, "_fetch_eastmoney_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(index_client, "_fetch_sina_daily_history", lambda *_a, **_k: None)
    monkeypatch.setattr(
        index_client, "fetch_xueqiu_index_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        index_client,
        "fetch_csindex_daily_history",
        lambda symbol, trading_days=252: {
            "data": [{"date": "2026-08-11", "close": 1.0}] * 2,
            "source": "csindex",
        },
    )
    result = index_client.fetch_index_daily_history("930598", trading_days=60)
    assert result is not None and result["source"] == "csindex"


def test_sina_still_wins_for_exchange_listed_codes(monkeypatch) -> None:
    """新浪能报的不必绕道第三方。"""
    monkeypatch.setattr(
        index_client, "_fetch_eastmoney_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        index_client,
        "_fetch_sina_daily_history",
        lambda *_a, **_k: {"data": [{"date": "2026-08-11", "close": 1.0}], "source": "sina"},
    )
    monkeypatch.setattr(
        index_client,
        "fetch_xueqiu_index_daily_history",
        lambda *_a, **_k: pytest.fail("xueqiu must not be reached when sina answers"),
    )
    result = index_client.fetch_index_daily_history("399989", trading_days=60)
    assert result is not None and result["source"] == "sina"


def test_a_xueqiu_cooldown_is_not_negative_cached_for_an_hour(monkeypatch) -> None:
    """熔断是**本次**的事，不能被外层 TTL 冻成一小时"没有数据"。"""
    monkeypatch.setattr(
        index_client, "_fetch_eastmoney_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(index_client, "_fetch_sina_daily_history", lambda *_a, **_k: None)
    monkeypatch.setattr(
        index_client, "fetch_xueqiu_index_daily_history", lambda *_a, **_k: None
    )
    monkeypatch.setattr(index_client, "is_csindex_code", lambda _symbol: False)
    monkeypatch.setattr(index_client, "xueqiu_blocked_seconds_remaining", lambda: 120.0)

    attempts: list[int] = []
    monkeypatch.setattr(
        index_client,
        "_fetch_sina_daily_history",
        lambda *_a, **_k: attempts.append(1) or None,
    )
    assert index_client.fetch_index_daily_history("399989", trading_days=60) is None
    assert index_client.fetch_index_daily_history("399989", trading_days=60) is None
    assert len(attempts) == 2
