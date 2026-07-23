from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.background_worker import start_inline_background_worker
from app.config import get_settings
from app.db_connect import initialize_database_connection
from app.services.db_backup import maybe_auto_import_database
from app.services.fund_code_resolver import preload_fund_name_table
from app.services.ocr_engine import schedule_ocr_preload
from app.services.sector_quote_cache import mark_process_boot


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    mark_process_boot()
    # Complete MySQL schema bootstrap before request handling and before any
    # background worker opens its own thread-local connection. Subsequent
    # connections share the per-process schema-ready marker.
    initialize_database_connection()
    maybe_auto_import_database()
    from app.services.discovery_job_store import cleanup_stale_discovery_jobs
    from app.services.job_store import cleanup_stale_analysis_jobs
    from app.services.stream_session_store import (
        cleanup_expired_stream_sessions,
    )

    cleanup_stale_analysis_jobs()
    cleanup_stale_discovery_jobs()
    cleanup_expired_stream_sessions()
    schedule_ocr_preload()
    if get_settings().fund_name_preload_enabled:
        threading.Thread(
            target=preload_fund_name_table,
            name="fund-name-table-preload",
            daemon=True,
        ).start()
    inline_worker = start_inline_background_worker()
    try:
        yield
    finally:
        if inline_worker is not None:
            stop_event, worker_thread = inline_worker
            stop_event.set()
            worker_thread.join(
                timeout=max(
                    2.0,
                    get_settings().background_worker_lock_timeout_seconds + 2.0,
                )
            )
        from app.services.deepseek_http import close_deepseek_http_clients
        from app.services.eastmoney_http import close_eastmoney_http_clients

        close_deepseek_http_clients()
        close_eastmoney_http_clients()
