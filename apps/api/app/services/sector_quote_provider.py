from __future__ import annotations

import logging
from datetime import date

from app.config import get_settings
from app.services.akshare_spot_client import fetch_boards_via_akshare
from app.services.eastmoney_spot_client import fetch_eastmoney_boards
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot

logger = logging.getLogger(__name__)

SpotBoard = dict[str, float]


def fetch_spot_boards(*, force_refresh: bool = False) -> dict[str, SpotBoard]:
    settings = get_settings()
    if not settings.sector_quotes_enabled:
        return {"index": {}, "concept": {}, "industry": {}}

    today = date.today().isoformat()
    ttl = float(settings.sector_quotes_ttl_seconds)
    cache_key = f"spot:all:{today}"

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=ttl)
        if cached is not None:
            return cached

    boards = _fetch_live_boards()
    if any(boards.values()):
        save_spot_snapshot(cache_key, boards)
        return boards

    if force_refresh:
        stale = get_spot_snapshot(cache_key, ttl_seconds=24 * 3600)
        if stale is not None and any(stale.values()):
            logger.warning("live boards empty on force refresh; reusing stale cache")
            return stale
        return boards

    stale = get_spot_snapshot(cache_key, ttl_seconds=24 * 3600)
    if stale is not None:
        logger.warning("live eastmoney boards empty; using stale cache for %s", cache_key)
        return stale

    return boards


def _fetch_live_boards() -> dict[str, SpotBoard]:
    boards = _empty_boards()
    try:
        boards = fetch_eastmoney_boards()
    except Exception as exc:
        logger.warning("fetch_eastmoney_boards failed: %s", exc)

    if any(boards.values()):
        boards = _fill_missing_boards_from_akshare(boards)
        return boards

    logger.info("httpx eastmoney empty; trying akshare fallback")
    fallback = fetch_boards_via_akshare()
    if any(fallback.values()):
        return fallback

    return boards


def _fill_missing_boards_from_akshare(boards: dict[str, SpotBoard]) -> dict[str, SpotBoard]:
    """东财部分接口失败或数据过少时，用 AkShare 补齐概念/行业/指数。"""
    sparse_threshold = 80
    needs_fill: list[str] = []
    for key in ("concept", "industry", "index"):
        board = boards.get(key) or {}
        if not board or (key in {"concept", "industry"} and len(board) < sparse_threshold):
            needs_fill.append(key)
    if not needs_fill:
        return boards

    fallback = fetch_boards_via_akshare()
    merged = dict(boards)
    filled: list[str] = []
    for key in needs_fill:
        if fallback.get(key):
            if merged.get(key):
                merged[key] = {**fallback[key], **merged[key]}
            else:
                merged[key] = fallback[key]
            filled.append(f"{key}={len(merged[key])}")
    if filled:
        logger.info("akshare filled sparse boards: %s", ", ".join(filled))
    return merged


def _empty_boards() -> dict[str, SpotBoard]:
    return {"index": {}, "concept": {}, "industry": {}}
