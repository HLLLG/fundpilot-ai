"""美股交易时段检测（含夏令时）。

仿 `trading_session.py`，以 `ZoneInfo("America/New_York")` 自动处理夏令时（DST），
将任意时刻判定为 `pre_market` / `regular` / `after_hours` / `closed` 之一。

时段窗口（美东墙钟时间，按交易日）：
    04:00–09:30 ET → pre_market（盘前交易中）
    09:30–16:00 ET → regular（盘中）
    16:00–20:00 ET → after_hours（盘后）
    其余时间或非交易日 → closed（休市）

判定基于 DST 感知的美东墙钟时间（而非固定 UTC 偏移）：传入的瞬时先 `astimezone`
到 `America/New_York`，`ZoneInfo` 会按该日期是否处于夏令时自动给出正确的墙钟时间，
因此夏令时与标准时下相同墙钟时间得到相同的时段判定。

校验需求：3.1, 3.2, 3.3, 3.4, 3.5, 3.6
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

US_TZ = ZoneInfo("America/New_York")

PRE_MARKET_OPEN = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
AFTER_HOURS_CLOSE = time(20, 0)

# 时段中文标签（需求 3.6）
_LABELS = {
    "pre_market": "盘前交易中",
    "regular": "盘中",
    "after_hours": "盘后",
    "closed": "休市",
}


def detect_us_session(when: datetime | None = None) -> dict:
    """判定给定时刻（默认当前）的美股交易时段。

    Args:
        when: 任意带/不带时区的 `datetime`；不带时区时按美东时区解读。

    Returns:
        dict，含：
            - `session_kind`: pre_market / regular / after_hours / closed 之一
            - `session_label`: 对应中文标签
            - `et_date`: 美东墙钟日期（ISO 字符串）
    """
    moment = when or datetime.now(US_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=US_TZ)
    else:
        moment = moment.astimezone(US_TZ)

    et_date = moment.date()

    if not _is_us_trading_day(et_date):
        kind = "closed"
    else:
        t = moment.time()
        if REGULAR_OPEN <= t < REGULAR_CLOSE:
            kind = "regular"
        elif PRE_MARKET_OPEN <= t < REGULAR_OPEN:
            kind = "pre_market"
        elif REGULAR_CLOSE <= t < AFTER_HOURS_CLOSE:
            kind = "after_hours"
        else:
            kind = "closed"

    return {
        "session_kind": kind,
        "session_label": _LABELS[kind],
        "et_date": et_date.isoformat(),
    }


def _is_us_trading_day(day: date) -> bool:
    """判断给定美东墙钟日期是否为美股交易日。

    已知限制（MVP 务实简化，见设计 §6.1.1）：
        本期仅排除周末（周六/周日）。美股法定节假日（如感恩节、独立日、
        马丁·路德·金日等）**未**接入权威日历，节假日当天会被误判为
        pre_market / regular / after_hours。由于数据源在休市时自然返回
        空/陈旧数据，上层降级逻辑会将其标为 stale/unavailable 而不会编造
        数值，用户仍看到「数据未更新」而非错误数值。
        后续增强（非本期）：可接入静态节假日表或 `pandas-market-calendars`
        （NYSE 日历）以精确判定 closed。
    """
    return day.weekday() < 5
