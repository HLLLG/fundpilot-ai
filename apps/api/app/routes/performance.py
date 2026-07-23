from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.background_worker import inspect_worker_health
from app.config import get_settings
from app.database import _connect
from app.db_connect import dedicated_mysql_session_pool_snapshot
from app.services.akshare_subprocess import akshare_worker_pool_snapshot
from app.services.discovery_job_store import discovery_job_capacity_snapshot
from app.services.job_store import analysis_job_capacity_snapshot
from app.services.performance_metrics import (
    normalize_request_path,
    performance_snapshot,
    record_web_vital,
)
from app.services.shared_executors import shared_executor_snapshot
from app.services.stream_admission import active_stream_count

router = APIRouter(tags=["performance"])


class WebVitalObservation(BaseModel):
    name: Literal["CLS", "FCP", "INP", "LCP", "TTFB"]
    value: float = Field(ge=0, le=600_000)
    path: str = Field(default="/", max_length=240)
    rating: Literal["good", "needs-improvement", "poor", "unknown"] = "unknown"


def _require_admin(request: Request) -> int:
    principal = request.scope.get("state", {}).get("auth_principal")
    if not isinstance(principal, dict) or str(principal.get("userRole")) != "admin":
        raise HTTPException(status_code=403, detail="administrator access required")
    return int(principal["id"])


def _database_runtime_snapshot() -> dict:
    settings = get_settings()
    if not settings.uses_mysql:
        path = Path(settings.db_path)
        return {
            "dialect": "sqlite",
            "database_bytes": path.stat().st_size if path.exists() else 0,
        }
    try:
        with _connect() as connection:
            status_rows = connection.execute(
                """
                SHOW GLOBAL STATUS
                WHERE Variable_name IN (
                    'Threads_connected',
                    'Threads_running',
                    'Max_used_connections',
                    'Connections',
                    'Aborted_connects'
                )
                """
            ).fetchall()
            variable_rows = connection.execute(
                """
                SHOW GLOBAL VARIABLES
                WHERE Variable_name IN ('max_connections', 'wait_timeout')
                """
            ).fetchall()
        values = {
            str(row.get("Variable_name") or "").lower(): _coerce_number(
                row.get("Value")
            )
            for row in [*status_rows, *variable_rows]
        }
        return {
            "dialect": "mysql",
            "threads_connected": values.get("threads_connected"),
            "threads_running": values.get("threads_running"),
            "max_used_connections": values.get("max_used_connections"),
            "max_connections": values.get("max_connections"),
            "connections_total": values.get("connections"),
            "aborted_connects": values.get("aborted_connects"),
            "wait_timeout_seconds": values.get("wait_timeout"),
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics remain available.
        return {
            "dialect": "mysql",
            "available": False,
            "failure_category": exc.__class__.__name__,
        }


def _coerce_number(value: object) -> int | float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


@router.get("/api/admin/performance")
def admin_performance(
    response: Response,
    _actor_id: int = Depends(_require_admin),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    result = performance_snapshot()
    result["runtime"] = {
        "database": _database_runtime_snapshot(),
        "dedicated_mysql_sessions": dedicated_mysql_session_pool_snapshot(),
        "shared_executors": shared_executor_snapshot(),
        "akshare_workers": akshare_worker_pool_snapshot(),
        "active_sse": active_stream_count(),
        "jobs": {
            "analysis": analysis_job_capacity_snapshot(),
            "discovery": discovery_job_capacity_snapshot(),
        },
        "background_worker": inspect_worker_health(verify_process=False),
    }
    return result


@router.post("/api/telemetry/web-vitals", status_code=202)
def web_vitals(
    body: WebVitalObservation,
    response: Response,
) -> dict[str, bool]:
    # The route is authenticated by AuthMiddleware. Only a normalized path and
    # the metric value survive; no user identity is retained.
    record_web_vital(
        body.name,
        body.value,
        route=normalize_request_path(body.path),
    )
    response.headers["Cache-Control"] = "no-store"
    return {"accepted": True}
