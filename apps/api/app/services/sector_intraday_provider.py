from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.eastmoney_trends_client import fetch_eastmoney_intraday_trends

logger = logging.getLogger(__name__)
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.trading_session import CN_TZ, build_trading_session

IntradayPoint = dict[str, str | float]

# 盘中短缓存；收盘后长缓存（当日 9:30–15:00 曲线不再变）
_INTRADAY_LIVE_TTL_SECONDS = 60.0
_INTRADAY_CLOSED_TTL_SECONDS = 86400.0


def fetch_sector_intraday(
    source_type: str,
    source_name: str,
    *,
    force_refresh: bool = False,
) -> tuple[list[IntradayPoint], str | None, str | None]:
    """返回 (points, note, session_date)。"""
    label = (source_name or "").strip()
    if not label or source_type not in {"index", "concept", "industry"}:
        return [], "缺少板块映射信息", None

    session = build_trading_session()
    session_kind = session["session_kind"]
    trade_date = _effective_trade_date(session)
    closed_session = session_kind in {"trading_day_after_close", "non_trading_day"}

    cache_key = f"intraday:{source_type}:{label}:{trade_date}"
    cache_ttl = _INTRADAY_CLOSED_TTL_SECONDS if closed_session else _INTRADAY_LIVE_TTL_SECONDS

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl)
        if cached is not None and cached.get("points"):
            note = cached.get("note")
            if closed_session and not note:
                note = f"展示 {trade_date} 收盘分时（09:30–15:00）"
            return cached.get("points", []), note, trade_date

    should_fetch = _should_fetch_intraday(session_kind) or force_refresh
    points: list[IntradayPoint] = []
    note: str | None = None

    if should_fetch:
        try:
            if source_type == "index":
                points = _fetch_index_intraday(label, trade_date=trade_date)
            else:
                points = _fetch_board_intraday(source_type, label, trade_date=trade_date)
        except Exception as exc:
            logger.warning("sector intraday fetch failed for %s: %s", label, exc)
            if not note:
                note = "分时数据获取失败，请稍后重试"

    if not points and not force_refresh:
        stale = get_spot_snapshot(cache_key, ttl_seconds=86400.0 * 7)
        if stale and stale.get("points"):
            return (
                stale.get("points", []),
                stale.get("note") or f"展示 {trade_date} 缓存分时",
                trade_date,
            )

    if not points:
        note = note or "暂无分时数据（数据源未返回或板块未映射）"
    elif closed_session:
        note = f"展示 {trade_date} 收盘分时（09:30–15:00）"
    elif session_kind == "trading_day_intraday":
        note = "盘中实时分时"

    if points:
        save_spot_snapshot(
            cache_key,
            {"points": points, "note": note, "session_date": trade_date},
        )
    return points, note, trade_date


def _should_fetch_intraday(session_kind: str) -> bool:
    """收盘后仍需拉取当日完整分时（养基宝同款：展示 9:00–15:00 已定曲线）。"""
    return session_kind in {
        "trading_day_intraday",
        "trading_day_pre_close",
        "trading_day_after_close",
    }


def _effective_trade_date(session: dict) -> str:
    moment = datetime.now(CN_TZ)
    today = moment.date()
    if session["session_kind"] in {
        "trading_day_intraday",
        "trading_day_pre_close",
        "trading_day_after_close",
    }:
        return today.isoformat()

    cursor = today
    for _ in range(14):
        cursor -= timedelta(days=1)
        probe = build_trading_session(
            datetime.combine(cursor, datetime.min.time(), tzinfo=CN_TZ).replace(hour=12)
        )
        if probe["is_trading_day"]:
            return cursor.isoformat()
    return today.isoformat()


def _fetch_index_intraday(source_name: str, *, trade_date: str | None = None) -> list[IntradayPoint]:
    canon = get_canonical_sector(source_name)
    if canon is not None:
        points = fetch_eastmoney_intraday_trends(
            canon.eastmoney_secid,
            source_code=canon.source_code,
            trade_date=trade_date,
        )
        if points:
            return points

    symbol = _index_symbol_for_name(source_name)
    if symbol:
        points = fetch_eastmoney_intraday_trends(
            "",
            source_code=symbol,
            trade_date=trade_date,
        )
        if points:
            return points
        try:
            frame = _call_akshare_index_min(symbol)
            parsed = _points_from_minute_frame(frame)
            if parsed:
                return parsed
        except Exception as exc:
            logger.debug("akshare index intraday fallback failed for %s: %s", symbol, exc)
    return []


def _fetch_board_intraday(
    source_type: str,
    source_name: str,
    *,
    trade_date: str | None = None,
) -> list[IntradayPoint]:
    canon = get_canonical_sector(source_name)
    if canon is not None:
        points = fetch_eastmoney_intraday_trends(
            canon.eastmoney_secid,
            source_code=canon.source_code,
            trade_date=trade_date,
        )
        if points:
            return points

    points = fetch_eastmoney_intraday_trends("", source_code=source_name, trade_date=trade_date)
    if points:
        return points

    try:
        frame = _call_akshare_board_min(source_type, source_name)
        return _points_from_minute_frame(frame)
    except Exception as exc:
        logger.debug("akshare board intraday fallback failed for %s: %s", source_name, exc)
        return []


def _call_akshare_index_min(symbol: str):
    import akshare as ak  # type: ignore[import-not-found]

    fn = getattr(ak, "index_zh_a_hist_min_em", None) or getattr(
        ak, "stock_zh_index_hist_min_em", None
    )
    if fn is None:
        return None
    return fn(symbol=symbol, period="1")


def _call_akshare_board_min(source_type: str, source_name: str):
    import akshare as ak  # type: ignore[import-not-found]

    if source_type == "concept":
        return ak.stock_board_concept_hist_min_em(symbol=source_name, period="1")
    return ak.stock_board_industry_hist_min_em(symbol=source_name, period="1")


def _points_from_minute_frame(frame) -> list[IntradayPoint]:
    if frame is None or frame.empty:
        return []

    points: list[IntradayPoint] = []
    for _, row in frame.iterrows():
        time_value = _cell(row, "时间", "time")
        percent = _cell_float(row, "涨跌幅", "change", "涨跌")
        if time_value is None or percent is None:
            continue
        clock = str(time_value).strip()
        if not _in_trading_clock(clock):
            continue
        points.append({"time": clock[:5], "percent": percent})

    if len(points) >= 2:
        return points

    baseline = _cell_float(frame.iloc[0], "收盘", "close", "最新价")
    if baseline is None or baseline == 0:
        return []

    rebuilt: list[IntradayPoint] = []
    for _, row in frame.iterrows():
        time_value = _cell(row, "时间", "time")
        price = _cell_float(row, "收盘", "close", "最新价")
        if time_value is None or price is None:
            continue
        clock = str(time_value).strip()[:5]
        if not _in_trading_clock(clock):
            continue
        percent = round((price / baseline - 1) * 100, 4)
        rebuilt.append({"time": clock, "percent": percent})
    return rebuilt


def _in_trading_clock(clock: str) -> bool:
    try:
        hour, minute = clock.split(":")
        total = int(hour) * 60 + int(minute)
    except ValueError:
        return False
    return (9 * 60 + 30) <= total <= (15 * 60)


def _index_symbol_for_name(source_name: str) -> str | None:
    canon = get_canonical_sector(source_name)
    if canon is not None and canon.source_type == "index" and canon.source_code:
        return canon.source_code

    aliases = {
        "上证指数": "000001",
        "上证综指": "000001",
        "深证成指": "399001",
        "创业板指": "399006",
        "沪深300": "000300",
        "中证500": "000905",
        "中证1000": "000852",
        "科创50": "000688",
        "中证人工智能": "930713",
        "人工智能": "930713",
        "中证电网设备": "931994",
        "电网设备": "931994",
    }
    if source_name in aliases:
        return aliases[source_name]
    for key, symbol in aliases.items():
        if key in source_name or source_name in key:
            return symbol
    return None


def _cell(row: object, *names: str) -> str | None:
    for name in names:
        if hasattr(row, "index") and name in row.index:  # type: ignore[attr-defined]
            value = row[name]  # type: ignore[index]
            if value is not None and str(value).strip():
                return str(value).strip()
    return None


def _cell_float(row: object, *names: str) -> float | None:
    raw = _cell(row, *names)
    if raw is None:
        return None
    cleaned = raw.replace("%", "").replace(",", "").strip()
    try:
        return round(float(cleaned), 4)
    except ValueError:
        return None
