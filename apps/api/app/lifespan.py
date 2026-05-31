from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.services.inbox_watcher import start_inbox_watcher, stop_inbox_watcher
from app.services.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    settings = get_settings()
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    (settings.inbox_dir / "processed").mkdir(parents=True, exist_ok=True)
    start_inbox_watcher()
    start_scheduler()
    yield
    stop_inbox_watcher()
    stop_scheduler()
