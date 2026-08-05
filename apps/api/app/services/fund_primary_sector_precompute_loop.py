"""daemon：周期性批量预计算 fund_primary_sectors_global。"""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.services.fund_primary_sector_precompute import (
    bulk_profile_resolution_backlog_pending,
    migrate_legacy_pending_profile_resolutions,
    run_precompute_batch,
    run_priority_precompute_batch,
)

logger = logging.getLogger(__name__)
_PRIORITY_POLL_SECONDS = 60.0


def _enabled() -> bool:
    settings = get_settings()
    return bool(
        settings.fund_primary_sector_global_enabled
        and settings.fund_primary_sector_precompute_enabled
    )


def _interval_seconds() -> float:
    hours = max(1, int(get_settings().fund_primary_sector_precompute_interval_hours))
    return hours * 3600.0


def _startup_delay_seconds() -> float:
    return float(max(60, int(get_settings().fund_primary_sector_precompute_startup_delay_seconds)))


def _backfill_pause_seconds() -> float:
    return float(
        max(1, int(get_settings().fund_primary_sector_precompute_backfill_pause_seconds))
    )


def _holdings_backfill_pause_seconds() -> float:
    return float(
        max(
            1,
            int(
                get_settings().fund_primary_sector_precompute_holdings_backfill_pause_seconds
            ),
        )
    )


def _wake_holdings_after_benchmark_queue(
    scheduled_at: float,
    result: object | None,
    *,
    now: float | None = None,
) -> float:
    """Make newly queued profile rows eligible for the next worker tick."""

    try:
        queued = int(getattr(result, "queued", 0) or 0)
    except (TypeError, ValueError):
        queued = 0
    if queued <= 0:
        return scheduled_at
    ready_at = time.monotonic() if now is None else now
    return min(scheduled_at, ready_at)


def fund_primary_sector_precompute_loop() -> None:
    if not _enabled():
        return

    delay = _startup_delay_seconds()
    next_regular_batch_at = time.monotonic() + delay
    next_holdings_batch_at = time.monotonic() + delay
    try:
        migrated = migrate_legacy_pending_profile_resolutions()
        if migrated.processed:
            logger.info(
                "fund primary sector pending migration processed=%s queued=%s research_only=%s unmapped=%s",
                migrated.processed,
                migrated.queued,
                migrated.research_only,
                migrated.unmapped,
            )
    except Exception as exc:
        logger.info("fund primary sector pending migration failed: %s", exc)
    logger.info(
        "fund primary sector precompute regular batch starts in %ss; priority queue is active",
        int(delay),
    )
    while True:
        try:
            run_priority_precompute_batch()
        except Exception as exc:
            logger.info("fund primary sector priority batch failed: %s", exc)

        now = time.monotonic()
        if now >= next_holdings_batch_at:
            try:
                holdings_result = run_precompute_batch(
                    mode="holdings",
                    force=False,
                    sleep_seconds=0.0,
                )
            except Exception as exc:
                logger.info("fund primary sector holdings batch failed: %s", exc)
                holdings_result = None
            next_holdings_batch_at = time.monotonic() + (
                _holdings_backfill_pause_seconds()
                if holdings_result is not None and holdings_result.processed > 0
                else _interval_seconds()
            )

        if now >= next_regular_batch_at:
            try:
                result = run_precompute_batch(mode="benchmark", force=False)
            except Exception as exc:
                logger.info("fund primary sector precompute batch failed: %s", exc)
                result = None
            if (
                result is not None
                and result.processed > 0
                and bulk_profile_resolution_backlog_pending()
            ):
                # The all-market pass is checkpointed after every provider
                # chunk. Keep draining missing rows and due retry backlogs with
                # a short bounded pause; each processed row advances its own
                # retry checkpoint, so provider failures cannot hot-loop.
                next_regular_batch_at = time.monotonic() + _backfill_pause_seconds()
            else:
                next_regular_batch_at = time.monotonic() + _interval_seconds()
            previous_holdings_batch_at = next_holdings_batch_at
            next_holdings_batch_at = _wake_holdings_after_benchmark_queue(
                next_holdings_batch_at,
                result,
            )
            if next_holdings_batch_at < previous_holdings_batch_at:
                logger.info(
                    "fund primary sector holdings batch woken for %s newly queued profiles",
                    result.queued,
                )

        seconds_until_regular = max(1.0, next_regular_batch_at - time.monotonic())
        seconds_until_holdings = max(1.0, next_holdings_batch_at - time.monotonic())
        time.sleep(
            min(
                _PRIORITY_POLL_SECONDS,
                seconds_until_regular,
                seconds_until_holdings,
            )
        )
