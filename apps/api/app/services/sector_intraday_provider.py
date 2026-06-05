from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.services.eastmoney_trends_client import fetch_eastmoney_intraday_trends
from app.services.sector_intraday_browser_provider import fetch_intraday_via_browser_command

logger = logging.getLogger(__name__)
from app.services.sector_canonical import get_canonical_sector, get_intraday_canonical_sector
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.trading_session import CN_TZ, build_trading_session

IntradayPoint = dict[str, str | float]

# 盘中短缓存；收盘后长缓存（当日 9:30–15:00 曲线不再变）
_INTRADAY_LIVE_TTL_SECONDS = 60.0
_INTRADAY_CLOSED_TTL_SECONDS = 86400.0
_INTRADAY_STALE_TTL_SECONDS = 86400.0 * 7

# 低于此点数视为骨架点（东财 kline 有时仅返回开盘+收盘 2 点），不写缓存也不用作 stale 兜底
_MIN_INTRADAY_POINTS_TO_CACHE = 30

# 合并并发请求，避免详情页连打东财导致偶发空数据
_INTRADAY_COALESCE_LOCK = threading.Lock()
_INTRADAY_COALESCE_WAITERS: dict[str, threading.Event] = {}
_INTRADAY_COALESCE_RESULT: dict[str, tuple[list[IntradayPoint], str | None, str | None, float | None]] = {}


def fetch_sector_intraday(
    source_type: str,
    source_name: str,
    *,
    force_refresh: bool = False,
) -> tuple[list[IntradayPoint], str | None, str | None, float | None]:
    """返回 (points, note, session_date, close_change_percent)。"""
    label = (source_name or "").strip()
    if not label or source_type not in {"index", "concept", "industry"}:
        return [], "缺少板块映射信息", None, None

    session = build_trading_session()
    session_kind = session["session_kind"]
    trade_date = _effective_trade_date(session)
    closed_session = session_kind in {"trading_day_after_close", "non_trading_day"}

    # v2：分时基准改为昨收（preKPrice）；旧键 intraday: 含开盘基准脏数据
    cache_key = f"intraday:v2:{source_type}:{label}:{trade_date}"
    cache_ttl = _INTRADAY_CLOSED_TTL_SECONDS if closed_session else _INTRADAY_LIVE_TTL_SECONDS

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl)
        if cached is not None and cached.get("points"):
            note = cached.get("note")
            if closed_session and not note:
                note = f"展示 {trade_date} 收盘分时（09:30–15:00）"
            return (
                cached.get("points", []),
                note,
                trade_date,
                cached.get("close_change_percent"),
            )

    return _coalesce_intraday_fetch(
        cache_key,
        lambda: _load_intraday_from_network(
            source_type,
            label,
            trade_date=trade_date,
            session_kind=session_kind,
            closed_session=closed_session,
            cache_key=cache_key,
            force_refresh=force_refresh,
        ),
    )


def _coalesce_intraday_fetch(
    cache_key: str,
    loader: Callable[[], tuple[list[IntradayPoint], str | None, str | None, float | None]],
) -> tuple[list[IntradayPoint], str | None, str | None, float | None]:
    with _INTRADAY_COALESCE_LOCK:
        if cache_key in _INTRADAY_COALESCE_WAITERS:
            event = _INTRADAY_COALESCE_WAITERS[cache_key]
            is_leader = False
        else:
            event = threading.Event()
            _INTRADAY_COALESCE_WAITERS[cache_key] = event
            is_leader = True

    if not is_leader:
        event.wait(timeout=90.0)
        with _INTRADAY_COALESCE_LOCK:
            cached = _INTRADAY_COALESCE_RESULT.get(cache_key)
        if cached is not None:
            return cached
        return loader()

    try:
        result = loader()
    finally:
        with _INTRADAY_COALESCE_LOCK:
            _INTRADAY_COALESCE_RESULT[cache_key] = result
            _INTRADAY_COALESCE_WAITERS.pop(cache_key, None)
            event.set()
    return result


def _load_intraday_from_network(
    source_type: str,
    label: str,
    *,
    trade_date: str,
    session_kind: str,
    closed_session: bool,
    cache_key: str,
    force_refresh: bool,
) -> tuple[list[IntradayPoint], str | None, str | None, float | None]:
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
            note = "分时数据获取失败，请稍后重试"

    if not points:
        stale = get_spot_snapshot(cache_key, ttl_seconds=_INTRADAY_STALE_TTL_SECONDS)
        stale_points = stale.get("points", []) if stale else []
        if len(stale_points) >= _MIN_INTRADAY_POINTS_TO_CACHE:
            return (
                stale_points,
                stale.get("note") or f"展示 {trade_date} 缓存分时（数据源暂不可用）",
                trade_date,
                stale.get("close_change_percent"),
            )

    if not points:
        note = note or "暂无分时数据（数据源未返回或板块未映射）"
    elif closed_session:
        note = f"展示 {trade_date} 收盘分时（09:30–15:00）"
    elif session_kind == "trading_day_intraday":
        note = "盘中实时分时"

    close_change_percent: float | None = None
    if points:
        last_percent = points[-1].get("percent")
        if last_percent is not None:
            close_change_percent = float(last_percent)

    if len(points) >= _MIN_INTRADAY_POINTS_TO_CACHE:
        # 健全性检查：所有点都在 ±0.1 范围内说明 percent 没有乘以 100，拒绝写缓存
        max_abs = max(abs(p.get("percent", 0) or 0) for p in points)
        if max_abs < 0.1:
            logger.warning(
                "intraday points for %s look like fractions not percentages "
                "(max_abs=%.5f), skipping cache write",
                cache_key,
                max_abs,
            )
        else:
            save_spot_snapshot(
                cache_key,
                {
                    "points": points,
                    "note": note,
                    "session_date": trade_date,
                    "close_change_percent": close_change_percent,
                },
            )
    return points, note, trade_date, close_change_percent


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
    canon = get_intraday_canonical_sector(source_name) or get_canonical_sector(source_name)
    symbol = (
        (canon.source_code if canon is not None else None)
        or _index_symbol_for_name(source_name)
    )
    secid = canon.eastmoney_secid if canon is not None else ""
    source_code = canon.source_code if canon is not None else symbol

    points = _fetch_intraday_minute_chain(
        secid,
        source_code=source_code,
        trade_date=trade_date,
    )
    if points:
        return points

    if symbol:
        # AkShare index_zh_a_hist_min_em 仅支持主流上证/深证指数（如 000001）；
        # 中证指数（93xxxx）不在其支持范围，调用会静默返回空 DataFrame，跳过。
        if symbol.startswith("93"):
            logger.debug(
                "skipping akshare index_zh_a_hist_min_em for CSI index %s (not supported)",
                symbol,
            )
        else:
            try:
                frame = _call_akshare_index_min(symbol)
                parsed = _points_from_minute_frame(frame)
                if parsed:
                    return parsed
                logger.debug(
                    "akshare index intraday returned empty for %s", symbol
                )
            except Exception as exc:
                logger.debug(
                    "akshare index intraday fallback failed for %s: %s", symbol, exc
                )
    return []


def _fetch_intraday_minute_chain(
    secid: str,
    *,
    source_code: str | None,
    trade_date: str | None,
) -> list[IntradayPoint]:
    """仅分钟/ trends2（09:30–15:00），不走日 K。"""
    code = (source_code or "").strip()
    candidates: list[tuple[str, str | None]] = []
    if secid.strip():
        candidates.append((secid.strip(), code or None))
    if code and not any(c == code for _, c in candidates):
        candidates.append(("", code))

    settings = get_settings()
    browser_timeout = max(settings.sector_quotes_browser_timeout_seconds, 15.0)

    for candidate_secid, candidate_code in candidates:
        points = fetch_eastmoney_intraday_trends(
            candidate_secid,
            source_code=candidate_code,
            trade_date=trade_date,
            timeout=8.0,
            max_retries=1,
        )
        if points:
            return points

        if settings.sector_quotes_browser_enabled:
            resolved_secid = candidate_secid
            if not resolved_secid and candidate_code:
                if candidate_code.upper().startswith("BK"):
                    resolved_secid = f"90.{candidate_code}"
                elif candidate_code.isdigit():
                    resolved_secid = f"2.{candidate_code}"
            if resolved_secid:
                points = fetch_intraday_via_browser_command(
                    resolved_secid,
                    source_code=candidate_code,
                    trade_date=trade_date,
                    timeout_seconds=browser_timeout,
                )
                if points:
                    return points

    return []


def _fetch_board_intraday(
    source_type: str,
    source_name: str,
    *,
    trade_date: str | None = None,
) -> list[IntradayPoint]:
    index_canon = get_intraday_canonical_sector(source_name)
    if index_canon is not None and index_canon.source_type == "index":
        points = _fetch_intraday_minute_chain(
            index_canon.eastmoney_secid,
            source_code=index_canon.source_code,
            trade_date=trade_date,
        )
        if points:
            return points

    canon = get_canonical_sector(source_name)
    if canon is not None and canon.source_type == "concept":
        try:
            frame = _call_akshare_board_min("concept", canon.source_name)
            parsed = _points_from_minute_frame(frame)
            if parsed:
                return parsed
        except Exception as exc:
            logger.debug("akshare concept intraday for %s: %s", canon.source_name, exc)

    if canon is not None:
        points = _fetch_intraday_minute_chain(
            canon.eastmoney_secid,
            source_code=canon.source_code,
            trade_date=trade_date,
        )
        if points:
            return points

    points = _fetch_intraday_minute_chain(
        "",
        source_code=source_name,
        trade_date=trade_date,
    )
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
        "中证半导体": "931865",
        "中证半导": "931865",
        "半导体": "931865",
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
