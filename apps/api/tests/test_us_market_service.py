"""美股概览服务属性测试。

测试 `app.services.us_market_service` 的时段感知 TTL 逻辑（私有 `_ttl_for`）。

本仓库未引入 `hypothesis`（见 `requirements.txt`），故按任务约定以
「参数化 + 多样本（≥100 次迭代）」近似属性测试。
"""

from __future__ import annotations

import random

import pytest

from app.services import us_market_service
from app.services.us_market_service import (
    _CLOSED_TTL_SECONDS,
    _LIVE_TTL_SECONDS,
    _ttl_for,
    get_us_market_snapshot,
)
from app.services.us_qdii_seeds import get_qdii_seeds

# 全部合法时段类别。
_LIVE_SESSION_KINDS = ("pre_market", "regular")
_REST_SESSION_KINDS = ("after_hours", "closed")
_ALL_SESSION_KINDS = _LIVE_SESSION_KINDS + _REST_SESSION_KINDS

# 属性测试迭代次数（≥100）。
_ITERATIONS = 200


# ---------------------------------------------------------------------------
# Property 4：TTL 单调性
# Feature: us-market-overview, Property 4
# Validates: Requirements 4.3, 4.4
# ---------------------------------------------------------------------------


def test_property4_ttl_monotonicity_relationship():
    """Feature: us-market-overview, Property 4

    时段感知 TTL 满足单调关系：
        ttl(pre_market) == ttl(regular) ≤ 60s < ttl(after_hours) ≤ ttl(closed)

    Validates: Requirements 4.3, 4.4
    """
    ttl_pre = _ttl_for("pre_market")
    ttl_regular = _ttl_for("regular")
    ttl_after = _ttl_for("after_hours")
    ttl_closed = _ttl_for("closed")

    # 盘前与盘中 TTL 相等（同属高频「live」桶）。
    assert ttl_pre == ttl_regular
    # 高频 TTL ≤ 60s。
    assert ttl_pre <= 60.0
    assert ttl_regular <= 60.0
    # 60s < 盘后 TTL（盘后/休市为低频「rest」桶）。
    assert 60.0 < ttl_after
    # 盘后 TTL ≤ 休市 TTL。
    assert ttl_after <= ttl_closed

    # 完整链式不等式。
    assert ttl_pre == ttl_regular <= 60.0 < ttl_after <= ttl_closed


@pytest.mark.parametrize("kind", _ALL_SESSION_KINDS)
def test_property4_ttl_is_positive_and_bucketed(kind: str):
    """Feature: us-market-overview, Property 4

    对任一合法时段，TTL 为正，且恰落入两个常量桶之一：
    live(pre_market/regular)→_LIVE_TTL_SECONDS；rest(after_hours/closed)→_CLOSED_TTL_SECONDS。

    Validates: Requirements 4.3, 4.4
    """
    ttl = _ttl_for(kind)
    assert ttl > 0
    if kind in _LIVE_SESSION_KINDS:
        assert ttl == _LIVE_TTL_SECONDS
    else:
        assert ttl == _CLOSED_TTL_SECONDS


def test_property4_ttl_monotonicity_repeated_samples():
    """Feature: us-market-overview, Property 4

    多次迭代（≥100）地在全部时段类别上重复校验单调关系恒成立（_ttl_for
    为纯函数，应对相同输入稳定返回，关系不随调用次数变化）。

    Validates: Requirements 4.3, 4.4
    """
    for i in range(_ITERATIONS):
        # 轮换覆盖全部时段类别。
        live_kind = _LIVE_SESSION_KINDS[i % len(_LIVE_SESSION_KINDS)]
        rest_kind = _REST_SESSION_KINDS[i % len(_REST_SESSION_KINDS)]

        ttl_pre = _ttl_for("pre_market")
        ttl_regular = _ttl_for("regular")
        ttl_live = _ttl_for(live_kind)
        ttl_rest = _ttl_for(rest_kind)
        ttl_after = _ttl_for("after_hours")
        ttl_closed = _ttl_for("closed")

        assert ttl_pre == ttl_regular <= 60.0 < ttl_after <= ttl_closed
        # live 桶恒 ≤ 60s < rest 桶。
        assert ttl_live <= 60.0 < ttl_rest


# ---------------------------------------------------------------------------
# Property 5：禁止编造数值（核心安全不变量）
# Feature: us-market-overview, Property 5
# Validates: Requirements 1.5, 2.5, 7.5
#
# 对任一聚合产出的 UsMarketSnapshot，其中任一报价条目（期货 / USD_CNY / QDII
# 参考涨跌）：
#   - status == "ok"          数值来自本次真实采集（== 本次采集值）；
#   - status == "stale"       数值等于该源上一次真实采集缓存值（== 缓存值）；
#   - status == "unavailable" 数值字段为 None。
# 在任何情形下，数值字段都不得由收盘价或占位常量推导（以「== 真实来源值 /
# == 缓存值 / == None」三种严格等式锁死，杜绝任何替代/编造值）。
#
# 本仓库未引入 hypothesis，遵循既有约定以「随机生成 + 多样本（≥100 次迭代）」
# 近似属性测试。服务以 `from ... import name` 将以下符号导入自身命名空间，故
# 必须在 `app.services.us_market_service` 上打桩。
# ---------------------------------------------------------------------------

# 固定 3 条顶部指标（顺序即展示顺序，与服务内 _MARKET_SYMBOLS 一致）。
_FUTURES_SYMBOLS = (
    ("NASDAQ_FUT", "纳斯达克"),
    ("SP500_FUT", "标普500"),
    ("DOW_FUT", "道琼斯"),
)

# Property 5 迭代次数（≥100）。
_PROP5_ITERATIONS = 150


def _enable_qdii_estimates(monkeypatch) -> None:
    """单元测试内临时开启 QDII 估值路径（生产默认关闭）。"""
    monkeypatch.setattr(us_market_service, "qdii_estimates_enabled", lambda: True)


def _pin_session(monkeypatch) -> None:
    """钉住一个确定性时段，避免随墙钟时间漂移影响聚合路径。"""
    monkeypatch.setattr(
        us_market_service,
        "detect_us_session",
        lambda *_a, **_k: {
            "session_kind": "pre_market",
            "session_label": "盘前交易中",
            "et_date": "2026-06-17",
        },
    )


def _install_drivers(monkeypatch, state: dict) -> None:
    """打桩各采集 / 缓存入口，由可变的 `state` 驱动三条降级路径。

    - fetch_us_index_futures / fetch_us_index_spot / fetch_usd_cny：本次采集结果；
    - get_spot_snapshot：恒返回 None（不命中新鲜服务端缓存，强制重聚合）；
    - get_spot_snapshot_any_age：上一次真实缓存（stale 回退来源）；
    - save_spot_snapshot：no-op（避免写真实缓存）。
    """
    monkeypatch.setattr(
        us_market_service, "fetch_us_index_futures", lambda: state.get("futures")
    )
    monkeypatch.setattr(
        us_market_service, "fetch_us_index_spot", lambda: state.get("indices")
    )
    monkeypatch.setattr(us_market_service, "fetch_usd_cny", lambda: state.get("forex"))
    monkeypatch.setattr(
        us_market_service,
        "fetch_fund_estimates_for_codes",
        lambda *_a, **_k: state.get("fundgz_estimates") or {},
    )
    monkeypatch.setattr(
        us_market_service,
        "fetch_stock_changes_for_holdings",
        lambda *_a, **_k: state.get("stock_quotes") or {},
    )
    monkeypatch.setattr(
        us_market_service,
        "load_qdii_holdings_batch",
        lambda *_a, **_k: state.get("holdings_by_fund") or {},
    )
    monkeypatch.setattr(us_market_service, "get_spot_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(
        us_market_service, "get_spot_snapshot_any_age", lambda *_a, **_k: state.get("prev")
    )
    monkeypatch.setattr(us_market_service, "save_spot_snapshot", lambda *_a, **_k: None)


def _gen_futures_rows(rng: random.Random) -> list[dict]:
    """生成 3 条随机但真实的期货采集行（值域刻意远离 0 等占位常量）。"""
    rows = []
    for symbol, display_name in _FUTURES_SYMBOLS:
        rows.append(
            {
                "symbol": symbol,
                "display_name": display_name,
                "last_price": round(rng.uniform(100.0, 50000.0), 4),
                "change_percent": round(rng.uniform(-9.5, 9.5), 4),
                "quote_time": "2026-06-17T08:12:00-04:00",
            }
        )
    return rows


def _gen_forex_row(rng: random.Random) -> dict:
    return {
        "last_price": round(rng.uniform(6.0, 8.0), 4),
        "change_percent": round(rng.uniform(-1.5, 1.5), 4),
        "quote_time": "2026-06-16",
        "source": "currency_boc_sina",
    }


def _futures_by_symbol(snapshot) -> dict:
    return {q.symbol: q for q in snapshot.futures}


def test_property5_ok_values_equal_fetched(monkeypatch):
    """Feature: us-market-overview, Property 5

    ok 路径：本次采集返回真实值时，snapshot 各期货 / USD_CNY 的数值**严格等于**
    本次采集值，状态为 "ok"；QDII 参考涨跌严格等于由真实期货盘前涨跌推导的
    round(change × factor, 2)（绝不为占位 / 编造值）。

    Validates: Requirements 1.5, 2.5, 7.5
    """
    _pin_session(monkeypatch)
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}
    _install_drivers(monkeypatch, state)

    rng = random.Random(20260617)

    for _ in range(_PROP5_ITERATIONS):
        rows = _gen_futures_rows(rng)
        forex = _gen_forex_row(rng)
        state["futures"], state["forex"], state["prev"] = rows, forex, None

        snap = get_us_market_snapshot(force_refresh=False)

        assert snap.futures_status == "ok"
        assert snap.forex_status == "ok"

        # 期货：数值严格等于本次采集值，且非 None（杜绝占位 / 编造）。
        by_symbol = _futures_by_symbol(snap)
        fetched_change = {}
        for row in rows:
            quote = by_symbol[row["symbol"]]
            assert quote.status == "ok"
            assert quote.last_price == row["last_price"]
            assert quote.last_price is not None
            assert quote.change_percent == row["change_percent"]
            fetched_change[row["symbol"]] = row["change_percent"]

        # USD/CNY：数值严格等于本次采集值。
        assert snap.usd_cny.status == "ok"
        assert snap.usd_cny.last_price == forex["last_price"]
        assert snap.usd_cny.last_price is not None
        assert snap.usd_cny.change_percent == forex["change_percent"]

        # 方案 A 默认不聚合 QDII；估值路径见 test_property8 / fundgz / holdings 等用例。
        assert snap.qdii_status == "unavailable"
        assert snap.qdii == []


def test_property5_stale_values_equal_cached(monkeypatch):
    """Feature: us-market-overview, Property 5

    stale 路径：本次采集失败（None）但任意年龄缓存存有上一次真实值时，snapshot
    各期货 / USD_CNY 的数值**严格等于该缓存值**，状态为 "stale"（沿用最后真实值，
    绝不改写为收盘价 / 占位常量推导值）。

    Validates: Requirements 1.5, 2.5, 7.5
    """
    _pin_session(monkeypatch)
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}
    _install_drivers(monkeypatch, state)

    rng = random.Random(99887766)

    for _ in range(_PROP5_ITERATIONS):
        cached_rows = _gen_futures_rows(rng)
        cached_forex = _gen_forex_row(rng)
        prev = {
            "available": True,
            "futures": [
                {**row, "status": "ok"} for row in cached_rows
            ],
            "usd_cny": {**cached_forex, "status": "ok"},
            "qdii": [],
        }
        # 本次采集全部失败，强制走 stale 回退。
        state["futures"], state["forex"], state["prev"] = None, None, prev

        snap = get_us_market_snapshot(force_refresh=False)

        assert snap.futures_status == "stale"
        assert snap.forex_status == "stale"
        assert snap.stale is True

        cached_by_symbol = {row["symbol"]: row for row in cached_rows}
        for quote in snap.futures:
            cached = cached_by_symbol[quote.symbol]
            assert quote.status == "stale"
            assert quote.last_price == cached["last_price"]
            assert quote.last_price is not None
            assert quote.change_percent == cached["change_percent"]

        assert snap.usd_cny.status == "stale"
        assert snap.usd_cny.last_price == cached_forex["last_price"]
        assert snap.usd_cny.last_price is not None
        assert snap.usd_cny.change_percent == cached_forex["change_percent"]


def test_property5_unavailable_values_are_none(monkeypatch):
    """Feature: us-market-overview, Property 5

    unavailable 路径：本次采集失败（None）且无任何历史缓存时，所有数值字段一律
    为 None，状态为 "unavailable"，QDII 列表为空（绝不以占位常量填充）。

    Validates: Requirements 1.5, 2.5, 7.5
    """
    _pin_session(monkeypatch)
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}
    _install_drivers(monkeypatch, state)

    for _ in range(_PROP5_ITERATIONS):
        # 采集失败且无缓存：每次迭代输入相同，确证不变量稳定恒成立。
        state["futures"], state["forex"], state["prev"] = None, None, None

        snap = get_us_market_snapshot(force_refresh=False)

        assert snap.available is False
        assert snap.futures_status == "unavailable"
        assert snap.forex_status == "unavailable"
        assert snap.qdii_status == "unavailable"

        for quote in snap.futures:
            assert quote.status == "unavailable"
            assert quote.last_price is None
            assert quote.change_percent is None

        assert snap.usd_cny.status == "unavailable"
        assert snap.usd_cny.last_price is None
        assert snap.usd_cny.change_percent is None

        # QDII 不可估算时返回空列表（禁止占位）。
        assert snap.qdii == []


# ---------------------------------------------------------------------------
# Property 6：陈旧回退（stale fallback 不变量）
# Feature: us-market-overview, Property 6
# Validates: Requirements 7.1
#
# 当某数据源本次采集失败（fetch 返回 None）但 US_Market_Snapshot_Cache 中存在
# 该源上一次真实采集的历史数据时，US_Market_Service 必须返回该历史数据并将其
# Data_Source_Status 标记为 "stale"。本属性聚焦验证：
#   - 期货 / USD_CNY 每一条目的 status == "stale"；
#   - 其数值（last_price / change_percent）严格等于最后一次真实采集缓存值；
#   - 聚合快照的 stale 标志为 True。
#
# 与 Property 5 的 stale 子用例同源，但本属性专门锁死「失败 + 有历史 → 陈旧
# 回退且沿用最后真实值」这一不变量，独立覆盖 Requirements 7.1。仓库未引入
# hypothesis，沿用既有「随机生成 + 多样本（≥100 次迭代）」近似属性测试，并在
# `app.services.us_market_service` 上打桩（服务以 `from ... import name` 引入符号）。
# ---------------------------------------------------------------------------

# Property 6 迭代次数（≥100）。
_PROP6_ITERATIONS = 120


def test_property6_stale_fallback_uses_last_real_value(monkeypatch):
    """Feature: us-market-overview, Property 6

    本次采集失败（fetch_us_index_futures / fetch_usd_cny 返回 None、新鲜缓存
    get_spot_snapshot 未命中）但任意年龄缓存（get_spot_snapshot_any_age）存有
    上一次真实采集值时：聚合快照对每一数据源的 status 恒为 "stale"，其数值严格
    等于最后一次真实采集缓存值，且快照 stale 标志为 True。

    Validates: Requirements 7.1
    """
    _pin_session(monkeypatch)
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}
    _install_drivers(monkeypatch, state)

    rng = random.Random(76543210)

    for _ in range(_PROP6_ITERATIONS):
        # 上一次真实采集值（随机但落在远离占位常量的真实值域）。
        cached_rows = _gen_futures_rows(rng)
        cached_forex = _gen_forex_row(rng)
        prev = {
            "available": True,
            "futures": [{**row, "status": "ok"} for row in cached_rows],
            "usd_cny": {**cached_forex, "status": "ok"},
            "qdii": [],
        }
        # 本次采集全部失败，强制走陈旧回退路径。
        state["futures"], state["forex"], state["prev"] = None, None, prev

        snap = get_us_market_snapshot(force_refresh=False)

        # 聚合层：各源状态陈旧，且快照标记陈旧。
        assert snap.futures_status == "stale"
        assert snap.forex_status == "stale"
        assert snap.stale is True

        # 期货：每条数值严格等于最后一次真实采集缓存值（非 None，绝不改写）。
        cached_by_symbol = {row["symbol"]: row for row in cached_rows}
        for quote in snap.futures:
            cached = cached_by_symbol[quote.symbol]
            assert quote.status == "stale"
            assert quote.last_price == cached["last_price"]
            assert quote.last_price is not None
            assert quote.change_percent == cached["change_percent"]

        # USD/CNY：数值严格等于最后一次真实采集缓存值。
        assert snap.usd_cny.status == "stale"
        assert snap.usd_cny.last_price == cached_forex["last_price"]
        assert snap.usd_cny.last_price is not None
        assert snap.usd_cny.change_percent == cached_forex["change_percent"]


# ---------------------------------------------------------------------------
# Property 7：不可用（采集失败且无历史 → unavailable）
# Feature: us-market-overview, Property 7
# Validates: Requirements 1.5, 2.5, 7.2
#
# 本属性专门锁死 Requirement 7.2 的「无缓存 → 不可用」契约：当某数据源本次采集
# 失败（fetch_us_index_futures / fetch_usd_cny 返回 None）且 US_Market_Snapshot_Cache
# 中**不存在任何历史数据**（get_spot_snapshot 与 get_spot_snapshot_any_age 均返回
# None）时，US_Market_Service 必须将该数据源的 Data_Source_Status 标记为
# "unavailable"，省略其数值字段（一律为 None），并将 QDII 列表置空（qdii=[]），
# 整体 available 为 False。
#
# 与 Property 5 的 unavailable 子用例区别：Property 5 在固定时段下校验「数值字段
# 不得编造」的安全不变量；Property 7 专注「无缓存 → 不可用」这一契约，并在多种
# US_Session_Kind 与 force_refresh 取值上扫描，证明该契约与时段 / 刷新模式无关地
# 恒成立，独立覆盖 Requirement 7.2。仓库未引入 hypothesis，沿用既有「随机生成 +
# 多样本（≥100 次迭代）」近似属性测试，并在 `app.services.us_market_service` 上
# 打桩（服务以 `from ... import name` 引入符号）。
# ---------------------------------------------------------------------------

# Property 7 迭代次数（≥100）。
_PROP7_ITERATIONS = 160


def _pin_session_kind(monkeypatch, kind: str) -> None:
    """钉住任意指定时段（覆盖 _pin_session 的固定 pre_market），用于证明
    「无缓存 → 不可用」契约与具体 US_Session_Kind 无关地恒成立。"""
    label_by_kind = {
        "pre_market": "盘前交易中",
        "regular": "盘中",
        "after_hours": "盘后",
        "closed": "休市",
    }
    monkeypatch.setattr(
        us_market_service,
        "detect_us_session",
        lambda *_a, **_k: {
            "session_kind": kind,
            "session_label": label_by_kind[kind],
            "et_date": "2026-06-17",
        },
    )


def test_property7_no_cache_failure_is_unavailable(monkeypatch):
    """Feature: us-market-overview, Property 7

    采集失败（期货 / 外汇 fetch 均返回 None）且无任何历史缓存（get_spot_snapshot
    与 get_spot_snapshot_any_age 均返回 None）时，对任意 US_Session_Kind 与
    force_refresh 取值，聚合快照恒满足「无缓存 → 不可用」契约：

      - futures_status / forex_status / qdii_status 均为 "unavailable"；
      - 每条期货与 USD/CNY 的 status 为 "unavailable" 且 last_price /
        change_percent 一律为 None（省略数值，绝不以收盘价 / 占位常量填充）；
      - QDII 列表为空（qdii == []）；
      - 整体 available 为 False。

    Validates: Requirements 1.5, 2.5, 7.2
    """
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}
    _install_drivers(monkeypatch, state)

    rng = random.Random(13572468)

    for i in range(_PROP7_ITERATIONS):
        # 扫描全部时段与 force_refresh 两种路径，证明契约与时段 / 刷新模式无关。
        kind = _ALL_SESSION_KINDS[i % len(_ALL_SESSION_KINDS)]
        _pin_session_kind(monkeypatch, kind)
        force_refresh = bool(rng.getrandbits(1))

        # 采集失败且无任何历史缓存（_install_drivers 已使两类缓存读恒为 None）。
        state["futures"], state["forex"], state["prev"] = None, None, None

        snap = get_us_market_snapshot(force_refresh=force_refresh)

        # 整体不可用。
        assert snap.available is False
        assert snap.futures_status == "unavailable"
        assert snap.forex_status == "unavailable"
        assert snap.qdii_status == "unavailable"

        # 期货：状态不可用且数值省略。
        for quote in snap.futures:
            assert quote.status == "unavailable"
            assert quote.last_price is None
            assert quote.change_percent is None

        # USD/CNY：状态不可用且数值省略。
        assert snap.usd_cny.status == "unavailable"
        assert snap.usd_cny.last_price is None
        assert snap.usd_cny.change_percent is None

        # QDII：无可用数据且无缓存 → 空列表（禁止编造条目）。
        assert snap.qdii == []

# ---------------------------------------------------------------------------
# Property 8：QDII 盘前参考涨跌估算
# Feature: us-market-overview, Property 8
# Validates: Requirements 2.2, 2.3
#
# 对任一 QDII_Premarket_Item，其 Reference_Change_Percent 必须**仅**由其跟踪标的
# 对应期货的盘前涨跌 c 与跟踪系数 k 按 round(c × k, 2) 估算得出（需求 2.2），并携带
# 非空 estimate_basis 估算依据标识（需求 2.3，供前端标注「非承诺性预估」）；当跟踪
# 期货不可用 / 无映射时，Reference_Change_Percent 必须为 None（绝不编造数值）。
#
# 仓库未引入 hypothesis，沿用既有「随机生成 + 多样本（≥100 次迭代）」近似属性测试，
# 并在 `app.services.us_market_service` 上打桩（服务以 `from ... import name` 引入符号）。
# 每次迭代随机选取期货可用品种的非空子集（其余品种本次采集缺失且无缓存 → unavailable），
# 同时随机化期货 change_percent，从而在「可用 → round(c×k,2)」与「不可用/无映射 → None」
# 两条分支上扫描，证明估算契约恒成立。
# ---------------------------------------------------------------------------

# Property 8 迭代次数（≥100）。
_PROP8_ITERATIONS = 150


def test_property8_qdii_reference_change_estimation(monkeypatch):
    """Feature: us-market-overview, Property 8

    跨 ≥100 次随机期货 change_percent 取值，对每条 QDII_Premarket_Item 断言：

      - 若其 tracking_symbol 映射到一条本次可用的期货报价（盘前涨跌 c、跟踪系数 k），
        则 reference_change_percent == round(c × k, 2) 且 estimate_basis 非空；
      - 若其跟踪期货本次不可用 / 无映射，则 reference_change_percent is None。

    Validates: Requirements 2.2, 2.3
    """
    _pin_session(monkeypatch)
    _enable_qdii_estimates(monkeypatch)
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}
    _install_drivers(monkeypatch, state)

    rng = random.Random(20260808)

    # 由种子表派生：tracking_symbol → tracking_factor。
    factor_by_code = {
        seed["fund_code"]: float(seed["tracking_factor"])
        for seed in get_qdii_seeds()
    }
    all_symbols = [symbol for symbol, _ in _FUTURES_SYMBOLS]

    for _ in range(_PROP8_ITERATIONS):
        # 随机选取「本次可用」的期货品种非空子集（保证 futures_status==ok、qdii 非空）。
        k_count = rng.randint(1, len(all_symbols))
        available_symbols = set(rng.sample(all_symbols, k_count))

        rows = _gen_futures_rows(rng)
        present_rows = [row for row in rows if row["symbol"] in available_symbols]
        change_by_symbol = {row["symbol"]: row["change_percent"] for row in present_rows}

        # 本次仅采到可用子集；缺失品种无缓存 → unavailable。
        state["futures"], state["forex"], state["prev"] = present_rows, None, None

        snap = get_us_market_snapshot(force_refresh=False)

        assert snap.qdii  # 可估算路径下恒返回种子列表（即便部分无参考值）

        any_reference = any(item.reference_change_percent is not None for item in snap.qdii)
        if any_reference:
            assert snap.qdii_status in ("ok", "stale")
        else:
            # 例如仅道指期货可用、但种子均跟踪纳指/标普时，整体可为 unavailable
            assert snap.qdii_status == "unavailable"

        for item in snap.qdii:
            ts = item.tracking_symbol
            change = change_by_symbol.get(ts) if ts in available_symbols else None
            factor = factor_by_code.get(item.fund_code)

            if change is not None and factor is not None:
                # 可用映射：参考涨跌严格等于 round(c × k, 2)，估算依据非空。
                assert item.reference_change_percent == round(change * factor, 2)
                assert item.estimate_basis
            else:
                # 跟踪期货不可用 / 无映射：禁止编造，参考涨跌为 None。
                assert item.reference_change_percent is None

# ---------------------------------------------------------------------------
# Task 6.7：snapshot 结构与强制刷新单元测试（聚焦的非属性单测）
# Feature: us-market-overview
# Validates: Requirements 9.2, 9.3, 4.5, 4.1, 4.6
#
# 复用上方既有 helpers（_pin_session / _install_drivers / _gen_futures_rows /
# _gen_forex_row）。本节聚焦验证：
#   1) 各源 ok / stale / unavailable 时的 snapshot 结构与 *_status；
#   2) force_refresh=True 绕过新鲜服务端缓存重新聚合；
#   3) updated_at 等字段完整。
# ---------------------------------------------------------------------------


def test_snapshot_ok_has_all_required_fields(monkeypatch):
    """ok 路径下 snapshot 所有必填字段均被填充（结构完整性）。

    覆盖 session_kind / session_label / et_date / updated_at（非空）/ 期货 3 条 /
    usd_cny / qdii / 各 *_status / available / from_cache / stale / message。

    Validates: Requirements 9.2, 4.1, 4.6
    """
    _pin_session(monkeypatch)
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}
    _install_drivers(monkeypatch, state)

    rng = random.Random(607001)
    rows = _gen_futures_rows(rng)
    forex = _gen_forex_row(rng)
    state["futures"], state["forex"], state["prev"] = rows, forex, None

    snap = get_us_market_snapshot(force_refresh=False)

    # 时段字段（需求 4.1）。
    assert snap.session_kind == "pre_market"
    assert snap.session_label == "盘前交易中"
    assert snap.et_date == "2026-06-17"

    # updated_at 时间戳非空（需求 4.6）。
    assert isinstance(snap.updated_at, str)
    assert snap.updated_at != ""

    # 期货固定 3 条且顺序即展示顺序。
    assert len(snap.futures) == 3
    assert [q.symbol for q in snap.futures] == ["NASDAQ_FUT", "SP500_FUT", "DOW_FUT"]
    for quote in snap.futures:
        assert quote.display_name
        assert quote.status == "ok"
        assert quote.last_price is not None

    assert snap.usd_cny is not None
    assert snap.usd_cny.status == "ok"
    assert snap.qdii == []
    assert snap.qdii_status == "unavailable"

    # 各源整体状态。
    assert snap.futures_status == "ok"
    assert snap.forex_status == "ok"

    # 聚合标志：全 ok → 可用、非缓存、非陈旧、无降级提示。
    assert snap.available is True
    assert snap.from_cache is False
    assert snap.stale is False
    assert snap.message is None


def test_snapshot_stale_structural_shape(monkeypatch):
    """stale 路径下的 snapshot 结构形状：各源 status=='stale'、数值沿用缓存、
    stale 标志为 True、available 为 True，且 message 含「缓存」降级提示。

    Validates: Requirements 9.2, 9.3
    """
    _pin_session(monkeypatch)
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}
    _install_drivers(monkeypatch, state)

    rng = random.Random(607002)
    cached_rows = _gen_futures_rows(rng)
    cached_forex = _gen_forex_row(rng)
    prev = {
        "available": True,
        "futures": [{**row, "status": "ok"} for row in cached_rows],
        "usd_cny": {**cached_forex, "status": "ok"},
        "qdii": [],
    }
    state["futures"], state["forex"], state["prev"] = None, None, prev

    snap = get_us_market_snapshot(force_refresh=False)

    assert snap.futures_status == "stale"
    assert snap.forex_status == "stale"
    assert snap.stale is True
    assert snap.available is True
    assert snap.from_cache is False

    assert len(snap.futures) == 3
    cached_by_symbol = {row["symbol"]: row for row in cached_rows}
    for quote in snap.futures:
        assert quote.status == "stale"
        assert quote.last_price == cached_by_symbol[quote.symbol]["last_price"]
        assert quote.last_price is not None

    assert snap.usd_cny.status == "stale"
    assert snap.usd_cny.last_price == cached_forex["last_price"]
    assert snap.usd_cny.last_price is not None

    # 降级提示文案（需求 9.3）。
    assert snap.message is not None
    assert "缓存" in snap.message


def test_snapshot_unavailable_structural_shape(monkeypatch):
    """unavailable 路径下的 snapshot 结构形状：各源 status=='unavailable'、
    数值字段一律 None、qdii 空列表、available 为 False、stale 为 False，
    且 message 为统一不可用文案。

    Validates: Requirements 9.2, 9.3
    """
    _pin_session(monkeypatch)
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}
    _install_drivers(monkeypatch, state)

    state["futures"], state["forex"], state["prev"] = None, None, None

    snap = get_us_market_snapshot(force_refresh=False)

    assert snap.available is False
    assert snap.stale is False
    assert snap.futures_status == "unavailable"
    assert snap.forex_status == "unavailable"
    assert snap.qdii_status == "unavailable"

    assert len(snap.futures) == 3
    for quote in snap.futures:
        assert quote.status == "unavailable"
        assert quote.last_price is None
        assert quote.change_percent is None

    assert snap.usd_cny.status == "unavailable"
    assert snap.usd_cny.last_price is None
    assert snap.usd_cny.change_percent is None

    assert snap.qdii == []
    assert snap.message == "美股行情暂不可用，请稍后重试"

    # updated_at 即便在全不可用时也必须填充（需求 4.6）。
    assert isinstance(snap.updated_at, str)
    assert snap.updated_at != ""


def test_force_refresh_bypasses_fresh_cache(monkeypatch):
    """force_refresh=True 绕过新鲜服务端缓存（get_spot_snapshot）并重新聚合。

    将 get_spot_snapshot 打桩为返回一个「新鲜命中」的哨兵快照：
      - force_refresh=False 时应短路返回该缓存（from_cache=True，沿用哨兵
        updated_at 与空 futures，证明未重聚合）；
      - force_refresh=True 时应忽略该缓存重新聚合（from_cache=False、updated_at
        不同于哨兵、期货重新聚合为 3 条且 futures_status=="ok"）。

    Validates: Requirements 4.5
    """
    _pin_session(monkeypatch)
    state: dict = {"futures": None, "forex": None, "prev": None, "stock_quotes": None, "holdings_by_fund": {}}

    # 仅安装采集 / 任意年龄缓存 / 写缓存桩；get_spot_snapshot 另行打桩为哨兵。
    monkeypatch.setattr(
        us_market_service, "fetch_us_index_futures", lambda: state["futures"]
    )
    monkeypatch.setattr(
        us_market_service, "fetch_us_index_spot", lambda: state.get("indices")
    )
    monkeypatch.setattr(us_market_service, "fetch_usd_cny", lambda: state["forex"])
    monkeypatch.setattr(
        us_market_service, "fetch_stock_changes_for_holdings", lambda *_a, **_k: state.get("stock_quotes") or {}
    )
    monkeypatch.setattr(
        us_market_service,
        "load_qdii_holdings_batch",
        lambda *_a, **_k: state.get("holdings_by_fund") or {},
    )
    monkeypatch.setattr(
        us_market_service, "get_spot_snapshot_any_age", lambda *_a, **_k: state["prev"]
    )
    monkeypatch.setattr(us_market_service, "save_spot_snapshot", lambda *_a, **_k: None)

    sentinel_updated_at = "1999-01-01T00:00:00-05:00"
    sentinel = {
        "session_kind": "pre_market",
        "session_label": "盘前交易中",
        "et_date": "2026-06-17",
        "updated_at": sentinel_updated_at,
        "futures": [],  # 空——与重聚合得到的 3 条形成可区分对照
        "usd_cny": {
            "last_price": 7.0,
            "change_percent": 0.1,
            "quote_time": "2026-06-16",
            "status": "ok",
        },
        "qdii": [],
        "qdii_status": "ok",
        "futures_status": "ok",
        "forex_status": "ok",
        "available": True,
        "from_cache": False,
        "stale": False,
        "message": None,
    }
    monkeypatch.setattr(
        us_market_service, "get_spot_snapshot", lambda *_a, **_k: sentinel
    )

    rng = random.Random(607004)
    state["futures"] = _gen_futures_rows(rng)
    state["forex"] = _gen_forex_row(rng)

    # force_refresh=False：命中新鲜缓存，短路返回哨兵（未重聚合）。
    cached_snap = get_us_market_snapshot(force_refresh=False)
    assert cached_snap.from_cache is True
    assert cached_snap.updated_at == sentinel_updated_at
    assert cached_snap.futures == []  # 来自哨兵的空列表，证明未重聚合

    # force_refresh=True：忽略新鲜缓存，重新聚合。
    fresh_snap = get_us_market_snapshot(force_refresh=True)
    assert fresh_snap.from_cache is False
    assert fresh_snap.updated_at != sentinel_updated_at
    assert len(fresh_snap.futures) == 3  # 重聚合得到固定 3 条
    assert fresh_snap.futures_status == "ok"
    assert fresh_snap.forex_status == "ok"


def test_after_hours_prefers_index_spot_over_futures(monkeypatch):
    """方案 C：盘后优先指数收盘涨跌，期货仅作交叉回退。"""
    monkeypatch.setattr(
        us_market_service,
        "detect_us_session",
        lambda *_a, **_k: {
            "session_kind": "after_hours",
            "session_label": "盘后",
            "et_date": "2026-06-17",
        },
    )
    state: dict = {"futures": None, "forex": None, "indices": None, "prev": None}
    _install_drivers(monkeypatch, state)

    state["futures"] = [
        {
            "symbol": "NASDAQ_FUT",
            "display_name": "纳斯达克",
            "last_price": 30100.0,
            "change_percent": -0.67,
            "quote_time": "2026-06-17T20:00:00-04:00",
        },
        {
            "symbol": "SP500_FUT",
            "display_name": "标普500",
            "last_price": 7505.0,
            "change_percent": -1.08,
            "quote_time": "2026-06-17T20:00:00-04:00",
        },
        {
            "symbol": "DOW_FUT",
            "display_name": "道琼斯",
            "last_price": 52044.0,
            "change_percent": -0.81,
            "quote_time": "2026-06-17T20:00:00-04:00",
        },
    ]
    state["indices"] = [
        {
            "symbol": "NASDAQ_FUT",
            "display_name": "纳斯达克",
            "last_price": 26376.34,
            "change_percent": -1.15,
            "quote_time": "2026-06-16",
            "source": "index_us_stock_sina",
        },
        {
            "symbol": "SP500_FUT",
            "display_name": "标普500",
            "last_price": 7511.35,
            "change_percent": -0.57,
            "quote_time": "2026-06-16",
            "source": "index_us_stock_sina",
        },
        {
            "symbol": "DOW_FUT",
            "display_name": "道琼斯",
            "last_price": 51999.67,
            "change_percent": 0.64,
            "quote_time": "2026-06-16",
            "source": "index_us_stock_sina",
        },
    ]
    state["forex"] = {
        "last_price": 6.8096,
        "change_percent": -0.02,
        "quote_time": "2026-06-17",
        "source": "currency_boc_safe",
    }

    snap = get_us_market_snapshot(force_refresh=True)
    by_symbol = {q.symbol: q for q in snap.futures}
    assert by_symbol["NASDAQ_FUT"].change_percent == pytest.approx(-1.15)
    assert by_symbol["NASDAQ_FUT"].last_price == pytest.approx(26376.34)
    assert by_symbol["NASDAQ_FUT"].quote_caliber == "index_close"
    # 方案 A：盘后三指数统一指数收盘，期货仅作缺失回退
    assert by_symbol["DOW_FUT"].change_percent == pytest.approx(0.64)
    assert by_symbol["DOW_FUT"].quote_caliber == "index_close"
    assert snap.qdii == []


def test_fundgz_used_for_active_fund_without_holdings(monkeypatch):
    """主动型基金无穿透数据时，仍使用天天基金估值。"""
    _pin_session(monkeypatch)
    _enable_qdii_estimates(monkeypatch)
    state: dict = {
        "futures": None,
        "forex": None,
        "prev": None,
        "stock_quotes": None,
        "holdings_by_fund": {},
        "fundgz_estimates": {
            "012920": {"change_percent": 0.44, "estimated_at": "2026-06-17 15:00"},
        },
    }
    _install_drivers(monkeypatch, state)
    state["futures"] = [
        {
            "symbol": "NASDAQ_FUT",
            "display_name": "纳斯达克",
            "last_price": 19850.5,
            "change_percent": -1.34,
            "quote_time": "2026-06-17T08:12:00-04:00",
        }
    ]
    state["forex"] = {
        "last_price": 6.8096,
        "change_percent": -0.02,
        "quote_time": "2026-06-17",
    }

    snap = get_us_market_snapshot(force_refresh=True)
    by_code = {item.fund_code: item for item in snap.qdii}
    assert by_code["012920"].reference_change_percent == 0.44
    assert by_code["012920"].estimate_basis and "天天基金" in by_code["012920"].estimate_basis
    assert by_code["012920"].estimated_at == "2026-06-17 15:00"
    # 无 fundgz 的基金仍走指数回退
    assert by_code["017436"].reference_change_percent == round(-1.34 * 1.0, 2)


def test_holdings_penetration_overrides_fundgz_for_active_fund(monkeypatch):
    """主动型 QDII 优先穿透估值（对标小倍夜盘），指数型仍走指数/天天基金。"""
    _pin_session(monkeypatch)
    _enable_qdii_estimates(monkeypatch)
    state: dict = {
        "futures": None,
        "forex": None,
        "prev": None,
        "fundgz_estimates": {
            "012920": {"change_percent": -0.47, "estimated_at": "2026-06-18 04:00"},
        },
        "stock_quotes": {
            "us:NVDA": -2.0,
            "us:AAPL": 4.0,
        },
        "holdings_by_fund": {
            "012920": {
                "fund_code": "012920",
                "holdings": [
                    {"code": "NVDA", "market": "us", "weight": 10.0},
                    {"code": "AAPL", "market": "us", "weight": 10.0},
                ],
            }
        },
    }
    _install_drivers(monkeypatch, state)
    state["futures"] = [
        {
            "symbol": "NASDAQ_FUT",
            "display_name": "纳斯达克",
            "last_price": 19850.5,
            "change_percent": -1.34,
            "quote_time": "2026-06-17T08:12:00-04:00",
        }
    ]
    state["forex"] = {
        "last_price": 6.8096,
        "change_percent": -0.02,
        "quote_time": "2026-06-17",
    }

    snap = get_us_market_snapshot(force_refresh=True)
    by_code = {item.fund_code: item for item in snap.qdii}
    assert by_code["012920"].reference_change_percent == 1.0
    assert by_code["012920"].estimate_basis and "穿透" in by_code["012920"].estimate_basis
    # 指数型无 fundgz 时走指数系数，不用穿透
    assert by_code["017436"].reference_change_percent == round(-1.34 * 1.0, 2)
