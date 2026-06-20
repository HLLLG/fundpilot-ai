from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.services.trade_calendar_cache import get_trade_date_set

CN_TZ = ZoneInfo("Asia/Shanghai")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(15, 0)
PRE_CLOSE_FOCUS = time(14, 30)


def build_trading_session(when: datetime | None = None) -> dict:
    moment = when or datetime.now(CN_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=CN_TZ)
    else:
        moment = moment.astimezone(CN_TZ)

    today = moment.date()
    is_trading_day = _is_trading_day(today)
    close_dt = datetime.combine(today, MARKET_CLOSE, tzinfo=CN_TZ)
    market_open = datetime.combine(today, MARKET_OPEN, tzinfo=CN_TZ)
    in_session = (
        is_trading_day
        and moment.time() >= MARKET_OPEN
        and moment.time() < MARKET_CLOSE
    )
    minutes_to_close = int((close_dt - moment).total_seconds() // 60) if in_session else None

    if not is_trading_day:
        session_kind = "non_trading_day"
        decision_window = "非交易日：结论供复盘与下一交易日预案，勿当作收盘前即时指令。"
    elif moment.time() < MARKET_OPEN:
        session_kind = "trading_day_pre_open"
        decision_window = (
            "开盘前：展示上一交易日结算数据；当日板块涨跌与分时将于 9:30 开盘后更新。"
        )
    elif moment.time() >= MARKET_CLOSE:
        session_kind = "trading_day_after_close"
        decision_window = "已收盘：可复盘当日表现，加仓/减仓指令默认顺延至下一交易日开盘前后再评估。"
    elif moment.time() >= PRE_CLOSE_FOCUS:
        session_kind = "trading_day_pre_close"
        decision_window = "收盘前决策窗口（约 14:30–15:00）：须结合当日板块涨跌与要闻，优先保守动作。"
    else:
        session_kind = "trading_day_intraday"
        decision_window = "盘中：板块涨跌为实时值，持有收益多为昨日结算；收盘前需再次确认当日收益列。"

    effective_trade_date = get_effective_trade_date(session_kind=session_kind, today=today)

    return {
        "timezone": "Asia/Shanghai",
        "local_datetime": moment.strftime("%Y-%m-%d %H:%M"),
        "calendar_date": today.isoformat(),
        "effective_trade_date": effective_trade_date,
        "is_trading_day": is_trading_day,
        "session_kind": session_kind,
        "minutes_to_close": minutes_to_close,
        "decision_window": decision_window,
        "market_close_time": "15:00",
        "market_open_time": "09:30",
    }


def get_effective_trade_date(
    *,
    session_kind: str | None = None,
    today: date | None = None,
) -> str:
    """板块涨跌/估算当日收益所对应的交易日（非交易日回溯至上一交易日）。"""
    moment = datetime.now(CN_TZ)
    anchor = today or moment.date()
    kind = session_kind
    if kind is None:
        kind = build_trading_session(moment)["session_kind"]

    if kind in {
        "trading_day_intraday",
        "trading_day_pre_close",
        "trading_day_after_close",
    }:
        return anchor.isoformat()

    cursor = anchor
    for _ in range(14):
        cursor -= timedelta(days=1)
        if _is_trading_day(cursor):
            return cursor.isoformat()
    return anchor.isoformat()


def get_previous_trade_date(effective_trade_date: str | None = None) -> str | None:
    """给定有效交易日，返回其上一交易日（养基宝「昨日收益」日期语义）。"""
    anchor = effective_trade_date or get_effective_trade_date()
    try:
        cursor = date.fromisoformat(anchor)
    except ValueError:
        return None
    for _ in range(14):
        cursor -= timedelta(days=1)
        if _is_trading_day(cursor):
            return cursor.isoformat()
    return None


def _is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    trade_dates = get_trade_date_set()
    if trade_dates is None:
        return True
    return day.isoformat() in trade_dates


def _parse_trade_time(trade_time: str) -> datetime:
    """容错解析成交时间字符串（"YYYY-MM-DD HH:MM:SS" 优先，兼容少量变体）。"""
    text = (trade_time or "").strip().replace("/", "-").replace("T", " ")
    text = " ".join(text.split())
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    # 末位兜底：尝试 fromisoformat（去掉时区信息）
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=None)
    except ValueError as exc:
        raise ValueError(f"无法解析成交时间：{trade_time!r}") from exc


def _next_trading_day(day: date) -> date:
    cursor = day
    for _ in range(366):
        cursor += timedelta(days=1)
        if _is_trading_day(cursor):
            return cursor
    return day + timedelta(days=1)


def resolve_confirm_date(trade_time: str, *, today: date | None = None) -> str:
    """成交时间 -> 确认净值交易日。

    <15:00 且当天为交易日 -> 当天；否则（≥15:00 或非交易日）-> 之后的下一个交易日。
    返回 ISO date 字符串。``today`` 预留给调用方覆盖"当前日"语义，规则本身只依赖成交时间。
    """
    moment = _parse_trade_time(trade_time)
    trade_day = moment.date()
    if moment.time() < MARKET_CLOSE and _is_trading_day(trade_day):
        return trade_day.isoformat()
    return _next_trading_day(trade_day).isoformat()
