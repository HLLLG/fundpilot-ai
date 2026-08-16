"""主题板块连续涨跌天数：现货 clist 没有这个字段，由系统按日涨跌自己记账计算。

口径：从最近一个有涨跌的**交易日**起，连涨记正、连跌记负。
例如连涨 3 天为 ``+3``，连跌 3 天为 ``-3``，当天持平为 ``0``。
中间缺了交易日就不能把两头的上涨连在一起——账本漏记 8/10–8/13、只留下 8/7 和 8/14
时，连涨是 1 而不是 7。
没有任何日涨跌记录则空（前端显示 —）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.sector_quote_cache import get_spot_snapshot_any_age, save_spot_snapshot

_LEDGER_KEY = "theme:daily_change:v1"
_MAX_LEDGER_DAYS = 40
_MIN_HISTORY_FOR_BACKFILL = 8


def consecutive_up_days(daily_changes: list[tuple[str, float | None]]) -> int | None:
    """``daily_changes`` 按日期升序。无记录返回 None；连涨为正、连跌为负、持平为 0。

    相邻两条记录之间若隔着未入账的交易日，视为连涨中断，不能跳过缺口继续数。
    """
    if not daily_changes:
        return None
    streak = 0
    direction: int | None = None
    seen = False
    newer_date: str | None = None
    for day, change in reversed(daily_changes):
        if change is None:
            if seen:
                break
            continue
        seen = True
        if change == 0:
            return 0
        sign = 1 if change > 0 else -1
        if direction is None:
            direction = sign
        if sign != direction:
            break
        if newer_date is not None and _has_trading_gap(str(day or ""), newer_date):
            break
        streak += 1
        newer_date = str(day or "")[:10]
    if not seen:
        return None
    return (direction or 0) * streak


def attach_consecutive_up_days(
    items: list[dict[str, Any]],
    *,
    trade_date: str,
    persist: bool = False,
) -> None:
    """给主题行挂 ``consecutive_up_days``；刷新路径才写回日涨跌账本。"""
    day = str(trade_date or "")[:10]
    boards = _load_ledger()
    dirty = False

    for item in items:
        label = str(item.get("sector_label") or "").strip()
        if not label:
            item["consecutive_up_days"] = None
            continue
        history = list(boards.get(label) or [])
        incoming = (
            _history_from_flow_cache(item)
            if _should_backfill(history, day)
            else []
        )
        today_change = _as_float(item.get("change_1d_percent"))
        if day and today_change is not None:
            incoming.append((day, today_change))
        merged = _merge_history(history, incoming)
        if persist and merged != history:
            boards[label] = merged
            dirty = True
        item["consecutive_up_days"] = consecutive_up_days(
            [(str(row.get("date") or ""), _as_float(row.get("change"))) for row in merged]
        )

    if persist and dirty:
        _save_ledger(boards)


def _history_from_flow_cache(item: dict[str, Any]) -> list[tuple[str, float]]:
    code = str(item.get("flow_source_code") or "").strip()
    if not code:
        return []
    # 账本按展示涨幅（source_code 口径）逐日记账。只有当资金流板块就是展示标的本身
    # （概念/行业主题，source_code == flow_source_code）时，BK 资金流日线里的涨跌才与
    # 账本同源、可用于冷启动回填；指数主题（涨幅=中证指数、资金=东财 BK）两个成分
    # 篮子的涨跌不能混进同一条连涨序列——宁可从当日起重新记。
    if code != str(item.get("source_code") or "").strip():
        return []
    try:
        from app.services.board_fund_flow_history import get_board_flow_series_cache_only

        series = get_board_flow_series_cache_only(code)
    except Exception:
        return []
    points: list[tuple[str, float]] = []
    for row in series:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "")[:10]
        change = _as_float(row.get("change_percent"))
        if day and change is not None:
            points.append((day, change))
    return points


def _merge_history(
    existing: list[dict[str, Any]],
    incoming: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    by_date: dict[str, float] = {}
    for row in existing:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "")[:10]
        change = _as_float(row.get("change"))
        if day and change is not None:
            by_date[day] = change
    for day, change in incoming:
        cleaned = str(day or "")[:10]
        if cleaned and change is not None:
            by_date[cleaned] = change
    return [
        {"date": day, "change": by_date[day]}
        for day in sorted(by_date)[-_MAX_LEDGER_DAYS:]
    ]


def _load_ledger() -> dict[str, list[dict[str, Any]]]:
    snapshot = get_spot_snapshot_any_age(_LEDGER_KEY) or {}
    boards = snapshot.get("boards")
    if not isinstance(boards, dict):
        return {}
    cleaned: dict[str, list[dict[str, Any]]] = {}
    for label, rows in boards.items():
        key = str(label or "").strip()
        if key and isinstance(rows, list):
            cleaned[key] = [row for row in rows if isinstance(row, dict)]
    return cleaned


def _save_ledger(boards: dict[str, list[dict[str, Any]]]) -> None:
    save_spot_snapshot(
        _LEDGER_KEY,
        {
            "boards": boards,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _should_backfill(history: list[dict[str, Any]], trade_date: str) -> bool:
    if len(history) < _MIN_HISTORY_FOR_BACKFILL:
        return True
    return _ledger_has_gap(history, trade_date)


def _ledger_has_gap(history: list[dict[str, Any]], trade_date: str) -> bool:
    dates = [str(row.get("date") or "")[:10] for row in history if str(row.get("date") or "")[:10]]
    if trade_date:
        dates.append(trade_date)
    unique = sorted({day for day in dates if day})
    if len(unique) < 2:
        return False
    return any(_has_trading_gap(older, newer) for older, newer in zip(unique, unique[1:]))


def _has_trading_gap(older: str, newer: str) -> bool:
    """两条记录之间只要隔着工作日，就不能当成连续交易日。

    周末（周五→周一）中间没有工作日，允许连上。节假日周五休市时会略保守地断开，
    总好过把漏记的一整周涨跌拼成假连涨。
    """
    try:
        start = date.fromisoformat(str(older)[:10])
        end = date.fromisoformat(str(newer)[:10])
    except ValueError:
        return False
    if end <= start:
        return False
    cursor = start + timedelta(days=1)
    while cursor < end:
        if cursor.weekday() < 5:
            return True
        cursor += timedelta(days=1)
    return False
