"""daemon：周期性批量预计算 fund_primary_sectors_global。"""

from __future__ import annotations

import logging
import time

from app.config import get_settings
from app.services.fund_primary_sector_precompute import run_precompute_batch

logger = logging.getLogger(__name__)


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


def fund_primary_sector_precompute_loop() -> None:
    if not _enabled():
        return

    delay = _startup_delay_seconds()
    logger.info("fund primary sector precompute sleeping %ss before first batch", int(delay))
    time.sleep(delay)

    while True:
        try:
            run_precompute_batch(mode="benchmark", force=False)
        except Exception as exc:
            logger.info("fund primary sector precompute batch failed: %s", exc)
        time.sleep(_interval_seconds())
