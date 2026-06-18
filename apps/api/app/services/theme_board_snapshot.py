from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Literal

from app.database import list_fund_primary_sectors
from app.models import Holding
from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series
from app.services.eastmoney_trends_client import fetch_eastmoney_kline_close_percent
from app.services.akshare_spot_client import fetch_akshare_board_records, fetch_boards_via_akshare
from app.services.sector_board_snapshot import get_sector_board_snapshot
from app.services.fund_primary_sector_service import GLOBAL_FUND_SECTOR_SEEDS
from app.services.sector_labels import build_sector_candidates, normalize_sector_label
from app.services.sector_canonical import (
    get_canonical_sector,
    get_quote_canonical_sector,
    is_plausible_daily_change,
    list_discovery_sector_labels,
)
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

SortMode = Literal["change", "streak"]

_LIVE_TTL_SECONDS = 60.0
_CLOSED_TTL_SECONDS = 3600.0
_CACHE_VERSION = "v2"
_BUDGET_SECONDS = 12.0
_STREAK_BUDGET_SECONDS = 20.0
_STREAK_FETCH_TIMEOUT = 8.0
_STREAK_UNAVAILABLE_HINT = (
    "日涨幅已更新；连涨天数需近N日历史K线，当前暂未获取"
    "（历史日K暂未获取，可配置 sector-relay 或 AkShare 板块日线）"
)
def compute_consecutive_up_days(
    series: list[dict],
    trade_date: str | None,
) -> int | None:
    """从有效交易日 bar 向前统计 change_percent > 0 的连续天数。"""
    if not series:
        return None

    bars = _bars_through_trade_date(series, trade_date)
    if not bars:
        return None

    latest_change = _as_float(bars[-1].get("change_percent"))
    if latest_change is None:
        return None
    if latest_change <= 0:
        return 0

    streak = 0
    for bar in reversed(bars):
        change = _as_float(bar.get("change_percent"))
        if change is None:
            break
        if change > 0:
            streak += 1
        else:
            break
    return streak


def build_linked_fund_counts() -> dict[str, int]:
    labels = list_discovery_sector_labels()
    by_sector: dict[str, set[str]] = {label: set() for label in labels}

    for code, seed in GLOBAL_FUND_SECTOR_SEEDS.items():
        sector_name = seed.get("sector_name")
        if sector_name in by_sector:
            by_sector[str(sector_name)].add(str(code).zfill(6))

    for row in list_fund_primary_sectors():
        sector_name = row.get("sector_name")
        fund_code = str(row.get("fund_code", "")).strip().zfill(6)
        if sector_name in by_sector and fund_code:
            by_sector[str(sector_name)].add(fund_code)

    return {label: len(codes) for label, codes in by_sector.items()}


def resolve_holding_to_discovery_label(sector_name: str | None) -> str | None:
    canon = get_quote_canonical_sector(sector_name) or get_canonical_sector(sector_name)
    if canon is None:
        return None
    labels = list_discovery_sector_labels()
    for label in labels:
        label_canon = get_quote_canonical_sector(label) or get_canonical_sector(label)
        if label_canon and label_canon.eastmoney_secid == canon.eastmoney_secid:
            return label
    return canon.label if canon.label in labels else None


def count_held_funds_by_sector(holdings: list[Holding]) -> dict[str, int]:
    labels = list_discovery_sector_labels()
    counts = {label: 0 for label in labels}
    for holding in holdings:
        resolved = resolve_holding_to_discovery_label(holding.sector_name)
        if resolved in counts:
            counts[resolved] += 1
    return counts


def apply_holdings_overlay(items: list[dict[str, Any]], holdings: list[Holding]) -> list[dict[str, Any]]:
    held_counts = count_held_funds_by_sector(holdings)
    return [
        {
            **item,
            "held_fund_count": held_counts.get(str(item.get("sector_label")), 0),
            "in_portfolio": held_counts.get(str(item.get("sector_label")), 0) > 0,
        }
        for item in items
    ]


def build_theme_board_payload(
    items: list[dict[str, Any]],
    *,
    sort: SortMode,
    snapshot_meta: dict[str, Any],
    holdings: list[Holding] | None = None,
) -> dict[str, Any]:
    overlaid = apply_holdings_overlay(items, holdings or [])
    sorted_items = _sort_theme_items(overlaid, sort=sort)
    ranked = [{**row, "rank": index + 1} for index, row in enumerate(sorted_items)]
    return {
        "trade_date": snapshot_meta.get("trade_date"),
        "session_kind": snapshot_meta.get("session_kind"),
        "available": snapshot_meta.get("available", False),
        "from_cache": snapshot_meta.get("from_cache", False),
        "stale": snapshot_meta.get("stale", False),
        "message": snapshot_meta.get("message"),
        "sort": sort,
        "items": ranked,
    }


def get_theme_board_snapshot(
    *,
    force_refresh: bool = False,
    holdings: list[Holding] | None = None,
    sort: SortMode = "change",
    fetch_series=None,
) -> dict[str, Any]:
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    cache_ttl = (
        _LIVE_TTL_SECONDS
        if session_kind in {"trading_day_intraday", "trading_day_pre_close"}
        else _CLOSED_TTL_SECONDS
    )
    cache_key = f"theme:boards:{_CACHE_VERSION}:{trade_date}"

    cached_items: list[dict[str, Any]] | None = None
    snapshot_meta: dict[str, Any]

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl)
        if cached and cached.get("items"):
            cached_items = list(cached["items"])
            snapshot_meta = {
                "trade_date": cached.get("trade_date", trade_date),
                "session_kind": cached.get("session_kind", session_kind),
                "available": True,
                "from_cache": True,
                "stale": False,
                "message": cached.get("message"),
            }
            return build_theme_board_payload(
                cached_items,
                sort=sort,
                snapshot_meta=snapshot_meta,
                holdings=holdings,
            )

    stale_cached = get_spot_snapshot_any_age(cache_key)
    series_fetcher = fetch_series or _default_fetch_theme_series
    items = _build_theme_board_items(
        trade_date=trade_date,
        fetch_series=series_fetcher,
    )
    has_live_quotes = any(item.get("change_1d_percent") is not None for item in items)

    if items and has_live_quotes:
        streak_hint = _theme_streak_unavailable_hint(items)
        snapshot_meta = {
            "trade_date": trade_date,
            "session_kind": session_kind,
            "available": True,
            "from_cache": False,
            "stale": False,
            "message": streak_hint,
        }
        save_spot_snapshot(
            cache_key,
            {
                "items": items,
                "trade_date": trade_date,
                "session_kind": session_kind,
            },
        )
        return build_theme_board_payload(
            items,
            sort=sort,
            snapshot_meta=snapshot_meta,
            holdings=holdings,
        )

    if items:
        snapshot_meta = {
            "trade_date": trade_date,
            "session_kind": session_kind,
            "available": True,
            "from_cache": False,
            "stale": True,
            "message": "行情暂不可用，仅展示板块列表",
        }
        return build_theme_board_payload(
            items,
            sort=sort,
            snapshot_meta=snapshot_meta,
            holdings=holdings,
        )

    if stale_cached and stale_cached.get("items"):
        snapshot_meta = {
            "trade_date": stale_cached.get("trade_date", trade_date),
            "session_kind": stale_cached.get("session_kind", session_kind),
            "available": True,
            "from_cache": True,
            "stale": True,
            "message": "行情更新失败，展示上次缓存数据",
        }
        return build_theme_board_payload(
            _merge_theme_board_rows(list(stale_cached["items"])),
            sort=sort,
            snapshot_meta=snapshot_meta,
            holdings=holdings,
        )

    snapshot_meta = {
        "trade_date": trade_date,
        "session_kind": session_kind,
        "available": True,
        "from_cache": False,
        "stale": False,
        "message": "行情暂不可用，仅展示板块列表",
    }
    return build_theme_board_payload(
        _merge_theme_board_rows([]),
        sort=sort,
        snapshot_meta=snapshot_meta,
        holdings=holdings,
    )


def _merge_theme_board_rows(partial: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_label = {row["sector_label"]: row for row in partial}
    linked_counts = build_linked_fund_counts()
    merged: list[dict[str, Any]] = []
    for label in list_discovery_sector_labels():
        row = by_label.get(label) or {
                "sector_label": label,
                "change_1d_percent": None,
                "consecutive_up_days": None,
                "linked_fund_count": linked_counts.get(label, 0),
            }
        merged.append(_strip_internal_theme_fields(row))
    return merged


def _load_theme_spot_changes() -> dict[str, float]:
    """批量现货涨跌幅：优先复用全市场板块缓存，失败再走 AkShare。"""
    changes: dict[str, float] = {}
    try:
        snapshot = get_sector_board_snapshot(force_refresh=False)
        for board_type in ("industry", "concept"):
            for row in snapshot.get(board_type) or []:
                name = str(row.get("name", "")).strip()
                change = row.get("change_percent")
                if name and change is not None:
                    changes[name] = float(change)
    except Exception as exc:
        logger.debug("theme spot from sector snapshot failed: %s", exc)

    if changes:
        return changes

    for board_type in ("industry", "concept"):
        try:
            for row in fetch_akshare_board_records(board_type):
                name = str(row.get("name", "")).strip()
                change = row.get("change_percent")
                if name and change is not None:
                    changes[name] = float(change)
        except Exception as exc:
            logger.debug("theme spot akshare %s failed: %s", board_type, exc)

    try:
        index_board = fetch_boards_via_akshare(include_index=True).get("index") or {}
        for name, change in index_board.items():
            cleaned = str(name).strip()
            if cleaned and change is not None:
                changes[cleaned] = float(change)
    except Exception as exc:
        logger.debug("theme spot index board failed: %s", exc)

    return changes


def _spot_name_matches(candidate_label: str, spot_name: str) -> bool:
    if candidate_label == spot_name:
        return True
    if len(candidate_label) < 2:
        return False
    if "商业航天" in candidate_label:
        return "商业航天" in spot_name
    if "国防军工" in candidate_label:
        return "国防军工" in spot_name or spot_name == "军工"
    if candidate_label in spot_name:
        return len(spot_name) - len(candidate_label) <= 6
    if spot_name in candidate_label:
        return True
    return False


def _lookup_spot_change(
    *,
    label: str,
    canon,
    spot_changes: dict[str, float],
) -> float | None:
    if not spot_changes:
        return None
    for key in (label, canon.label, canon.source_name):
        cleaned = normalize_sector_label(key)
        if cleaned and cleaned in spot_changes:
            return round(float(spot_changes[cleaned]), 2)

    candidates: list[str] = []
    for key in (label, canon.label, canon.source_name):
        candidates.extend(build_sector_candidates(key))

    for spot_name, change in spot_changes.items():
        for candidate in candidates:
            if _spot_name_matches(candidate, spot_name):
                return round(float(change), 2)
    return None


def _build_theme_board_items(
    *,
    trade_date: str | None,
    fetch_series,
) -> list[dict[str, Any]]:
    labels = list_discovery_sector_labels()
    linked_counts = build_linked_fund_counts()

    rows: list[dict[str, Any]] = []
    for label in labels:
        row = _theme_board_row_base(label, linked_counts.get(label, 0))
        if row is not None:
            rows.append(row)

    deadline = time.monotonic() + _BUDGET_SECONDS
    executor = ThreadPoolExecutor(max_workers=min(6, max(len(rows), 1)))
    change_futures = [
        executor.submit(_enrich_theme_board_daily_change, row, trade_date)
        for row in rows
    ]
    pending = set(change_futures)
    try:
        while pending and time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            done, pending = wait(
                pending,
                timeout=min(0.25, max(0.05, remaining)),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                try:
                    future.result()
                except Exception as exc:
                    logger.debug("theme board daily change enrich failed: %s", exc)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    streak_deadline = time.monotonic() + _STREAK_BUDGET_SECONDS
    streak_executor = ThreadPoolExecutor(max_workers=min(6, max(len(rows), 1)))
    streak_futures = [
        streak_executor.submit(_enrich_theme_board_streak, row, trade_date, fetch_series)
        for row in rows
    ]
    pending = set(streak_futures)
    try:
        while pending and time.monotonic() < streak_deadline:
            remaining = streak_deadline - time.monotonic()
            done, pending = wait(
                pending,
                timeout=min(0.25, max(0.05, remaining)),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                try:
                    future.result()
                except Exception as exc:
                    logger.debug("theme board streak enrich failed: %s", exc)
    finally:
        streak_executor.shutdown(wait=False, cancel_futures=True)

    for row in rows:
        row.pop("_canon", None)

    return [_strip_internal_theme_fields(row) for row in _merge_theme_board_rows(rows)]


def _theme_board_row_base(
    label: str,
    linked_fund_count: int,
) -> dict[str, Any] | None:
    canon = get_quote_canonical_sector(label) or get_canonical_sector(label)
    if canon is None:
        return None

    return {
        "sector_label": label,
        "change_1d_percent": None,
        "consecutive_up_days": None,
        "linked_fund_count": linked_fund_count,
        "_canon": canon,
    }


def _strip_internal_theme_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def _enrich_theme_board_daily_change(
    row: dict[str, Any],
    trade_date: str | None,
) -> None:
    canon = row.get("_canon")
    if canon is None:
        return

    change = fetch_eastmoney_kline_close_percent(
        canon.eastmoney_secid,
        source_code=canon.source_code,
        trade_date=trade_date,
        timeout=4.0,
        max_retries=1,
    )
    if change is not None and is_plausible_daily_change(change):
        row["change_1d_percent"] = round(float(change), 2)
        return

    row["change_1d_percent"] = _lookup_spot_change_fallback(
        label=str(row.get("sector_label", "")),
        canon=canon,
    )


def _enrich_theme_board_streak(
    row: dict[str, Any],
    trade_date: str | None,
    fetch_series,
) -> None:
    canon = row.get("_canon")
    if canon is None:
        return

    series = fetch_series(canon)
    if row.get("change_1d_percent") is None and series:
        row["change_1d_percent"] = _latest_change_percent(series, trade_date)
    if series:
        row["consecutive_up_days"] = compute_consecutive_up_days(series, trade_date)


def _lookup_spot_change_fallback(*, label: str, canon) -> float | None:
    """日 K 全失败时再用现货榜模糊匹配（与持仓 canonical K 线口径不一致，仅作兜底）。"""
    try:
        spot_changes = _load_theme_spot_changes()
    except Exception as exc:
        logger.debug("theme spot fallback failed: %s", exc)
        return None
    return _lookup_spot_change(label=label, canon=canon, spot_changes=spot_changes)


def _default_fetch_theme_series(canon) -> list[dict]:
    return fetch_canonical_daily_kline_series(
        canon,
        max_days=20,
        timeout=_STREAK_FETCH_TIMEOUT,
    )


def _theme_streak_unavailable_hint(items: list[dict[str, Any]]) -> str | None:
    if any(item.get("consecutive_up_days") is not None for item in items):
        return None
    if not any(item.get("change_1d_percent") is not None for item in items):
        return None
    return _STREAK_UNAVAILABLE_HINT


def _sort_theme_items(items: list[dict[str, Any]], *, sort: SortMode) -> list[dict[str, Any]]:
    key_name = "change_1d_percent" if sort == "change" else "consecutive_up_days"

    def sort_key(item: dict[str, Any]) -> tuple[int, float]:
        value = item.get(key_name)
        if value is None:
            return (1, 0.0)
        return (0, float(value))

    return sorted(items, key=sort_key, reverse=True)


def _bars_through_trade_date(series: list[dict], trade_date: str | None) -> list[dict]:
    if not series:
        return []
    if trade_date:
        for index, bar in enumerate(series):
            if str(bar.get("date", ""))[:10] == str(trade_date)[:10]:
                return series[: index + 1]
    return list(series)


def _latest_change_percent(series: list[dict], trade_date: str | None) -> float | None:
    if not series:
        return None
    if trade_date:
        for bar in reversed(series):
            if str(bar.get("date", ""))[:10] == str(trade_date)[:10]:
                return _as_float(bar.get("change_percent"))
    return _as_float(series[-1].get("change_percent"))


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
