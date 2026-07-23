from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.background_worker import start_inline_background_worker
from app.config import get_settings
from app.db_connect import initialize_database_connection, uses_mysql
from app.services.db_backup import maybe_auto_import_database
from app.services.fund_code_resolver import preload_fund_name_table
from app.services.ocr_engine import schedule_ocr_preload
from app.services.sector_quote_cache import mark_process_boot
from app.startup_readiness import mark_failed, mark_ready, mark_starting

logger = logging.getLogger(__name__)


def _initialize_runtime(app: FastAPI, shutdown_event: threading.Event) -> None:
    initialize_database_connection()
    maybe_auto_import_database()
    from app.services.discovery_job_store import cleanup_stale_discovery_jobs
    from app.services.job_store import cleanup_stale_analysis_jobs
    from app.services.stream_session_store import cleanup_expired_stream_sessions

    cleanup_stale_analysis_jobs()
    cleanup_stale_discovery_jobs()
    cleanup_expired_stream_sessions()
    if shutdown_event.is_set():
        mark_ready()
        return
    schedule_ocr_preload()
    if get_settings().fund_name_preload_enabled:
        threading.Thread(
            target=preload_fund_name_table,
            name="fund-name-table-preload",
            daemon=True,
        ).start()
    app.state.inline_background_worker = start_inline_background_worker()
    mark_ready()


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    mark_process_boot()
    mark_starting()
    shutdown_event = threading.Event()
    _app.state.inline_background_worker = None
    bootstrap_thread: threading.Thread | None = None
    background_bootstrap = bool(
        uses_mysql() and get_settings().startup_bootstrap_background
    )
    if background_bootstrap:
        def initialize_in_background() -> None:
            try:
                _initialize_runtime(_app, shutdown_event)
            except Exception as exc:  # noqa: BLE001 - readiness fails closed.
                mark_failed(exc)
                logger.exception("background runtime initialization failed")

        bootstrap_thread = threading.Thread(
            target=initialize_in_background,
            name="fund-ai-runtime-bootstrap",
            daemon=True,
        )
        bootstrap_thread.start()
    else:
        try:
            _initialize_runtime(_app, shutdown_event)
        except Exception as exc:
            mark_failed(exc)
            raise
    try:
        yield
    finally:
        shutdown_event.set()
        if bootstrap_thread is not None and bootstrap_thread.is_alive():
            bootstrap_thread.join(timeout=10.0)
        inline_worker = getattr(
            _app.state,
            "inline_background_worker",
            None,
        )
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
        from app.services.akshare_subprocess import close_akshare_worker_pool
        from app.services.shared_executors import close_shared_executors

        close_deepseek_http_clients()
        close_eastmoney_http_clients()
        close_akshare_worker_pool()
        close_shared_executors()
