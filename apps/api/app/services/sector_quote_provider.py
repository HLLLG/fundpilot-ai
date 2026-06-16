from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date

from app.config import get_settings
from app.services.akshare_spot_client import fetch_boards_via_akshare
from app.services.eastmoney_spot_client import fetch_eastmoney_boards
from app.services.sector_quote_browser_provider import fetch_boards_via_browser_command
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.sector_quote_relay_provider import fetch_boards_via_relay

logger = logging.getLogger(__name__)

SpotBoard = dict[str, float]
_MIN_CACHE_BOARD_ENTRIES = 8


@dataclass(frozen=True)
class SpotBoardFetchResult:
    boards: dict[str, SpotBoard]
    provider_path: str
    from_stale_cache: bool = False
    live_attempted: bool = False
    elapsed_seconds: float = 0.0


def fetch_spot_boards(
    *,
    force_refresh: bool = False,
    timeout_seconds: float | None = None,
) -> dict[str, SpotBoard]:
    """Keep the legacy dict return shape for older callers."""
    return fetch_spot_boards_result(
        force_refresh=force_refresh,
        timeout_seconds=timeout_seconds,
    ).boards


def fetch_spot_boards_result(
    *,
    force_refresh: bool = False,
    timeout_seconds: float | None = None,
) -> SpotBoardFetchResult:
    """Fetch board quotes together with provider metadata."""
    start_time = time.time()
    settings = get_settings()
    if not settings.sector_quotes_enabled:
        return SpotBoardFetchResult(
            boards=_empty_boards(),
            provider_path="disabled",
            elapsed_seconds=_elapsed(start_time),
        )

    today = date.today().isoformat()
    ttl = float(settings.sector_quotes_ttl_seconds)
    cache_key = f"spot:all:{today}"

    stale_day = get_spot_snapshot(cache_key, ttl_seconds=24 * 3600)
    if stale_day is not None and not _boards_cacheable(stale_day):
        stale_day = None

    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=ttl)
        if cached is not None and not _boards_cacheable(cached):
            cached = None
        if cached is not None:
            return SpotBoardFetchResult(
                boards=cached,
                provider_path="fresh_cache",
                elapsed_seconds=_elapsed(start_time),
            )

    live = _fetch_live_boards(timeout_seconds=timeout_seconds)
    if any(live.boards.values()) and _boards_cacheable(live.boards):
        save_spot_snapshot(cache_key, live.boards)
        return SpotBoardFetchResult(
            boards=live.boards,
            provider_path=live.provider_path,
            live_attempted=True,
            elapsed_seconds=_elapsed(start_time),
        )

    if stale_day is not None and any(stale_day.values()):
        logger.info("live boards empty; reusing stale spot cache for %s", cache_key)
        return SpotBoardFetchResult(
            boards=stale_day,
            provider_path="stale_cache",
            from_stale_cache=True,
            live_attempted=True,
            elapsed_seconds=_elapsed(start_time),
        )

    return SpotBoardFetchResult(
        boards=live.boards,
        provider_path=live.provider_path,
        live_attempted=True,
        elapsed_seconds=_elapsed(start_time),
    )


def _fetch_live_boards(*, timeout_seconds: float | None = None) -> SpotBoardFetchResult:
    """Fetch real board data with a pluggable provider chain."""
    boards = _empty_boards()
    start_time = time.time()

    try:
        if _budget_exhausted(start_time, timeout_seconds):
            logger.warning("_fetch_live_boards timeout before eastmoney attempt")
            return SpotBoardFetchResult(
                boards=boards,
                provider_path="empty",
                live_attempted=True,
                elapsed_seconds=_elapsed(start_time),
            )

        boards = fetch_eastmoney_boards(**_eastmoney_call_kwargs(timeout_seconds))
        if any(boards.values()):
            logger.info("eastmoney succeeded")
            boards = _fill_missing_boards_from_akshare(
                boards,
                timeout_seconds=timeout_seconds,
                start_time=start_time,
            )
            return SpotBoardFetchResult(
                boards=boards,
                provider_path="eastmoney_live",
                live_attempted=True,
                elapsed_seconds=_elapsed(start_time),
            )
    except Exception as exc:
        logger.warning("fetch_eastmoney_boards failed: %s", exc)

    relay_boards = fetch_boards_via_relay(timeout_seconds=_remaining_budget(start_time, timeout_seconds))
    if relay_boards and any(relay_boards.values()):
        logger.info("relay sector provider succeeded")
        return SpotBoardFetchResult(
            boards=relay_boards,
            provider_path="relay_live",
            live_attempted=True,
            elapsed_seconds=_elapsed(start_time),
        )

    if _has_browser_budget(start_time, timeout_seconds):
        browser_boards = fetch_boards_via_browser_command(
            timeout_seconds=_remaining_budget(start_time, timeout_seconds),
        )
        if browser_boards and any(browser_boards.values()):
            logger.info("browser sector command succeeded")
            return SpotBoardFetchResult(
                boards=browser_boards,
                provider_path="browser_live",
                live_attempted=True,
                elapsed_seconds=_elapsed(start_time),
            )

    if not _has_akshare_budget(timeout_seconds):
        logger.info("all fast providers failed and refresh budget exhausted; skip AkShare fallback")
        return SpotBoardFetchResult(
            boards=boards,
            provider_path="empty",
            live_attempted=True,
            elapsed_seconds=_elapsed(start_time),
        )

    logger.info("fast providers failed; attempting AkShare fallback")
    try:
        boards = fetch_boards_via_akshare(include_index=False)
        if any(boards.values()):
            logger.info(
                "akshare fallback succeeded: concept=%s industry=%s",
                len(boards.get("concept", {})),
                len(boards.get("industry", {})),
            )
            return SpotBoardFetchResult(
                boards=boards,
                provider_path="akshare_live",
                live_attempted=True,
                elapsed_seconds=_elapsed(start_time),
            )
    except Exception as exc:
        logger.warning("AkShare fallback also failed: %s", exc)

    logger.info("no live boards available, returning empty")
    return SpotBoardFetchResult(
        boards=boards,
        provider_path="empty",
        live_attempted=True,
        elapsed_seconds=_elapsed(start_time),
    )


def _fill_missing_boards_from_akshare(
    boards: dict[str, SpotBoard],
    timeout_seconds: float | None = None,
    start_time: float | None = None,
) -> dict[str, SpotBoard]:
    """Use AkShare to fill sparse concept or industry boards when budget allows."""
    if timeout_seconds is not None and start_time is not None:
        return boards

    sparse_threshold = 80
    needs_fill: list[str] = []
    for key in ("concept", "industry"):
        board = boards.get(key) or {}
        if not board or len(board) < sparse_threshold:
            needs_fill.append(key)
    if not needs_fill:
        return boards

    fallback = fetch_boards_via_akshare(include_index=False)
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


def _boards_cacheable(boards: dict[str, SpotBoard]) -> bool:
    return _board_entry_count(boards) >= _MIN_CACHE_BOARD_ENTRIES


def _board_entry_count(boards: dict[str, SpotBoard]) -> int:
    return sum(len(board or {}) for board in boards.values())


def _eastmoney_call_kwargs(timeout_seconds: float | None) -> dict[str, float | int]:
    if timeout_seconds is None:
        return {}
    return {
        "timeout": round(max(0.2, min(0.5, timeout_seconds * 0.1)), 3),
        "max_retries": 1,
        "max_hosts": 1,
    }


def _budget_exhausted(
    start_time: float,
    timeout_seconds: float | None,
    *,
    ratio: float = 1.0,
) -> bool:
    if timeout_seconds is None:
        return False
    return (time.time() - start_time) >= timeout_seconds * ratio


def _remaining_budget(start_time: float, timeout_seconds: float | None) -> float | None:
    if timeout_seconds is None:
        return None
    return max(0.5, round(timeout_seconds - (time.time() - start_time), 3))


def _elapsed(start_time: float) -> float:
    return round(time.time() - start_time, 4)


def _has_akshare_budget(timeout_seconds: float | None) -> bool:
    return timeout_seconds is None


def _has_browser_budget(start_time: float, timeout_seconds: float | None) -> bool:
    if timeout_seconds is None:
        return True
    return _remaining_budget(start_time, timeout_seconds) is not None and _remaining_budget(start_time, timeout_seconds) >= 1.2
