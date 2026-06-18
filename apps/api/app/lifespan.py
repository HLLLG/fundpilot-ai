from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.db_backup import maybe_auto_import_database
from app.services.fund_code_resolver import preload_fund_name_table
from app.services.ocr_engine import schedule_ocr_preload
from app.services.theme_board_snapshot import _refresh_enabled, theme_board_refresh_loop


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    maybe_auto_import_database()
    schedule_ocr_preload()
    threading.Thread(
        target=preload_fund_name_table,
        name="fund-name-table-preload",
        daemon=True,
    ).start()
    if _refresh_enabled():
        threading.Thread(
            target=theme_board_refresh_loop,
            name="theme-board-refresh",
            daemon=True,
        ).start()
    yield
