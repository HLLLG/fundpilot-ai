"""Low-latency sector price proxy built from current large constituents.

The proxy is research evidence only.  It is used when the board's own daily
K-line is unavailable and is labelled explicitly so it cannot be mistaken for
an official benchmark or execution price.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import RLock
from typing import Any

import httpx

from app.services.board_fund_flow_history import resolve_board_flow_code_for_sector
from app.services.eastmoney_spot_client import _COMMON_PARAMS, _EASTMONEY_HEADERS
from app.services.index_daily_client import fetch_index_daily_history
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import get_effective_trade_date

_CLIST_HOSTS = ("push2delay.eastmoney.com", "17.push2.eastmoney.com")
_DEFAULT_MEMBER_COUNT = 8
_MIN_MEMBER_COUNT = 4
_PROXY_CACHE_TTL_SECONDS = 6 * 60 * 60
_CONSTITUENT_CACHE_MAX_ENTRIES = 256
_CONSTITUENT_CACHE: dict[tuple[str, int, int], tuple[dict[str, Any], ...]] = {}
_CONSTITUENT_CACHE_LOCK = RLock()


def fetch_sector_constituent_proxy_series(
    sector_label: str,
    *,
    trading_days: int = 100,
    member_count: int = _DEFAULT_MEMBER_COUNT,
) -> list[dict[str, Any]]:
    board_code, _ = resolve_board_flow_code_for_sector(sector_label)
    if not board_code:
        return []
    days = max(61, min(int(trading_days), 150))
    cache_key = _proxy_cache_key(board_code, days, member_count)
    cached_rows = _cached_proxy_rows(cache_key, allow_same_trade_date_stale=False)
    if cached_rows:
        return cached_rows

    bucket = int(datetime.now(timezone.utc).timestamp() // 3600)
    members = list(_cached_constituents(board_code, member_count, bucket))
    if len(members) < _MIN_MEMBER_COUNT:
        return _cached_proxy_rows(cache_key, allow_same_trade_date_stale=True)

    def load(member: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        symbol = _sina_stock_symbol(str(member.get("code") or ""), member.get("market"))
        payload = fetch_index_daily_history(symbol, trading_days=days) if symbol else None
        return member, list((payload or {}).get("data") or [])

    with ThreadPoolExecutor(max_workers=min(len(members), 8)) as executor:
        loaded = list(executor.map(load, members))
    result = build_constituent_proxy_series(loaded, max_days=days)
    if result:
        try:
            save_spot_snapshot(
                cache_key,
                {
                    "rows": result,
                    "data_end_date": str(result[-1].get("date") or "")[:10],
                },
            )
        except Exception:  # noqa: BLE001 - persistence must not hide live evidence
            pass
        return result
    return _cached_proxy_rows(cache_key, allow_same_trade_date_stale=True)


def build_constituent_proxy_series(
    loaded: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    *,
    max_days: int,
) -> list[dict[str, Any]]:
    usable: list[tuple[dict[str, Any], dict[str, float]]] = []
    for member, rows in loaded:
        by_date: dict[str, float] = {}
        for row in rows:
            day = str(row.get("date") or "")[:10]
            close = _number(row.get("close"))
            if day and close is not None and close > 0:
                by_date[day] = close
        if len(by_date) >= 61:
            usable.append((member, by_date))
    if len(usable) < _MIN_MEMBER_COUNT:
        return []

    common_dates = set(usable[0][1])
    for _member, by_date in usable[1:]:
        common_dates.intersection_update(by_date)
    dates = sorted(common_dates)[-max_days:]
    if len(dates) < 61:
        return []

    caps = [max(_number(member.get("market_cap")) or 0.0, 0.0) for member, _ in usable]
    cap_total = sum(caps)
    weights = (
        [cap / cap_total for cap in caps]
        if cap_total > 0
        else [1.0 / len(usable)] * len(usable)
    )
    bases = [by_date[dates[0]] for _member, by_date in usable]
    rows: list[dict[str, Any]] = []
    for day in dates:
        level = 100.0 * sum(
            weight * by_date[day] / base
            for weight, base, (_member, by_date) in zip(weights, bases, usable)
        )
        rows.append(
            {
                "date": day,
                "close": round(level, 6),
                "volume": None,
                "amount": None,
                "_source": "sina_current_large_constituents_proxy",
                "_proxy_member_count": len(usable),
            }
        )
    return rows


def _cached_constituents(
    board_code: str,
    member_count: int,
    _hour_bucket: int,
) -> tuple[dict[str, Any], ...]:
    key = (board_code, member_count, _hour_bucket)
    with _CONSTITUENT_CACHE_LOCK:
        cached = _CONSTITUENT_CACHE.get(key)
    if cached is not None:
        return cached

    params = {
        **_COMMON_PARAMS,
        "pn": "1",
        "pz": str(max(_MIN_MEMBER_COUNT, min(member_count, 20))),
        "fid": "f20",
        "fs": f"b:{board_code}",
        "fields": "f12,f13,f14,f20",
    }
    for host in _CLIST_HOSTS:
        try:
            with httpx.Client(
                headers=_EASTMONEY_HEADERS,
                timeout=4.0,
                trust_env=False,
                follow_redirects=True,
            ) as client:
                response = client.get(f"https://{host}/api/qt/clist/get", params=params)
                response.raise_for_status()
                rows = list(((response.json().get("data") or {}).get("diff") or []))
        except Exception:  # noqa: BLE001 - proxy evidence is best-effort
            continue
        parsed = tuple(
            {
                "code": str(row.get("f12") or "").strip(),
                "market": row.get("f13"),
                "name": str(row.get("f14") or "").strip(),
                "market_cap": _number(row.get("f20")),
            }
            for row in rows
            if str(row.get("f12") or "").strip().isdigit()
        )
        if parsed:
            with _CONSTITUENT_CACHE_LOCK:
                _CONSTITUENT_CACHE[key] = parsed
                while len(_CONSTITUENT_CACHE) > _CONSTITUENT_CACHE_MAX_ENTRIES:
                    _CONSTITUENT_CACHE.pop(next(iter(_CONSTITUENT_CACHE)))
            return parsed
    # Do not cache an empty provider response for the whole hour.  A transient
    # clist failure must be retryable by the next scan.
    return ()


def _proxy_cache_key(board_code: str, days: int, member_count: int) -> str:
    return f"sector:constituent-proxy:v1:{board_code}:{days}:{member_count}"


def _cached_proxy_rows(
    cache_key: str,
    *,
    allow_same_trade_date_stale: bool,
) -> list[dict[str, Any]]:
    try:
        payload = (
            get_spot_snapshot_any_age(cache_key)
            if allow_same_trade_date_stale
            else get_spot_snapshot(cache_key, ttl_seconds=_PROXY_CACHE_TTL_SECONDS)
        )
    except Exception:  # noqa: BLE001 - shared cache is an optimization only
        return []
    rows = list((payload or {}).get("rows") or [])
    if len(rows) < 61:
        return []
    if allow_same_trade_date_stale:
        data_end_date = str(
            (payload or {}).get("data_end_date") or rows[-1].get("date") or ""
        )[:10]
        if data_end_date != get_effective_trade_date():
            return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _sina_stock_symbol(code: str, market: object) -> str | None:
    if not code.isdigit() or len(code) != 6 or code.startswith(("4", "8")):
        return None
    try:
        market_id = int(market)
    except (TypeError, ValueError):
        market_id = 1 if code.startswith(("5", "6", "9")) else 0
    return ("sh" if market_id == 1 else "sz") + code


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


__all__ = [
    "build_constituent_proxy_series",
    "fetch_sector_constituent_proxy_series",
]
