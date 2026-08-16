"""行情页头部 A 股宽基指数快照。

对标养基宝「上证指数 / 深证成指 / 创业板指 …」横滑卡片：点位、涨跌额、涨跌幅。
只读东财 ulist 现货，失败时沿用上次真实缓存，绝不编造数值。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.models import CnIndexOverview, CnIndexQuote, DataSourceStatus
from app.services.eastmoney_spot_client import fetch_eastmoney_quotes_by_secid
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

_ASIA_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CACHE_VERSION = "v2"
_LIVE_SESSIONS = frozenset({"trading_day_intraday", "trading_day_pre_close"})

# 顺序即行情页头部展示顺序。secid 与 sector_intraday_provider 主流指数映射一致。
CN_INDEX_SPECS: tuple[tuple[str, str, str], ...] = (
    ("000001", "1.000001", "上证指数"),
    ("399001", "0.399001", "深证成指"),
    ("399006", "0.399006", "创业板指"),
    ("000300", "1.000300", "沪深300"),
    ("000688", "1.000688", "科创50"),
)


def _ttl_for(session_kind: str) -> float:
    """读缓存新鲜度：盘中 20min；休市沿用闲时 TTL，过期后只回 stale 缓存、不再打源。"""
    from app.services.market_shared_refresh import (
        idle_refresh_interval_seconds,
        live_refresh_interval_seconds,
    )

    if session_kind in _LIVE_SESSIONS:
        return live_refresh_interval_seconds()
    return idle_refresh_interval_seconds()


def _cache_key(trade_date: str) -> str:
    return f"market:cn_index_overview:{_CACHE_VERSION}:{trade_date}"


def _change_from_price_and_percent(
    price: float | None,
    percent: float | None,
) -> float | None:
    if price is None or percent is None:
        return None
    denom = 1 + percent / 100
    if denom == 0:
        return None
    return round(price - price / denom, 4)


def _quote_time_iso(timestamp: int | None) -> str | None:
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(int(timestamp), tz=_ASIA_SHANGHAI).isoformat(
            timespec="seconds"
        )
    except (OSError, OverflowError, ValueError):
        return None


def _empty_quote(symbol: str, display_name: str) -> CnIndexQuote:
    return CnIndexQuote(
        symbol=symbol,
        display_name=display_name,
        last_price=None,
        change=None,
        change_percent=None,
        quote_time=None,
        status="unavailable",
    )


def _quote_from_row(
    symbol: str,
    display_name: str,
    row: dict[str, Any] | None,
    *,
    status: DataSourceStatus,
) -> CnIndexQuote:
    if not row:
        return _empty_quote(symbol, display_name)
    last_price = row.get("latest_price")
    change_percent = row.get("change_percent")
    change = row.get("change_amount")
    if change is None:
        change = _change_from_price_and_percent(
            last_price if isinstance(last_price, (int, float)) else None,
            change_percent if isinstance(change_percent, (int, float)) else None,
        )
    if last_price is None and change_percent is None:
        return _empty_quote(symbol, display_name)
    return CnIndexQuote(
        symbol=symbol,
        display_name=display_name,
        last_price=round(float(last_price), 4) if last_price is not None else None,
        change=round(float(change), 4) if change is not None else None,
        change_percent=round(float(change_percent), 4) if change_percent is not None else None,
        quote_time=_quote_time_iso(row.get("quote_timestamp")),
        status=status,
    )


def _quote_from_cached_item(item: dict[str, Any], *, status: DataSourceStatus) -> CnIndexQuote:
    last_price = item.get("last_price")
    change = item.get("change")
    change_percent = item.get("change_percent")
    if last_price is None and change_percent is None:
        return _empty_quote(str(item.get("symbol") or ""), str(item.get("display_name") or ""))
    return CnIndexQuote(
        symbol=str(item.get("symbol") or ""),
        display_name=str(item.get("display_name") or ""),
        last_price=last_price,
        change=change,
        change_percent=change_percent,
        quote_time=item.get("quote_time"),
        status=status,
    )


def assemble_cn_index_items(
    fetched: dict[str, dict[str, Any]] | None,
    prev_items: list[dict[str, Any]] | None = None,
) -> list[CnIndexQuote]:
    """把东财批次结果对齐到固定指数清单；缺项沿用上次真实值，否则 unavailable。"""
    prev_by_symbol = {
        str(item.get("symbol")): item
        for item in (prev_items or [])
        if isinstance(item, dict) and item.get("symbol")
    }
    items: list[CnIndexQuote] = []
    for symbol, secid, display_name in CN_INDEX_SPECS:
        row = (fetched or {}).get(secid)
        if row:
            items.append(_quote_from_row(symbol, display_name, row, status="ok"))
            continue
        prev = prev_by_symbol.get(symbol)
        if prev and (prev.get("last_price") is not None or prev.get("change_percent") is not None):
            items.append(
                _quote_from_cached_item(
                    {**prev, "symbol": symbol, "display_name": display_name},
                    status="stale",
                )
            )
            continue
        items.append(_empty_quote(symbol, display_name))
    return items


def _overlay_live_session(
    payload: dict[str, Any],
    *,
    trade_date: str,
    session_kind: str,
    from_cache: bool,
    stale: bool,
    message: str | None = None,
) -> CnIndexOverview:
    """报价可沿用缓存，时段标签始终用当前交易日历。"""
    return CnIndexOverview(
        **{
            **payload,
            "from_cache": from_cache,
            "stale": stale,
            "trade_date": trade_date or payload.get("trade_date"),
            "session_kind": session_kind or payload.get("session_kind"),
            "message": message if message is not None else payload.get("message"),
        }
    )


def get_cn_index_overview(*, force_refresh: bool = False) -> CnIndexOverview:
    session = build_trading_session()
    trade_date = str(session.get("effective_trade_date") or "")
    session_kind = str(session.get("session_kind") or "")
    cache_key = _cache_key(trade_date or "unknown")

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=_ttl_for(session_kind))
        if cached and cached.get("available"):
            return _overlay_live_session(
                cached,
                trade_date=trade_date,
                session_kind=session_kind,
                from_cache=True,
                stale=False,
            )
        stale_cached = get_spot_snapshot_any_age(cache_key)
        if stale_cached and stale_cached.get("available"):
            return _overlay_live_session(
                stale_cached,
                trade_date=trade_date,
                session_kind=session_kind,
                from_cache=True,
                stale=True,
                message=stale_cached.get("message")
                or "展示缓存数据，后台将在下一活跃时段更新",
            )

    prev = get_spot_snapshot_any_age(cache_key)
    prev_items = prev.get("items") if isinstance(prev, dict) else None
    if not isinstance(prev_items, list):
        prev_items = None

    fetched: dict[str, dict[str, Any]] | None = None
    try:
        fetched = fetch_eastmoney_quotes_by_secid(
            [secid for _symbol, secid, _name in CN_INDEX_SPECS],
            timeout=6.0,
            max_retries=2,
        )
    except Exception as exc:  # noqa: BLE001 — 指数条失败必须降级，不能打爆行情页
        logger.warning("cn index overview fetch failed: %s", exc)
        fetched = None

    items = assemble_cn_index_items(fetched, prev_items)
    available = any(item.status != "unavailable" for item in items)
    stale = any(item.status == "stale" for item in items)
    snapshot = CnIndexOverview(
        items=items,
        available=available,
        from_cache=False,
        stale=stale,
        updated_at=datetime.now(_ASIA_SHANGHAI).isoformat(timespec="seconds"),
        trade_date=trade_date or None,
        session_kind=session_kind or None,
        message=None if available else "主要指数暂不可用",
    )
    if available:
        save_spot_snapshot(cache_key, snapshot.model_dump(mode="json"))
    return snapshot
