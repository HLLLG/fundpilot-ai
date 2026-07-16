from __future__ import annotations

import calendar
import contextvars
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Literal

from app.database import get_portfolio_intraday_curve_entry, save_portfolio_intraday_curve
from app.models import FundProfile, Holding
from app.services.fund_profile import infer_intraday_index_from_sector
from app.services.index_daily_client import fetch_index_daily_history
from app.services.sector_canonical import get_intraday_canonical_sector
from app.services.sector_intraday_provider import fetch_sector_intraday
from app.services.sector_quote_label import sector_quote_lookup_label
from app.services.trade_calendar_cache import get_trade_date_set
from app.services.trading_session import build_trading_session, get_effective_trade_date

ProfitRange = Literal["today", "week", "month", "year", "all"]

_INDEX_INTRADAY_CACHE: tuple[float, list[dict]] | None = None
_INDEX_INTRADAY_TTL_SECONDS = 60


def _parse_clock_minutes(time: str) -> int:
    parts = time.strip().split(":")
    if len(parts) < 2:
        return 0
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return 0


def _time_sort_key(time: str) -> int:
    return _parse_clock_minutes(time)


def _percent_at_time(time_map: dict[str, float], target: str) -> float:
    if target in time_map:
        return time_map[target]
    target_mins = _parse_clock_minutes(target)
    best_time: str | None = None
    best_mins = -1
    for time_key, _ in time_map.items():
        mins = _parse_clock_minutes(time_key)
        if mins <= target_mins and mins >= best_mins:
            best_mins = mins
            best_time = time_key
    if best_time is None:
        return 0.0
    return time_map[best_time]


def _resolve_intraday_for_holding(
    holding: Holding,
    profile: FundProfile | None,
) -> tuple[str, str] | None:
    index_name = (holding.intraday_index_name or "").strip()
    if not index_name and profile is not None:
        index_name = (profile.intraday_index_name or "").strip()
    if index_name:
        canonical = get_intraday_canonical_sector(index_name)
        if canonical is not None:
            return canonical.source_type, canonical.source_name

    board_name = (holding.sector_name or "").strip()
    if board_name:
        mapped = infer_intraday_index_from_sector(board_name)
        if mapped:
            canonical = get_intraday_canonical_sector(mapped)
            if canonical is not None:
                return canonical.source_type, canonical.source_name

        canonical = get_intraday_canonical_sector(board_name)
        if canonical is not None:
            return canonical.source_type, canonical.source_name

    label = sector_quote_lookup_label(holding, profile=profile)
    if not label:
        return None

    canon = get_intraday_canonical_sector(label)
    if canon is not None:
        return canon.source_type, canon.source_name

    # 基金名称、投资风格或地域分类并不等于可查询的东财概念板块。例如
    # “海外基金”“信澳业绩驱动”“全球高端制造”没有稳定 secid；把它们直接交给
    # AkShare 只会产生空子进程和重复重试。无可靠映射时明确返回 None，由组合曲线
    # 使用已有缓存/大盘基准降级，不伪造板块分时。
    return None


def _holdings_intraday_fingerprint(
    holdings: list[Holding],
    profiles_by_code: dict[str, FundProfile] | None = None,
) -> str:
    profiles_by_code = profiles_by_code or {}
    rows: list[dict[str, object]] = []
    for holding in sorted(holdings, key=lambda item: item.fund_code):
        profile = profiles_by_code.get(holding.fund_code)
        query = _resolve_intraday_for_holding(holding, profile)
        rows.append(
            {
                "fund_code": holding.fund_code,
                "amount": round(float(holding.holding_amount), 2),
                "sector": holding.sector_name,
                "index": holding.intraday_index_name,
                "query": query,
            }
        )
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _fetch_cached_index_intraday() -> list[dict]:
    global _INDEX_INTRADAY_CACHE
    now = time.time()
    if (
        _INDEX_INTRADAY_CACHE is not None
        and now - _INDEX_INTRADAY_CACHE[0] < _INDEX_INTRADAY_TTL_SECONDS
    ):
        return _INDEX_INTRADAY_CACHE[1]
    points, *_ = fetch_sector_intraday("index", "上证指数")
    _INDEX_INTRADAY_CACHE = (now, points)
    return points


def _fetch_weighted_intraday_map(
    holding: Holding,
    profile: FundProfile | None,
    total: float,
) -> tuple[float, dict[str, float]] | None:
    query = _resolve_intraday_for_holding(holding, profile)
    if query is None:
        return None
    source_type, source_name = query
    points, *_ = fetch_sector_intraday(source_type, source_name)
    if len(points) < 2:
        return None
    time_map = {str(point["time"]): float(point["percent"]) for point in points}
    return (holding.holding_amount / total, time_map)


def _blend_portfolio_rows(
    holdings: list[Holding],
    profiles_by_code: dict[str, FundProfile] | None = None,
) -> list[dict[str, float | str | None]]:
    profiles_by_code = profiles_by_code or {}
    total = sum(holding.holding_amount for holding in holdings if holding.holding_amount > 0)
    if total <= 0:
        return []

    jobs = [
        (holding, profiles_by_code.get(holding.fund_code))
        for holding in holdings
        if holding.holding_amount > 0
    ]
    weighted_maps: list[tuple[float, dict[str, float]]] = []
    if not jobs:
        return []

    max_workers = min(8, len(jobs))
    # ThreadPoolExecutor 不会自动把调用线程的 contextvars（如当前用户上下文）带到
    # worker 线程——如果某只持仓在 profiles_by_code 里没有档案（比如刚新增、还没
    # 跑过 OCR/持仓穿透的基金），_resolve_intraday_for_holding 内部会尝试重新查
    # 一次基金档案，读用户上下文时就会因为"未设置当前用户上下文"直接抛异常，
    # 拖垮整批 holdings 的板块行情刷新（包括其它本该成功的持仓）。用
    # copy_context() 把当前上下文带进每个 worker 即可修复——但同一个 Context
    # 对象不能被多个线程同时 run（会抛 "cannot enter context: ... is already
    # entered"），所以要给每个并发任务单独 copy 一份，不能共用一个 ctx。
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(
                contextvars.copy_context().run,
                _fetch_weighted_intraday_map,
                holding,
                profile,
                total,
            )
            for holding, profile in jobs
        ]
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                weighted_maps.append(result)

    if not weighted_maps:
        return []

    all_times = sorted(
        {time_key for _, time_map in weighted_maps for time_key in time_map},
        key=_time_sort_key,
    )
    portfolio_rows: list[dict[str, float | str | None]] = []
    for time_key in all_times:
        portfolio_percent = sum(
            weight * _percent_at_time(time_map, time_key)
            for weight, time_map in weighted_maps
        )
        portfolio_rows.append(
            {
                "time": time_key,
                "portfolio_percent": round(portfolio_percent, 4),
                "index_percent": None,
            }
        )
    return portfolio_rows


def blend_portfolio_intraday(
    holdings: list[Holding],
    profiles_by_code: dict[str, FundProfile] | None = None,
) -> list[dict[str, float | str | None]]:
    portfolio_rows = _blend_portfolio_rows(holdings, profiles_by_code)
    if not portfolio_rows:
        return []
    index_points = _fetch_cached_index_intraday()
    return _merge_index_intraday(portfolio_rows, index_points)


def _merge_index_intraday(
    portfolio_rows: list[dict[str, float | str | None]],
    index_points: list[dict],
) -> list[dict[str, float | str | None]]:
    if not portfolio_rows:
        return portfolio_rows
    index_map = {str(point["time"]): float(point["percent"]) for point in index_points}
    merged: list[dict[str, float | str | None]] = []
    for row in portfolio_rows:
        copy = dict(row)
        time_key = str(copy.get("time") or "")
        copy["index_percent"] = index_map.get(time_key)
        if copy["index_percent"] is None and index_map:
            copy["index_percent"] = round(_percent_at_time(index_map, time_key), 4)
        merged.append(copy)
    return merged


def persist_intraday_curve(
    holdings: list[Holding],
    profiles_by_code: dict[str, FundProfile] | None = None,
) -> list[dict[str, float | str | None]]:
    session = build_trading_session()
    trade_date = get_effective_trade_date(session_kind=session["session_kind"])
    fingerprint = _holdings_intraday_fingerprint(holdings, profiles_by_code)
    portfolio_rows = _blend_portfolio_rows(holdings, profiles_by_code)
    if portfolio_rows:
        save_portfolio_intraday_curve(
            trade_date,
            portfolio_rows,
            holdings_fingerprint=fingerprint,
        )
    return blend_portfolio_intraday(holdings, profiles_by_code)


def load_or_build_intraday_curve(
    holdings: list[Holding],
    profiles_by_code: dict[str, FundProfile] | None = None,
    *,
    cache_only: bool = False,
) -> tuple[list[dict[str, float | str | None]], str | None]:
    session = build_trading_session()
    trade_date = get_effective_trade_date(session_kind=session["session_kind"])
    fingerprint = _holdings_intraday_fingerprint(holdings, profiles_by_code)
    cached_entry = get_portfolio_intraday_curve_entry(trade_date)
    if cached_entry and len(cached_entry["points"]) >= 2:
        if cached_entry.get("holdings_fingerprint") == fingerprint:
            index_points = _fetch_cached_index_intraday()
            return _merge_index_intraday(cached_entry["points"], index_points), trade_date
    if cache_only:
        return [], trade_date
    portfolio_rows = _blend_portfolio_rows(holdings, profiles_by_code)
    if portfolio_rows:
        save_portfolio_intraday_curve(
            trade_date,
            portfolio_rows,
            holdings_fingerprint=fingerprint,
        )
        index_points = _fetch_cached_index_intraday()
        return _merge_index_intraday(portfolio_rows, index_points), trade_date
    return [], trade_date


def filter_snapshots_by_range(
    rows: list[dict],
    profit_range: ProfitRange,
    *,
    anchor_date: date | None = None,
) -> list[dict]:
    if not rows:
        return []
    anchor = anchor_date or date.today()
    range_start: date | None = None
    if profit_range == "today":
        range_start = anchor
    elif profit_range == "week":
        range_start = anchor - timedelta(days=anchor.weekday())
    elif profit_range == "month":
        range_start = anchor.replace(day=1)
    elif profit_range == "year":
        range_start = anchor.replace(month=1, day=1)

    dated_rows: list[tuple[date, dict]] = []
    for row in rows:
        try:
            snapshot_date = date.fromisoformat(str(row.get("snapshot_date") or "")[:10])
        except ValueError:
            continue
        if snapshot_date > anchor:
            continue
        if range_start is not None and snapshot_date < range_start:
            continue
        dated_rows.append((snapshot_date, row))

    dated_rows.sort(key=lambda item: item[0])
    return [row for _, row in dated_rows]


def build_daily_trend_series(
    snapshots: list[dict],
    *,
    index_symbol: str = "000001",
) -> list[dict[str, float | str | None]]:
    if len(snapshots) < 2:
        if len(snapshots) == 1:
            row = snapshots[0]
            return [
                {
                    "date": row.get("snapshot_date"),
                    "portfolio_percent": float(row.get("daily_return_percent") or 0),
                    "index_percent": _index_return_for_date(
                        str(row.get("snapshot_date") or ""),
                        index_symbol,
                    ),
                }
            ]
        return []

    index_history = fetch_index_daily_history(index_symbol, trading_days=400)
    index_by_date = _index_daily_change_lookup(index_history)

    cumulative_portfolio_factor = 1.0
    cumulative_index_factor = 1.0
    series: list[dict[str, float | str | None]] = []
    for row in snapshots:
        day = str(row.get("snapshot_date") or "")
        daily_return = row.get("daily_return_percent")
        if daily_return is not None:
            cumulative_portfolio_factor *= 1.0 + float(daily_return) / 100.0
        index_change = index_by_date.get(day)
        if index_change is not None:
            cumulative_index_factor *= 1.0 + float(index_change) / 100.0
        series.append(
            {
                "date": day,
                "portfolio_percent": round((cumulative_portfolio_factor - 1.0) * 100.0, 4),
                "index_percent": round((cumulative_index_factor - 1.0) * 100.0, 4),
            }
        )
    return series


def _index_daily_change_lookup(index_history: dict | None) -> dict[str, float]:
    if not index_history or not index_history.get("data"):
        return {}
    rows = index_history["data"]
    lookup: dict[str, float] = {}
    for index, row in enumerate(rows):
        day = str(row.get("date") or "")[:10]
        if index == 0:
            lookup[day] = 0.0
            continue
        prev_close = float(rows[index - 1]["close"])
        close = float(row["close"])
        if prev_close > 0:
            lookup[day] = round((close / prev_close - 1) * 100, 4)
    return lookup


def _index_return_for_date(day: str, index_symbol: str) -> float | None:
    history = fetch_index_daily_history(index_symbol, trading_days=60)
    lookup = _index_daily_change_lookup(history)
    return lookup.get(day)


def build_profit_trend(
    *,
    profit_range: ProfitRange,
    snapshots: list[dict],
    holdings: list[Holding],
    profiles_by_code: dict[str, FundProfile] | None = None,
    intraday_cache_only: bool = False,
) -> dict:
    session = build_trading_session()
    trade_date = get_effective_trade_date(session_kind=session["session_kind"])

    if profit_range == "today":
        points, session_date = load_or_build_intraday_curve(
            holdings,
            profiles_by_code,
            cache_only=intraday_cache_only,
        )
        return {
            "kind": "intraday",
            "trade_date": session_date or trade_date,
            "points": [
                {
                    "time": row.get("time"),
                    "portfolio_percent": row.get("portfolio_percent"),
                    "index_percent": row.get("index_percent"),
                }
                for row in points
            ],
        }

    try:
        anchor_date = date.fromisoformat(str(trade_date)[:10])
    except ValueError:
        anchor_date = date.today()
    filtered = filter_snapshots_by_range(
        snapshots,
        profit_range,
        anchor_date=anchor_date,
    )
    series = build_daily_trend_series(filtered)
    return {
        "kind": "daily",
        "points": [
            {
                "date": row.get("date"),
                "portfolio_percent": row.get("portfolio_percent"),
                "index_percent": row.get("index_percent"),
            }
            for row in series
        ],
    }


def build_daily_top5(holdings: list[Holding]) -> dict[str, list[dict]]:
    rows: list[dict] = []
    for holding in holdings:
        if holding.daily_profit is None:
            continue
        rows.append(
            {
                "fund_code": holding.fund_code,
                "fund_name": holding.fund_name,
                "daily_profit": round(float(holding.daily_profit), 2),
            }
        )
    gainers = sorted(
        [row for row in rows if row["daily_profit"] > 0],
        key=lambda row: row["daily_profit"],
        reverse=True,
    )[:5]
    losers = sorted(
        [row for row in rows if row["daily_profit"] < 0],
        key=lambda row: row["daily_profit"],
    )[:5]
    return {"gainers": gainers, "losers": losers}


def build_calendar_month(
    *,
    year: int,
    month: int,
    snapshots: list[dict],
    trade_dates: frozenset[str] | None = None,
    holdings: list[Holding] | None = None,
) -> dict:
    from app.services.holding_estimates import (
        compute_portfolio_daily_return_percent,
        portfolio_official_nav_settled,
        sum_daily_profit,
    )

    trade_dates = trade_dates or get_trade_date_set()
    snapshot_by_date = {str(row.get("snapshot_date")): row for row in snapshots}
    today = date.today()
    today_key = today.isoformat()
    official_today = portfolio_official_nav_settled(holdings or [])
    _, days_in_month = calendar.monthrange(year, month)
    index_lookup = _index_daily_change_lookup(
        fetch_index_daily_history("000001", trading_days=400)
    )

    days: list[dict] = []
    month_profit = 0.0
    month_returns: list[float] = []
    index_returns: list[float] = []

    for day_num in range(1, days_in_month + 1):
        current = date(year, month, day_num)
        key = current.isoformat()
        weekday = current.weekday()
        snapshot = snapshot_by_date.get(key)
        is_trading = key in trade_dates if trade_dates else weekday < 5
        daily_profit = snapshot.get("daily_profit") if snapshot else None
        daily_return = snapshot.get("daily_return_percent") if snapshot else None
        # 非交易日（周末、法定节假日）收益为 0，不沿用上一交易日快照中的结算值。
        if not is_trading and current <= today:
            daily_profit = 0.0
            daily_return = 0.0
        elif not is_trading:
            daily_profit = None
            daily_return = None

        is_pending_update = False
        if key == today_key and is_trading and holdings:
            if not official_today:
                daily_profit = None
                daily_return = None
                is_pending_update = True
            else:
                daily_profit = sum_daily_profit(holdings)
                daily_return = compute_portfolio_daily_return_percent(holdings, daily_profit)

        if is_trading and daily_profit is not None and not is_pending_update:
            month_profit += float(daily_profit)
        if is_trading and daily_return is not None and not is_pending_update:
            month_returns.append(float(daily_return))
        index_return = index_lookup.get(key)
        if index_return is not None and is_trading:
            index_returns.append(float(index_return))

        days.append(
            {
                "date": key,
                "day": day_num,
                "weekday": weekday,
                "is_trading_day": is_trading,
                "is_today": key == today.isoformat(),
                "daily_profit": daily_profit,
                "daily_return_percent": daily_return,
                "is_holiday": weekday < 5 and not is_trading,
                "is_pending_update": is_pending_update,
            }
        )

    return {
        "year": year,
        "month": month,
        "days": days,
        "month_cumulative_profit": round(month_profit, 2),
        "month_index_return_percent": (
            round(_compound_return_percent(index_returns), 2) if index_returns else None
        ),
        "month_cumulative_return_percent": (
            round(_compound_return_percent(month_returns), 2) if month_returns else None
        ),
    }


def summarize_trend_footer(
    trend: dict,
    *,
    summary_daily_return: float | None,
) -> dict[str, float | None]:
    points = trend.get("points") or []
    if not points:
        return {
            "portfolio_return_percent": (
                summary_daily_return if trend.get("kind") == "intraday" else None
            ),
            "index_return_percent": None,
            "alpha_percent": None,
        }

    if trend.get("kind") == "intraday":
        last = points[-1]
        portfolio = last.get("portfolio_percent")
        index = last.get("index_percent")
    else:
        last = points[-1]
        portfolio = last.get("portfolio_percent")
        index = last.get("index_percent")

    if portfolio is None and summary_daily_return is not None:
        portfolio = summary_daily_return

    alpha = None
    if portfolio is not None and index is not None:
        alpha = round(float(portfolio) - float(index), 2)

    return {
        "portfolio_return_percent": float(portfolio) if portfolio is not None else None,
        "index_return_percent": float(index) if index is not None else None,
        "alpha_percent": alpha,
    }


def default_calendar_anchor() -> tuple[int, int]:
    return date.today().year, date.today().month


def _compound_return_percent(daily_return_percents: list[float]) -> float:
    """把若干日收益率（百分比）复利累乘，返回区间累计收益率（百分比）。"""
    result = 1.0
    for r in daily_return_percents:
        result *= 1.0 + r / 100.0
    return (result - 1.0) * 100.0
