"""后台循环：全市场净值日更 + 断点回填。

荐基目录刷新仍会顺手触发一次 sidecar，但不能当成日更保证。
本循环保证 worker 在线时每个上海自然日至少跑一轮日更；
回填只在 MySQL（或显式打开开关）时启动，避免本地 SQLite 灌 2 万只。
生产另有每个交易日 22:10 的 GitHub Actions 强制刷一次。
"""

from __future__ import annotations

import logging
import time

from app.config import get_settings

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(get_settings().fund_nav_series_refresh_enabled)


def _poll_seconds() -> float:
    return float(max(60, int(get_settings().fund_nav_series_refresh_poll_seconds)))


def _startup_delay_seconds() -> float:
    return float(
        max(0, int(get_settings().fund_nav_series_refresh_startup_delay_seconds))
    )


def fund_nav_series_refresh_loop() -> None:
    if not _enabled():
        return

    delay = _startup_delay_seconds()
    if delay > 0:
        logger.info("fund nav series refresh starts in %ss", int(delay))
        time.sleep(delay)

    from app.services.fund_nav_series import (
        daily_nav_series_already_ran_today,
        run_daily_nav_series_and_risk,
        schedule_nav_series_backfill,
    )

    while True:
        try:
            if daily_nav_series_already_ran_today():
                logger.info("fund nav series daily sync skipped; already ran today")
            else:
                summary = run_daily_nav_series_and_risk()
                daily = summary.get("daily") or {}
                logger.info(
                    "fund nav series daily sync written=%s purged=%s risk=%s latest=%s",
                    daily.get("written"),
                    daily.get("purged"),
                    summary.get("risk_written"),
                    daily.get("latest_date"),
                )
        except Exception:  # noqa: BLE001 - keep the daemon alive
            logger.exception("fund nav series daily refresh loop failed")
        try:
            schedule_nav_series_backfill()
        except Exception:  # noqa: BLE001 - keep the daemon alive
            logger.exception("fund nav series backfill schedule failed")
        time.sleep(_poll_seconds())
