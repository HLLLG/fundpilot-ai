from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from app.services.akshare_spot_client import fetch_akshare_board_records
from app.services.eastmoney_spot_client import fetch_eastmoney_board_records
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)
BoardType = Literal["industry", "concept"]
SortMode = Literal["change", "inflow"]

_LIVE_TTL_SECONDS = 60.0
_CLOSED_TTL_SECONDS = 3600.0
_CACHE_VERSION = "v2"


def get_sector_board_snapshot(*, force_refresh: bool = False) -> dict[str, Any]:
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    session_kind = session.get("session_kind", "")
    cache_ttl = (
        _LIVE_TTL_SECONDS
        if session_kind in {"trading_day_intraday", "trading_day_pre_close"}
        else _CLOSED_TTL_SECONDS
    )
    cache_key = f"market:sector_boards:{_CACHE_VERSION}:{trade_date}"

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl)
        if cached and _snapshot_has_rows(cached):
            return {**cached, "from_cache": True, "stale": False}

    stale_cached = get_spot_snapshot_any_age(cache_key)

    industry, concept = _fetch_all_board_records_parallel()
    available = bool(industry or concept)

    if available:
        snapshot = {
            "trade_date": trade_date,
            "session_kind": session_kind,
            "available": True,
            "from_cache": False,
            "stale": False,
            "message": None,
            "industry": _dedupe_board_rows(industry),
            "concept": _dedupe_board_rows(concept),
        }
        save_spot_snapshot(cache_key, snapshot)
        return snapshot

    if stale_cached and _snapshot_has_rows(stale_cached):
        return {
            **stale_cached,
            "from_cache": True,
            "stale": True,
            "available": True,
            "message": "行情更新失败，展示上次缓存数据",
        }

    return {
        "trade_date": trade_date,
        "session_kind": session_kind,
        "available": False,
        "from_cache": False,
        "stale": False,
        "message": "板块行情暂不可用，请稍后重试",
        "industry": [],
        "concept": [],
    }


def _snapshot_has_rows(snapshot: dict[str, Any]) -> bool:
    return bool(snapshot.get("industry") or snapshot.get("concept"))


def build_widget_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    combined = _combined_board_rows(
        list(snapshot.get("industry") or []),
        list(snapshot.get("concept") or []),
    )
    return {
        "trade_date": snapshot.get("trade_date"),
        "session_kind": snapshot.get("session_kind"),
        "available": snapshot.get("available", False),
        "from_cache": snapshot.get("from_cache", False),
        "message": snapshot.get("message"),
        "top_gainers": _top_n(combined, key="change_percent", n=3, reverse=True),
        "top_losers": _top_n(combined, key="change_percent", n=3, reverse=False),
        "top_inflow": _top_n(combined, key="main_force_net_yi", n=3, reverse=True),
        "top_outflow": _top_n(combined, key="main_force_net_yi", n=3, reverse=False),
        "stale": snapshot.get("stale", False),
    }


def build_list_payload(
    snapshot: dict[str, Any],
    *,
    board_type: BoardType,
    sort: SortMode,
) -> dict[str, Any]:
    rows = _dedupe_board_rows(list(snapshot.get(board_type) or []))
    sort_key = "change_percent" if sort == "change" else "main_force_net_yi"
    sorted_rows = _sort_rows(rows, key=sort_key, reverse=True)
    items = [
        {**row, "rank": index + 1}
        for index, row in enumerate(sorted_rows)
        if row.get(sort_key) is not None
    ]
    return {
        "trade_date": snapshot.get("trade_date"),
        "session_kind": snapshot.get("session_kind"),
        "available": snapshot.get("available", False),
        "from_cache": snapshot.get("from_cache", False),
        "message": snapshot.get("message"),
        "board_type": board_type,
        "sort": sort,
        "items": items,
        "stale": snapshot.get("stale", False),
    }


def _fetch_all_board_records_parallel() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results: dict[str, list[dict[str, Any]]] = {"industry": [], "concept": []}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_fetch_board_records, board_type): board_type
            for board_type in ("industry", "concept")
        }
        for future in as_completed(futures):
            board_type = futures[future]
            try:
                results[board_type] = future.result()
            except Exception as exc:
                logger.debug("board fetch failed (%s): %s", board_type, exc)
                results[board_type] = []
    return results["industry"], results["concept"]


def _fetch_board_records(board_type: BoardType) -> list[dict[str, Any]]:
    try:
        rows = fetch_eastmoney_board_records(board_type)
        if rows:
            return rows
    except Exception as exc:
        logger.debug("eastmoney board records failed (%s): %s", board_type, exc)

    rows = fetch_akshare_board_records(board_type)
    if rows:
        logger.info("market sector boards using akshare fallback for %s (%s rows)", board_type, len(rows))
        return rows

    rows = _fetch_board_records_from_relay(board_type)
    if rows:
        logger.info("market sector boards using relay fallback for %s (%s rows)", board_type, len(rows))
    return rows


def _fetch_board_records_from_relay(board_type: BoardType) -> list[dict[str, Any]]:
    try:
        from app.services.sector_quote_relay_provider import fetch_boards_via_relay

        boards = fetch_boards_via_relay(timeout_seconds=8.0)
        spot = boards.get(board_type) or {}
    except Exception as exc:
        logger.debug("relay board fallback failed (%s): %s", board_type, exc)
        return []

    return [
        {
            "name": name,
            "code": None,
            "change_percent": change,
            "main_force_net_yi": None,
        }
        for name, change in spot.items()
    ]


def _safe_fetch_board_records(board_type: BoardType) -> list[dict[str, Any]]:
    try:
        return _fetch_board_records(board_type)
    except Exception:
        return []


def _dedupe_board_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.services.eastmoney_spot_client import _dedupe_board_records

    return _dedupe_board_records(rows)


def _combined_board_rows(
    industry: list[dict[str, Any]],
    concept: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """板块表现卡片：经典行业 + 概念合并（同名优先保留行业口径）。"""
    by_name: dict[str, dict[str, Any]] = {}
    for row in concept:
        name = str(row.get("name", "")).strip()
        if name:
            by_name[name] = row
    for row in industry:
        name = str(row.get("name", "")).strip()
        if name:
            by_name[name] = row
    return list(by_name.values())


def _top_n(
    rows: list[dict[str, Any]],
    *,
    key: str,
    n: int,
    reverse: bool,
) -> list[dict[str, Any]]:
    eligible = [row for row in rows if row.get(key) is not None]
    return _sort_rows(eligible, key=key, reverse=reverse)[:n]


def _sort_rows(rows: list[dict[str, Any]], *, key: str, reverse: bool) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda item: float(item.get(key) or 0),
        reverse=reverse,
    )
