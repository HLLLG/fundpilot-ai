from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.trading_session import CN_TZ, build_trading_session

IntradayPoint = dict[str, str | float]


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

    cache_key = f"intraday:{source_type}:{label}:{trade_date}"
    long_ttl = session_kind in {"non_trading_day", "trading_day_after_close"}

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=86400.0 if long_ttl else 60.0)
        if cached is not None and cached.get("points"):
            note = cached.get("note")
            if long_ttl and not note:
                note = f"展示 {trade_date} 分时（当前非盘中）"
            return cached.get("points", []), note, trade_date

    live_allowed = session_kind in {"trading_day_intraday", "trading_day_pre_close"}
    points: list[IntradayPoint] = []
    note: str | None = None

    if live_allowed or force_refresh:
        try:
            if source_type == "index":
                points = _fetch_index_intraday(label)
            else:
                points = _fetch_board_intraday(source_type, label)
        except Exception as exc:
            note = f"分时数据获取失败：{exc}"

    if not points and not force_refresh:
        stale = get_spot_snapshot(cache_key, ttl_seconds=86400.0 * 7)
        if stale and stale.get("points"):
            return (
                stale.get("points", []),
                stale.get("note") or f"展示 {trade_date} 缓存分时",
                trade_date,
            )

    if not points:
        note = note or (
            "暂无分时数据（非交易时段或数据源未返回）"
            if not live_allowed
            else "暂无分时数据"
        )
    elif not live_allowed:
        note = f"展示 {trade_date} 分时（当前非盘中）"

    save_spot_snapshot(
        cache_key,
        {"points": points, "note": note, "session_date": trade_date},
    )
    return points, note, trade_date


def _effective_trade_date(session: dict) -> str:
    moment = datetime.now(CN_TZ)
    today = moment.date()
    if session["session_kind"] in {"trading_day_intraday", "trading_day_pre_close"}:
        return today.isoformat()
    if session["session_kind"] == "trading_day_after_close":
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


def _fetch_index_intraday(source_name: str) -> list[IntradayPoint]:
    import akshare as ak  # type: ignore[import-not-found]

    symbol = _index_symbol_for_name(source_name)
    if not symbol:
        return []

    frame = ak.stock_zh_index_hist_min_em(symbol=symbol, period="1")
    return _points_from_minute_frame(frame)


def _fetch_board_intraday(source_type: str, source_name: str) -> list[IntradayPoint]:
    import akshare as ak  # type: ignore[import-not-found]

    if source_type == "concept":
        frame = ak.stock_board_concept_hist_min_em(symbol=source_name, period="1")
    else:
        frame = ak.stock_board_industry_hist_min_em(symbol=source_name, period="1")
    return _points_from_minute_frame(frame)


def _points_from_minute_frame(frame) -> list[IntradayPoint]:
    if frame is None or frame.empty:
        return []

    points: list[IntradayPoint] = []
    for _, row in frame.iterrows():
        time_value = _cell(row, "时间", "time")
        percent = _cell_float(row, "涨跌幅", "change", "涨跌")
        if time_value is None or percent is None:
            continue
        points.append({"time": str(time_value).strip(), "percent": percent})

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
        percent = round((price / baseline - 1) * 100, 4)
        rebuilt.append({"time": str(time_value).strip(), "percent": percent})
    return rebuilt


def _index_symbol_for_name(source_name: str) -> str | None:
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
