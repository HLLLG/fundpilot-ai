"""美股概览 snapshot 聚合 + 时段感知缓存 + 优雅降级（需求 4 / 1.5 / 2.2 / 2.5 / 7）。

设计依据：``.kiro/specs/us-market-overview/design.md`` §3 / §6.2 / §10。

主线沿用 ``sector_board_snapshot.py`` 的 ``detect_session → TTL → cache_key →
get_spot_snapshot / any_age → fetch → save`` 模式，复用 ``sector_quote_cache``。

核心安全不变量（需求 1.5 / 2.5 / 7.5，Property 5）——**绝不编造数值**::

    status == "ok"          数值来自本次真实采集；
    status == "stale"       数值等于该源「上一次真实采集」的缓存值（沿用最后真实值）；
    status == "unavailable" 数值字段一律为 None。

在任何情形下，数值字段都**不得**由指数收盘价或占位常量推导。失败时要么沿用缓存中
的最后真实值（stale），要么置 None（unavailable）。

降级矩阵（每个数据源）::

    本次采集成功            → ok          使用真实最新值
    失败 + 有历史真实缓存   → stale       沿用最后真实值（quote_time 为旧时间）
    失败 + 无历史缓存       → unavailable 数值为 None（QDII 此时返回空列表）
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any

from app.models import (
    QdiiPremarketItem,
    UsdCnyQuote,
    UsFuturesQuote,
    UsMarketSnapshot,
)
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.config import get_settings
from app.services.fund_estimate_provider import fetch_fund_estimates_for_codes
from app.services.us_forex_client import fetch_usd_cny
from app.services.us_futures_client import fetch_us_index_futures
from app.services.us_index_client import fetch_us_index_spot
from app.services.us_market_session import US_TZ, detect_us_session
from app.services.us_qdii_holdings_client import load_qdii_holdings_batch
from app.services.us_qdii_quote_policy import quote_mode_for_session
from app.services.us_qdii_seeds import get_qdii_seeds
from app.services.us_qdii_valuation_service import (
    build_fundgz_meta_map,
    build_fundgz_reference_map,
    build_holdings_reference_map,
    latest_fundgz_time,
    merge_qdii_references,
)
from app.services.us_stock_quote_client import (
    fetch_stock_changes_for_holdings,
)

logger = logging.getLogger(__name__)

# 时段感知 TTL（秒）：盘前/盘中高频；盘后/休市低频（需求 4.3 / 4.4，Property 4）。
_LIVE_TTL_SECONDS = 60.0
_CLOSED_TTL_SECONDS = 1800.0
_CACHE_VERSION = "v8"
_STOCK_QUOTES_CACHE_VERSION = "v3"
_FUNDGZ_CACHE_VERSION = "v1"

# 并行拉取期货 + 指数 + 汇率的总预算（秒）。子进程 client 自身另有 60s 超时兜底。
_FETCH_BUDGET_SECONDS = 15.0

# 固定 3 条顶部指标（顺序即展示顺序；对标小倍「纳斯达克/标普500/道琼斯」）。
_MARKET_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("NASDAQ_FUT", "纳斯达克"),
    ("SP500_FUT", "标普500"),
    ("DOW_FUT", "道琼斯"),
)

_LIVE_KINDS = {"pre_market", "regular"}
_REST_KINDS = {"after_hours", "closed"}


def qdii_estimates_enabled() -> bool:
    """是否聚合 QDII 参考涨跌（默认关闭，方案 A 仅展示大盘指数）。"""
    return get_settings().us_market_qdii_enabled


def _ttl_for(session_kind: str) -> float:
    """时段感知 TTL：pre_market/regular ≤60s；after_hours/closed 用更长 TTL。"""
    return _LIVE_TTL_SECONDS if session_kind in _LIVE_KINDS else _CLOSED_TTL_SECONDS


def _bucket_for(session_kind: str) -> str:
    return "live" if session_kind in _LIVE_KINDS else "rest"


def get_us_market_snapshot(*, force_refresh: bool = False) -> UsMarketSnapshot:
    """聚合美股概览 snapshot（期货 + USD/CNY + QDII 盘前参考涨跌）。

    Args:
        force_refresh: 为 ``True`` 时绕过服务端缓存重新聚合（需求 4.5）。

    Returns:
        ``UsMarketSnapshot``，任何数据源失败均通过各 ``*_status`` / ``available`` /
        ``stale`` / ``message`` 表达降级，绝不抛出、绝不编造数值。
    """
    session = detect_us_session()
    session_kind = session["session_kind"]
    cache_key = (
        f"market:us_overview:{_CACHE_VERSION}:"
        f"{_bucket_for(session_kind)}:{session['et_date']}"
    )

    # 1) 新鲜或 stale 缓存（API 只读，后台任务负责刷新）
    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=_ttl_for(session_kind))
        if cached and cached.get("available"):
            return UsMarketSnapshot(**{**cached, "from_cache": True, "stale": False})
        stale_cached = get_spot_snapshot_any_age(cache_key)
        if stale_cached and stale_cached.get("available"):
            return UsMarketSnapshot(
                **{
                    **stale_cached,
                    "from_cache": True,
                    "stale": True,
                    "message": stale_cached.get("message")
                    or "展示缓存数据，后台将在下一活跃时段更新",
                }
            )

    # 2) 任意年龄缓存——fetch 失败时的 degrade 回退
    prev = get_spot_snapshot_any_age(cache_key)

    # 3) force_refresh 或冷启动：并行拉取期货 + 指数 + 汇率
    raw_futures, raw_indices, raw_forex = _fetch_sources_parallel()

    futures, futures_status = _degrade_market_quotes(
        session_kind, raw_futures, raw_indices, prev
    )
    usd_cny, forex_status = _degrade_forex(raw_forex, prev)
    if qdii_estimates_enabled():
        fund_codes = [seed["fund_code"] for seed in get_qdii_seeds()]
        fundgz_refs, fundgz_meta, fundgz_status = _resolve_fundgz_refs(
            session_kind, session["et_date"], fund_codes, prev
        )
        holdings_by_fund: dict[str, dict[str, Any]] = {}
        stock_quote_map: dict[str, float] | None = None
        stock_quotes_status = "unavailable"
        all_holdings = load_qdii_holdings_batch(fund_codes)
        holdings_by_fund = {
            code: payload for code, payload in all_holdings.items() if code in fund_codes
        }
        if holdings_by_fund:
            stock_quote_map, stock_quotes_status = _resolve_stock_quote_map(
                session_kind, session["et_date"], prev, holdings_by_fund
            )
        quote_mode = quote_mode_for_session(session_kind)
        qdii, qdii_status = _build_qdii_items(
            futures,
            futures_status,
            fundgz_refs,
            fundgz_status,
            stock_quote_map,
            stock_quotes_status,
            prev,
            holdings_by_fund,
            fundgz_meta=fundgz_meta,
            quote_mode=quote_mode,
        )
    else:
        fundgz_meta = {}
        qdii, qdii_status = [], "unavailable"

    available = any(
        status != "unavailable"
        for status in (futures_status, forex_status, qdii_status)
    )
    stale = any(
        status == "stale"
        for status in (futures_status, forex_status, qdii_status)
    )

    snapshot = UsMarketSnapshot(
        session_kind=session_kind,
        session_label=session["session_label"],
        et_date=session["et_date"],
        updated_at=datetime.now(US_TZ).isoformat(timespec="seconds"),
        futures=futures,
        usd_cny=usd_cny,
        qdii=qdii,
        qdii_status=qdii_status,
        qdii_estimated_at=latest_fundgz_time(fundgz_meta),
        futures_status=futures_status,
        forex_status=forex_status,
        available=available,
        from_cache=False,
        stale=stale,
        message=_build_message(
            available=available,
            futures_status=futures_status,
            forex_status=forex_status,
            qdii_status=qdii_status,
        ),
    )

    # 4) 仅在 available 时写缓存（避免把「全不可用」污染成最后真实值）。
    if available:
        save_spot_snapshot(cache_key, snapshot.model_dump(mode="json"))

    return snapshot


# ---------------------------------------------------------------------------
# 并行采集（共享 ~10s 预算）
# ---------------------------------------------------------------------------


def _fetch_sources_parallel() -> tuple[
    list[dict[str, Any]] | None,
    list[dict[str, Any]] | None,
    dict[str, Any] | None,
]:
    """并行拉取期货 + 指数现货 + 汇率，共享 ``_FETCH_BUDGET_SECONDS`` 预算。"""
    deadline = time.monotonic() + _FETCH_BUDGET_SECONDS
    executor = ThreadPoolExecutor(max_workers=3)
    try:
        fut_futures = executor.submit(_safe_fetch_futures)
        fut_indices = executor.submit(_safe_fetch_indices)
        fut_forex = executor.submit(_safe_fetch_forex)
        raw_futures = _result_within(fut_futures, deadline, label="futures")
        raw_indices = _result_within(fut_indices, deadline, label="indices")
        raw_forex = _result_within(fut_forex, deadline, label="forex")
    finally:
        executor.shutdown(wait=False)
    return raw_futures, raw_indices, raw_forex


def _result_within(future: Future, deadline: float, *, label: str) -> Any:
    remaining = max(0.0, deadline - time.monotonic())
    try:
        return future.result(timeout=remaining)
    except Exception as exc:  # noqa: BLE001 — 含 TimeoutError，统一降级为 None
        logger.warning("us market fetch failed/timeout (%s): %s", label, exc)
        return None


def _safe_fetch_futures() -> list[dict[str, Any]] | None:
    try:
        return fetch_us_index_futures()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_us_index_futures raised: %s", exc)
        return None


def _safe_fetch_indices() -> list[dict[str, Any]] | None:
    try:
        return fetch_us_index_spot()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_us_index_spot raised: %s", exc)
        return None


def _safe_fetch_forex() -> dict[str, Any] | None:
    try:
        return fetch_usd_cny()
    except Exception as exc:  # noqa: BLE001
        logger.warning("fetch_usd_cny raised: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 降级：期货
# ---------------------------------------------------------------------------


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _index_quotes_by_symbol(rows: Any) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return indexed
    for row in rows:
        if isinstance(row, dict) and row.get("symbol"):
            indexed[str(row["symbol"])] = row
    return indexed


# ---------------------------------------------------------------------------
# 降级：顶部指标（方案 C — 盘前/盘中期货，盘后/休市指数收盘）
# ---------------------------------------------------------------------------


def _row_has_price(row: dict[str, Any] | None) -> bool:
    return row is not None and _to_float(row.get("last_price")) is not None


def _pick_rest_change_percent(
    symbol: str,
    index_row: dict[str, Any] | None,
    futures_row: dict[str, Any] | None,
) -> tuple[float | None, str]:
    """盘后/休市涨跌：三指数统一指数收盘（方案 A，对标小倍）。"""
    _ = symbol
    idx_chg = _to_float(index_row.get("change_percent")) if index_row else None
    if idx_chg is not None:
        return idx_chg, "index_close"
    fut_chg = _to_float(futures_row.get("change_percent")) if futures_row else None
    if fut_chg is not None:
        return fut_chg, "futures_night"
    return None, "index_close"


def _degrade_market_quotes(
    session_kind: str,
    raw_futures: list[dict[str, Any]] | None,
    raw_indices: list[dict[str, Any]] | None,
    prev: dict[str, Any] | None,
) -> tuple[list[UsFuturesQuote], str]:
    """按时段合并期货 / 指数双源，再按 ok / stale / unavailable 降级。"""
    prefer_futures = session_kind in _LIVE_KINDS
    fetched_futures = _index_quotes_by_symbol(raw_futures)
    fetched_indices = _index_quotes_by_symbol(raw_indices)
    prev_by_symbol = _index_quotes_by_symbol(prev.get("futures") if prev else None)

    primary = fetched_futures if prefer_futures else fetched_indices
    secondary = fetched_indices if prefer_futures else fetched_futures

    quotes: list[UsFuturesQuote] = []
    statuses: list[str] = []
    for symbol, display_name in _MARKET_SYMBOLS:
        row: dict[str, Any] | None = None
        quote_caliber = "futures_live"

        if prefer_futures:
            row = primary.get(symbol) if _row_has_price(primary.get(symbol)) else None
            if row is None:
                alt = secondary.get(symbol)
                if _row_has_price(alt):
                    row = alt
                    quote_caliber = "index_close"
        else:
            idx_row = fetched_indices.get(symbol)
            fut_row = fetched_futures.get(symbol)
            if _row_has_price(idx_row):
                change_percent, quote_caliber = _pick_rest_change_percent(
                    symbol, idx_row, fut_row
                )
                row = {
                    **idx_row,
                    "change_percent": change_percent,
                }
            elif _row_has_price(fut_row):
                row = fut_row
                quote_caliber = "futures_night"

        last_price = _to_float(row.get("last_price")) if row else None
        if row is not None and last_price is not None:
            quotes.append(
                UsFuturesQuote(
                    symbol=symbol,
                    display_name=display_name,
                    last_price=last_price,
                    change_percent=_to_float(row.get("change_percent")),
                    quote_time=row.get("quote_time"),
                    quote_caliber=quote_caliber,
                    status="ok",
                )
            )
            statuses.append("ok")
            continue

        prev_row = prev_by_symbol.get(symbol)
        prev_price = _to_float(prev_row.get("last_price")) if prev_row else None
        if prev_row is not None and prev_price is not None:
            quotes.append(
                UsFuturesQuote(
                    symbol=symbol,
                    display_name=display_name,
                    last_price=prev_price,
                    change_percent=_to_float(prev_row.get("change_percent")),
                    quote_time=prev_row.get("quote_time"),
                    quote_caliber=prev_row.get("quote_caliber"),
                    status="stale",
                )
            )
            statuses.append("stale")
            continue

        quotes.append(
            UsFuturesQuote(symbol=symbol, display_name=display_name, status="unavailable")
        )
        statuses.append("unavailable")

    return quotes, _aggregate_status(statuses)


def _degrade_futures(
    raw_futures: list[dict[str, Any]] | None,
    prev: dict[str, Any] | None,
) -> tuple[list[UsFuturesQuote], str]:
    """兼容旧调用：仅期货单源降级（测试 / 回退）。"""
    return _degrade_market_quotes("pre_market", raw_futures, None, prev)


def _aggregate_status(statuses: list[str]) -> str:
    """整体状态：任一 ok→ok；否则任一 stale→stale；否则 unavailable。"""
    if any(s == "ok" for s in statuses):
        return "ok"
    if any(s == "stale" for s in statuses):
        return "stale"
    return "unavailable"


# ---------------------------------------------------------------------------
# 降级：汇率
# ---------------------------------------------------------------------------


def _degrade_forex(
    raw_forex: dict[str, Any] | None,
    prev: dict[str, Any] | None,
) -> tuple[UsdCnyQuote, str]:
    """USD/CNY 按 ok / stale / unavailable 降级。

    本次采集返回真实值即 ok（即便源为日频，其值仍是真实采集而非缓存回退）；
    失败有缓存 → stale 沿用最后真实值；失败无缓存 → unavailable。
    """
    last_price = _to_float(raw_forex.get("last_price")) if raw_forex else None
    if raw_forex is not None and last_price is not None:
        return (
            UsdCnyQuote(
                last_price=last_price,
                change_percent=_to_float(raw_forex.get("change_percent")),
                quote_time=raw_forex.get("quote_time"),
                status="ok",
            ),
            "ok",
        )

    prev_forex = prev.get("usd_cny") if prev else None
    prev_price = _to_float(prev_forex.get("last_price")) if isinstance(prev_forex, dict) else None
    if isinstance(prev_forex, dict) and prev_price is not None:
        return (
            UsdCnyQuote(
                last_price=prev_price,
                change_percent=_to_float(prev_forex.get("change_percent")),
                quote_time=prev_forex.get("quote_time"),
                status="stale",
            ),
            "stale",
        )

    return UsdCnyQuote(status="unavailable"), "unavailable"


# ---------------------------------------------------------------------------
# 天天基金 fundgz 估值（QDII 优先数据源）
# ---------------------------------------------------------------------------


def _fundgz_cache_key(session_kind: str, et_date: str) -> str:
    return (
        f"market:us_qdii_fundgz:{_FUNDGZ_CACHE_VERSION}:"
        f"{_bucket_for(session_kind)}:{et_date}"
    )


def _resolve_fundgz_refs(
    session_kind: str,
    et_date: str,
    fund_codes: list[str],
    prev: dict[str, Any] | None,
) -> tuple[dict[str, float], dict[str, dict[str, str]], str]:
    """天天基金 ``gszzl`` ok / stale / unavailable（桶级缓存）。"""
    cache_key = _fundgz_cache_key(session_kind, et_date)
    ttl = _ttl_for(session_kind)

    cached = get_spot_snapshot(cache_key, ttl_seconds=ttl)
    if cached and isinstance(cached.get("refs"), dict) and cached["refs"]:
        meta = cached.get("meta") if isinstance(cached.get("meta"), dict) else {}
        return (
            {str(k): float(v) for k, v in cached["refs"].items()},
            {str(k): v for k, v in meta.items() if isinstance(v, dict)},
            "ok",
        )

    raw = fetch_fund_estimates_for_codes(
        fund_codes, timeout_seconds=min(12.0, _FETCH_BUDGET_SECONDS)
    )
    refs = build_fundgz_reference_map(raw)
    meta = build_fundgz_meta_map(raw)
    if refs:
        save_spot_snapshot(cache_key, {"refs": refs, "meta": meta})
        return refs, meta, "ok"

    stale_payload = get_spot_snapshot_any_age(cache_key)
    if stale_payload and isinstance(stale_payload.get("refs"), dict) and stale_payload["refs"]:
        stale_meta = (
            stale_payload.get("meta") if isinstance(stale_payload.get("meta"), dict) else {}
        )
        return (
            {str(k): float(v) for k, v in stale_payload["refs"].items()},
            {str(k): v for k, v in stale_meta.items() if isinstance(v, dict)},
            "stale",
        )

    return {}, {}, "unavailable"


# ---------------------------------------------------------------------------
# 个股涨跌幅（穿透估值回退）
# ---------------------------------------------------------------------------


def _stock_quotes_cache_key(session_kind: str, et_date: str) -> str:
    return (
        f"market:us_stock_changes:{_STOCK_QUOTES_CACHE_VERSION}:"
        f"{_bucket_for(session_kind)}:{et_date}"
    )


def _resolve_stock_quote_map(
    session_kind: str,
    et_date: str,
    prev: dict[str, Any] | None,
    holdings_by_fund: dict[str, dict[str, Any]],
) -> tuple[dict[str, float] | None, str]:
    """个股涨跌幅 ok / stale / unavailable（按重仓定向采集 + 桶级缓存）。"""
    cache_key = _stock_quotes_cache_key(session_kind, et_date)
    ttl = _ttl_for(session_kind)

    cached = get_spot_snapshot(cache_key, ttl_seconds=ttl)
    if cached and isinstance(cached.get("quotes"), dict) and cached["quotes"]:
        return {str(k): float(v) for k, v in cached["quotes"].items()}, "ok"

    deadline = time.monotonic() + min(20.0, _FETCH_BUDGET_SECONDS)
    mode = quote_mode_for_session(session_kind)
    fresh = fetch_stock_changes_for_holdings(
        holdings_by_fund, deadline=deadline, mode=mode
    )
    if fresh:
        save_spot_snapshot(cache_key, {"quotes": fresh})
        return fresh, "ok"

    stale_payload = get_spot_snapshot_any_age(cache_key)
    if stale_payload and isinstance(stale_payload.get("quotes"), dict) and stale_payload["quotes"]:
        return {str(k): float(v) for k, v in stale_payload["quotes"].items()}, "stale"

    return None, "unavailable"


# ---------------------------------------------------------------------------
# QDII 盘前参考涨跌估算（天天基金 > 穿透 > 指数系数回退）
# ---------------------------------------------------------------------------


def _futures_change_map(futures: list[UsFuturesQuote]) -> dict[str, float | None]:
    """symbol → change_percent（仅取非 unavailable 的真实/沿用值）。"""
    mapping: dict[str, float | None] = {}
    for quote in futures:
        if quote.status != "unavailable":
            mapping[quote.symbol] = quote.change_percent
    return mapping


def _aggregate_qdii_status(
    futures_status: str,
    stock_quotes_status: str,
    fundgz_status: str,
    *,
    has_reference: bool,
) -> str:
    """QDII 整体状态：fundgz / 穿透 / 指数三源取较优 freshness。"""
    if not has_reference:
        return "unavailable"
    statuses = {futures_status, stock_quotes_status, fundgz_status}
    if "ok" in statuses:
        return "ok"
    if "stale" in statuses:
        return "stale"
    return "unavailable"


def _build_qdii_items(
    futures: list[UsFuturesQuote],
    futures_status: str,
    fundgz_refs: dict[str, float],
    fundgz_status: str,
    stock_quote_map: dict[str, float] | None,
    stock_quotes_status: str,
    prev: dict[str, Any] | None,
    holdings_by_fund: dict[str, dict[str, Any]] | None = None,
    fundgz_meta: dict[str, dict[str, str]] | None = None,
    quote_mode: str = "live",
) -> tuple[list[QdiiPremarketItem], str]:
    """估算各 QDII ``reference_change_percent``（fundgz > 穿透 > 指数回退）。"""
    seeds = get_qdii_seeds()
    can_estimate = (
        futures_status != "unavailable"
        or stock_quotes_status != "unavailable"
        or fundgz_status != "unavailable"
    )

    if not can_estimate:
        prev_qdii = prev.get("qdii") if prev else None
        if isinstance(prev_qdii, list) and prev_qdii:
            return [QdiiPremarketItem(**item) for item in prev_qdii], "stale"
        return [], "unavailable"

    holdings_by_fund = holdings_by_fund or {}
    holdings_refs: dict[str, float] = {}
    if stock_quote_map:
        holdings_refs = build_holdings_reference_map(holdings_by_fund, stock_quote_map)

    change_map = _futures_change_map(futures) if futures_status != "unavailable" else {}
    merged = merge_qdii_references(
        seeds,
        fundgz_refs,
        holdings_refs,
        change_map,
        fundgz_meta=fundgz_meta,
        quote_mode=quote_mode,
    )
    items = [QdiiPremarketItem(**row) for row in merged]
    has_reference = any(item.reference_change_percent is not None for item in items)

    if not has_reference:
        prev_qdii = prev.get("qdii") if prev else None
        if isinstance(prev_qdii, list) and prev_qdii:
            return [QdiiPremarketItem(**item) for item in prev_qdii], "stale"
        return items, "unavailable"

    status = _aggregate_qdii_status(
        futures_status,
        stock_quotes_status,
        fundgz_status,
        has_reference=has_reference,
    )
    return items, status


# ---------------------------------------------------------------------------
# 降级提示文案
# ---------------------------------------------------------------------------


def _build_message(
    *,
    available: bool,
    futures_status: str,
    forex_status: str,
    qdii_status: str,
) -> str | None:
    if not available:
        return "美股行情暂不可用，请稍后重试"

    parts: list[str] = []
    if futures_status == "stale":
        parts.append("美股指标更新失败，展示上次缓存值")
    elif futures_status == "unavailable":
        parts.append("美股指标暂不可用")

    if forex_status == "stale":
        parts.append("汇率数据更新失败，展示上次缓存值")
    elif forex_status == "unavailable":
        parts.append("汇率数据暂不可用")

    if qdii_estimates_enabled() and qdii_status == "unavailable":
        parts.append("QDII 参考涨跌暂不可用")

    return "；".join(parts) if parts else None
