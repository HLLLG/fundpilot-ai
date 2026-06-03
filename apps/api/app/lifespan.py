from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.services.db_backup import maybe_auto_import_database


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    maybe_auto_import_database()
    yield
