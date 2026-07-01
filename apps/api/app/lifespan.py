from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.db_backup import maybe_auto_import_database
from app.services.fund_code_resolver import preload_fund_name_table
from app.services.ocr_engine import schedule_ocr_preload
from app.services.sector_quote_cache import mark_process_boot
from app.services.market_shared_refresh import (
    _refresh_enabled,
    market_shared_refresh_loop,
    run_startup_market_refresh,
)
from app.services.portfolio_sector_refresh import portfolio_sector_refresh_loop
from app.services.fund_primary_sector_precompute_loop import fund_primary_sector_precompute_loop
from app.services.fund_primary_sector_backfill import (
    run_fund_primary_sector_backfill_once_at_startup,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    mark_process_boot()
    maybe_auto_import_database()
    schedule_ocr_preload()
    threading.Thread(
        target=preload_fund_name_table,
        name="fund-name-table-preload",
        daemon=True,
    ).start()
    if _refresh_enabled():

        def _startup_refresh() -> None:
            try:
                run_startup_market_refresh()
            except Exception as exc:
                logger.info("market shared startup refresh failed: %s", exc)

        threading.Thread(
            target=_startup_refresh,
            name="market-startup-refresh",
            daemon=True,
        ).start()
        threading.Thread(
            target=market_shared_refresh_loop,
            name="market-shared-refresh",
            daemon=True,
        ).start()
    threading.Thread(
        target=portfolio_sector_refresh_loop,
        name="portfolio-sector-refresh",
        daemon=True,
    ).start()
    threading.Thread(
        target=fund_primary_sector_precompute_loop,
        name="fund-primary-sector-precompute",
        daemon=True,
    ).start()
    threading.Thread(
        target=run_fund_primary_sector_backfill_once_at_startup,
        name="fund-primary-sector-backfill",
        daemon=True,
    ).start()
    yield
