from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.services.dip_drop_scanner import build_dip_radar_pool_with_stats
from app.services.fund_dip_rebound_backtest import build_sector_dip_rebound_hint
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
    snapshot_refreshed_before_process_boot,
)
from app.services.sector_registry import get_sector_entry
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

_CACHE_VERSION = "v2"
_LIVE_TTL_SECONDS = 60.0
_CLOSED_TTL_SECONDS = 3600.0
_EMPTY_CACHE_TTL_SECONDS = 300.0
_DEFAULT_FEE_BREAK_EVEN = 2.5
_RADAR_MIN_DROP_PERCENT = 2.0
_RADAR_BUDGET_SECONDS = 25.0
_INTRADAY_SESSIONS = {
    "trading_day_intraday",
    "trading_day_pre_close",
    "trading_day_pre_open",
}


def list_radar_sector_labels() -> list[str]:
    """Discovery chips used for sector inference in fast radar scan."""
    from app.services.sector_registry import list_discovery_sector_labels

    return list_discovery_sector_labels()


def _cache_ttl_seconds(session_kind: str, *, available: bool) -> float:
    if not available:
        return _EMPTY_CACHE_TTL_SECONDS
    if session_kind in _INTRADAY_SESSIONS:
        return _LIVE_TTL_SECONDS
    return _CLOSED_TTL_SECONDS


def _cache_key(trade_date: str, lookback_days: int) -> str:
    return f"dip:radar:{_CACHE_VERSION}:{trade_date}:{lookback_days}"


def _normalize_pool_entry(entry: dict) -> dict[str, Any]:
    sector = str(entry.get("sector_label") or entry.get("sector_name") or "").strip()
    nav_trend = entry.get("nav_trend") if isinstance(entry.get("nav_trend"), dict) else {}
    daily = nav_trend.get("recent_5d_daily_change_percent") or []
    change_1d = None
    if isinstance(daily, list) and daily:
        try:
            change_1d = round(float(daily[-1]), 2)
        except (TypeError, ValueError):
            change_1d = None

    return {
        "fund_code": str(entry.get("fund_code", "")).zfill(6),
        "fund_name": str(entry.get("fund_name", "")),
        "sector_label": sector,
        "dip_drop_percent": entry.get("dip_drop_percent"),
        "change_1d_percent": change_1d,
        "rebound_score": entry.get("rebound_score"),
        "rebound_signals": entry.get("rebound_signals") or [],
        "historical_hint": None,
    }


def _sector_dip_leaders(items: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    by_sector: dict[str, list[float]] = {}
    for item in items:
        sector = str(item.get("sector_label") or "").strip()
        dip = item.get("dip_drop_percent")
        if not sector or dip is None:
            continue
        by_sector.setdefault(sector, []).append(float(dip))

    leaders: list[dict[str, Any]] = []
    for sector, dips in by_sector.items():
        leaders.append(
            {
                "sector_label": sector,
                "avg_dip_drop_percent": round(sum(dips) / len(dips), 2),
                "fund_count": len(dips),
                "min_dip_drop_percent": round(min(dips), 2),
            }
        )
    leaders.sort(key=lambda row: float(row["min_dip_drop_percent"]))
    return leaders[:limit]


def _attach_historical_hints(
    items: list[dict[str, Any]],
    *,
    dip_threshold_percent: float,
    rebound_threshold_percent: float,
) -> None:
    """Fill per-item historical_hint from sector index backtest (one fetch per sector)."""
    hints: dict[str, dict[str, Any] | None] = {}
    for item in items:
        sector = str(item.get("sector_label") or "").strip()
        if not sector:
            item["historical_hint"] = None
            continue
        if sector not in hints:
            hints[sector] = build_sector_dip_rebound_hint(
                sector,
                dip_threshold_percent=dip_threshold_percent,
                rebound_threshold_percent=rebound_threshold_percent,
                forward_days=3,
            )
        item["historical_hint"] = hints[sector]


def _apply_sector_filter(items: list[dict[str, Any]], sector: str | None) -> list[dict[str, Any]]:
    if not sector:
        return items
    label = sector.strip()
    if not label:
        return items
    return [item for item in items if str(item.get("sector_label") or "").strip() == label]


def build_dip_radar_snapshot(
    *,
    lookback_days: int = 5,
    min_drop_percent: float = _RADAR_MIN_DROP_PERCENT,
    pool_cap: int = 30,
) -> dict[str, Any]:
    """Build cross-sector dip radar via fast global rank prescreen."""
    session = build_trading_session()
    trade_date = session.get("effective_trade_date", "")
    session_kind = session.get("session_kind", "")

    pool, scan_stats = build_dip_radar_pool_with_stats(
        lookback_days=lookback_days,
        min_drop_percent=min_drop_percent,
        pool_cap=pool_cap,
        budget_seconds=_RADAR_BUDGET_SECONDS,
    )

    items = [_normalize_pool_entry(entry) for entry in pool]
    # 历史命中率按需懒加载；同步构建时跳过，避免阻塞首屏
    items.sort(key=lambda row: float(row.get("dip_drop_percent") or 0.0))
    for index, item in enumerate(items, start=1):
        item["rank"] = index

    available = bool(items)
    empty_message = "暂无符合跌幅阈值的基金"
    if not available:
        shortlist = scan_stats.get("rank_shortlist") or 0
        threshold = scan_stats.get("dip_threshold_percent")
        empty_message = (
            f"近1周跌幅榜 {shortlist} 只中，无近 {lookback_days} 日净值跌幅 ≥{threshold}% 的场外基金"
            if shortlist
            else empty_message
        )
    return {
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "trade_date": trade_date,
        "lookback_days": lookback_days,
        "fee_break_even_percent": _DEFAULT_FEE_BREAK_EVEN,
        "items": items,
        "sector_dip_leaders": _sector_dip_leaders(items),
        "scan_stats": scan_stats,
        "available": available,
        "from_cache": False,
        "stale": False,
        "session_kind": session_kind,
        "message": None if available else empty_message,
    }


def refresh_dip_radar_snapshots(*, lookback_days: tuple[int, ...] = (3, 5)) -> None:
    """后台任务：刷新全用户共享的大跌雷达快照（默认 3/5 日）。"""
    session = build_trading_session()
    trade_date = session.get("effective_trade_date", "")
    for days in lookback_days:
        if days not in (3, 5):
            continue
        try:
            snapshot = build_dip_radar_snapshot(lookback_days=days)
            if snapshot.get("items"):
                save_spot_snapshot(_cache_key(trade_date, days), snapshot)
        except Exception as exc:
            logger.info("dip radar refresh failed lookback=%s: %s", days, exc)


def get_dip_radar_snapshot(
    *,
    lookback_days: int = 5,
    sector: str | None = None,
    limit: int = 20,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Read-through cache for dip radar; sector/limit applied after cache hit."""
    if lookback_days not in (3, 5):
        lookback_days = 5
    limit = max(1, min(int(limit), 50))

    if sector and sector.strip() and get_sector_entry(sector.strip()) is None:
        session = build_trading_session()
        return {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "trade_date": session.get("effective_trade_date", ""),
            "lookback_days": lookback_days,
            "fee_break_even_percent": _DEFAULT_FEE_BREAK_EVEN,
            "items": [],
            "sector_dip_leaders": [],
            "available": False,
            "from_cache": False,
            "stale": False,
            "session_kind": session.get("session_kind", ""),
            "message": f"未知板块：{sector.strip()}",
        }

    session = build_trading_session()
    trade_date = session.get("effective_trade_date", "")
    session_kind = session.get("session_kind", "")
    cache_key = _cache_key(trade_date, lookback_days)
    ttl = _cache_ttl_seconds(session_kind, available=True)

    cached: dict[str, Any] | None = None
    stale = False
    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=ttl)
        if cached is None:
            cached = get_spot_snapshot_any_age(cache_key)
            stale = cached is not None
        if cached is not None and snapshot_refreshed_before_process_boot(
            cached.get("refreshed_at")
        ):
            cached = None
            stale = False

    if cached is None or force_refresh:
        cached = build_dip_radar_snapshot(lookback_days=lookback_days)
        if cached.get("items"):
            save_spot_snapshot(cache_key, cached)
        stale = False

    all_items = list(cached.get("items") or [])
    filtered_items = _apply_sector_filter(all_items, sector)
    items = filtered_items[:limit]
    for index, item in enumerate(items, start=1):
        item["rank"] = index

    sector_label = sector.strip() if sector else None
    if sector_label:
        leaders = _sector_dip_leaders(all_items)
    else:
        leaders = list(cached.get("sector_dip_leaders") or [])

    scan_stats = dict(cached.get("scan_stats") or {})
    total_matches = len(all_items)
    scan_stats["total_matches"] = total_matches
    if sector_label:
        scan_stats["sector_filter"] = sector_label
        scan_stats["matches"] = len(filtered_items)
    else:
        scan_stats["matches"] = total_matches

    if items:
        message = None
    elif sector_label and total_matches > 0:
        message = (
            f"「{sector_label}」板块暂无近 {lookback_days} 日跌幅达标的基金"
            f"（全市场已扫描 {total_matches} 只，可点「全部」查看）"
        )
    elif sector_label:
        message = f"「{sector_label}」板块暂无符合条件的基金"
    else:
        message = cached.get("message") or "暂无符合跌幅阈值的基金"

    return {
        **cached,
        "items": items,
        "sector_dip_leaders": leaders,
        "scan_stats": scan_stats,
        "available": bool(items),
        "from_cache": True,
        "stale": stale,
        "message": message,
        "sector_filter": sector_label,
    }
