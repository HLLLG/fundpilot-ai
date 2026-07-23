from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, wait
from dataclasses import dataclass
from datetime import date
from typing import Callable

from app.config import get_settings
from app.services.akshare_spot_client import fetch_boards_via_akshare
from app.services.eastmoney_spot_client import fetch_eastmoney_boards
from app.services.sector_quote_browser_provider import fetch_boards_via_browser_command
from app.services.sector_quote_cache import get_spot_snapshot, get_spot_snapshot_any_age, save_spot_snapshot
from app.services.sector_quote_relay_provider import fetch_boards_via_relay
from app.services.shared_executors import get_shared_io_executor
from app.services.cache_policy import jittered_ttl
from app.services.cross_process_lock import (
    CrossProcessLockError,
    cross_process_lock,
)

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


def load_spot_boards_from_cache_only() -> SpotBoardFetchResult:
    """仅读板块快照缓存（新鲜或过期均可），不发起任何网络请求。"""
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

    fresh = get_spot_snapshot(cache_key, ttl_seconds=ttl)
    if fresh is not None and _boards_cacheable(fresh):
        return SpotBoardFetchResult(
            boards=fresh,
            provider_path="fresh_cache",
            elapsed_seconds=_elapsed(start_time),
        )

    stale = get_spot_snapshot_any_age(cache_key)
    if stale is not None and _boards_cacheable(stale):
        return SpotBoardFetchResult(
            boards=stale,
            provider_path="stale_cache",
            from_stale_cache=True,
            elapsed_seconds=_elapsed(start_time),
        )

    return SpotBoardFetchResult(
        boards=_empty_boards(),
        provider_path="cache_miss",
        elapsed_seconds=_elapsed(start_time),
    )


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

    cached = get_spot_snapshot(
        cache_key,
        ttl_seconds=_jittered_ttl(cache_key, ttl),
    )
    if not force_refresh:
        if cached is not None and not _boards_cacheable(cached):
            cached = None
        if cached is not None:
            return SpotBoardFetchResult(
                boards=cached,
                provider_path="fresh_cache",
                elapsed_seconds=_elapsed(start_time),
            )

    try:
        with cross_process_lock(
            f"sector-spot-refresh:{today}",
            timeout_seconds=max(
                0.5,
                min(5.0, float(timeout_seconds or 5.0)),
            ),
        ):
            # On a cold miss another process may have populated the snapshot
            # while this process waited for the advisory lock.
            if cached is None:
                filled = get_spot_snapshot(
                    cache_key,
                    ttl_seconds=_jittered_ttl(cache_key, ttl),
                )
                if filled is not None and _boards_cacheable(filled):
                    return SpotBoardFetchResult(
                        boards=filled,
                        provider_path="singleflight_cache",
                        live_attempted=False,
                        elapsed_seconds=_elapsed(start_time),
                    )
            live = _fetch_live_boards(timeout_seconds=timeout_seconds)
            if any(live.boards.values()) and _boards_cacheable(live.boards):
                save_spot_snapshot(cache_key, live.boards)
    except CrossProcessLockError as exc:
        logger.info("sector spot refresh single-flight unavailable: %s", exc)
        if stale_day is not None:
            return SpotBoardFetchResult(
                boards=stale_day,
                provider_path="stale_cache_lock_busy",
                from_stale_cache=True,
                live_attempted=False,
                elapsed_seconds=_elapsed(start_time),
            )
        live = SpotBoardFetchResult(
            boards=_empty_boards(),
            provider_path="refresh_lock_busy",
            live_attempted=False,
            elapsed_seconds=_elapsed(start_time),
        )
    if any(live.boards.values()) and _boards_cacheable(live.boards):
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
    provider_path, fast_boards = _race_fast_board_providers(
        start_time=start_time,
        timeout_seconds=timeout_seconds,
    )
    if fast_boards is not None:
        boards = fast_boards
        if provider_path == "eastmoney_live":
            boards = _fill_missing_boards_from_akshare(
                boards,
                timeout_seconds=timeout_seconds,
                start_time=start_time,
            )
        return SpotBoardFetchResult(
            boards=boards,
            provider_path=provider_path,
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


def _race_fast_board_providers(
    *,
    start_time: float,
    timeout_seconds: float | None,
) -> tuple[str, dict[str, SpotBoard] | None]:
    """Race configured fast paths and accept the first cacheable snapshot."""

    if _budget_exhausted(start_time, timeout_seconds):
        return "empty", None
    settings = get_settings()
    calls: list[tuple[str, Callable[[], object]]] = [
        (
            "eastmoney_live",
            lambda: fetch_eastmoney_boards(
                **_eastmoney_call_kwargs(timeout_seconds)
            ),
        )
    ]
    if settings.sector_quotes_relay_url:
        calls.append(
            (
                "relay_live",
                lambda: fetch_boards_via_relay(
                    timeout_seconds=_remaining_budget(
                        start_time,
                        timeout_seconds,
                    )
                ),
            )
        )
    if (
        settings.sector_quotes_browser_enabled
        and settings.sector_quotes_browser_command
        and _has_browser_budget(start_time, timeout_seconds)
    ):
        calls.append(
            (
                "browser_live",
                lambda: fetch_boards_via_browser_command(
                    timeout_seconds=_remaining_budget(
                        start_time,
                        timeout_seconds,
                    ),
                ),
            )
        )

    executor = get_shared_io_executor()
    pending = {
        executor.submit(call): provider_path
        for provider_path, call in calls
    }
    try:
        while pending:
            remaining = _remaining_budget(start_time, timeout_seconds)
            if timeout_seconds is not None and (
                remaining is None or remaining <= 0.5
            ):
                break
            done, _ = wait(
                pending,
                timeout=remaining,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                break
            for future in done:
                provider_path = pending.pop(future)
                try:
                    candidate = future.result()
                except Exception as exc:
                    logger.info("%s sector provider failed: %s", provider_path, exc)
                    continue
                if (
                    isinstance(candidate, dict)
                    and any(candidate.values())
                    and _boards_cacheable(candidate)
                ):
                    logger.info("%s sector provider won fast race", provider_path)
                    return provider_path, candidate
        return "empty", None
    finally:
        for future in pending:
            future.cancel()


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


def _jittered_ttl(cache_key: str, ttl_seconds: float) -> float:
    return max(1.0, jittered_ttl(cache_key, ttl_seconds))


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
