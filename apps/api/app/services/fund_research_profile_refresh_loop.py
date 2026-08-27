"""后台循环：整包写入规模档案 + 东财经理从业天数。

荐基请求路径只读表，不再拉源。空表或超过 24h TTL 时由本循环补；
生产另有每交易日 GitHub Actions 强制刷一次。
"""

from __future__ import annotations

import logging
import time

from app.config import get_settings

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return bool(get_settings().fund_research_profile_refresh_enabled)


def _poll_seconds() -> float:
    return float(max(60, int(get_settings().fund_research_profile_refresh_poll_seconds)))


def _startup_delay_seconds() -> float:
    return float(
        max(0, int(get_settings().fund_research_profile_refresh_startup_delay_seconds))
    )


def fund_research_profile_refresh_loop() -> None:
    if not _enabled():
        return

    delay = _startup_delay_seconds()
    if delay > 0:
        logger.info("fund research profile refresh starts in %ss", int(delay))
        time.sleep(delay)

    from app.services.fund_manager_roster import run_fund_manager_roster_refresh
    from app.services.fund_research_profile_store import (
        run_fund_research_profile_refresh,
    )

    while True:
        try:
            summary = run_fund_research_profile_refresh(force=False)
            logger.info(
                "fund research profile refresh ok=%s written=%s rows=%s stamp=%s",
                summary.get("ok"),
                summary.get("written"),
                summary.get("row_count"),
                summary.get("snapshot_available_at"),
            )
        except Exception:  # noqa: BLE001 - keep the daemon alive
            logger.exception("fund research profile refresh loop failed")
        try:
            roster = run_fund_manager_roster_refresh(force=False)
            logger.info(
                "fund manager roster refresh ok=%s written=%s rows=%s stamp=%s",
                roster.get("ok"),
                roster.get("written"),
                roster.get("row_count"),
                roster.get("snapshot_available_at"),
            )
        except Exception:  # noqa: BLE001 - keep the daemon alive
            logger.exception("fund manager roster refresh loop failed")
        time.sleep(_poll_seconds())
