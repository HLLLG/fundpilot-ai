from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
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
    minutes_to_close = (
        int((close_dt - moment).total_seconds() // 60) if is_trading_day else None
    )

    if not is_trading_day:
        session_kind = "non_trading_day"
        decision_window = "非交易日：结论供复盘与下一交易日预案，勿当作收盘前即时指令。"
    elif moment.time() >= MARKET_CLOSE:
        session_kind = "trading_day_after_close"
        decision_window = "已收盘：可复盘当日表现，加仓/减仓指令默认顺延至下一交易日开盘前后再评估。"
    elif moment.time() >= PRE_CLOSE_FOCUS:
        session_kind = "trading_day_pre_close"
        decision_window = "收盘前决策窗口（约 14:30–15:00）：须结合当日板块涨跌与要闻，优先保守动作。"
    else:
        session_kind = "trading_day_intraday"
        decision_window = "盘中：板块涨跌为实时值，持有收益多为昨日结算；收盘前需再次确认当日收益列。"

    return {
        "timezone": "Asia/Shanghai",
        "local_datetime": moment.strftime("%Y-%m-%d %H:%M"),
        "calendar_date": today.isoformat(),
        "is_trading_day": is_trading_day,
        "session_kind": session_kind,
        "minutes_to_close": minutes_to_close,
        "decision_window": decision_window,
        "market_close_time": "15:00",
    }


@lru_cache(maxsize=1)
def _trade_date_set() -> frozenset[str] | None:
    try:
        import akshare as ak

        frame = ak.tool_trade_date_hist_sina()
        column = "trade_date" if "trade_date" in frame.columns else frame.columns[0]
        return frozenset(str(value)[:10] for value in frame[column].tolist())
    except Exception:
        return None


def _is_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    trade_dates = _trade_date_set()
    if trade_dates is None:
        return True
    return day.isoformat() in trade_dates
