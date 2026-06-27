from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.database import _connect, get_discovery_report
from app.request_context import get_request_user_id
from app.services.discovery_job_store import _ensure_discovery_jobs_table
from app.services.job_store import _ensure_jobs_table


def resolve_job_status_single_connection(job_id: str) -> dict[str, Any]:
    """单连接查询 discovery / analysis 任务（进一步减少连接数）。"""
    user_id = get_request_user_id()
    try:
        with _connect() as connection:
            _ensure_discovery_jobs_table(connection)
            row = connection.execute(
                "SELECT * FROM discovery_jobs WHERE id = ? AND userId = ?",
                (job_id, user_id),
            ).fetchone()
            if row is not None:
                return _discovery_response_from_row(row)

            _ensure_jobs_table(connection)
            analysis_row = connection.execute(
                "SELECT * FROM analysis_jobs WHERE id = ? AND userId = ?",
                (job_id, user_id),
            ).fetchone()
            if analysis_row is not None:
                return _analysis_response_from_row(analysis_row)
    except HTTPException:
        raise
    except Exception as exc:
        name = type(exc).__name__
        message = str(exc)
        if "OperationalError" in name or "TimeoutError" in name or "Can't connect" in message:
            now = datetime.now(timezone.utc).isoformat()
            return {
                "id": job_id,
                "status": "running",
                "error": None,
                "stage": "transient_unavailable",
                "stage_label": "数据库连接波动，正在重试...",
                "analysis_mode": "fast",
                "created_at": now,
                "updated_at": now,
                "transient_unavailable": True,
            }
        raise
    raise HTTPException(status_code=404, detail="任务不存在")


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    if hasattr(row, "keys") and key in row.keys():
        return row[key]
    return None


def _discovery_response_from_row(row: Any) -> dict[str, Any]:
    request = json.loads(_row_get(row, "request_payload") or "{}")
    response: dict[str, Any] = {
        "id": _row_get(row, "id"),
        "status": _row_get(row, "status"),
        "error": _row_get(row, "error"),
        "stage": _row_get(row, "stage"),
        "stage_label": _row_get(row, "stage_label"),
        "analysis_mode": request.get("analysis_mode", "fast"),
        "created_at": _row_get(row, "created_at"),
        "updated_at": _row_get(row, "updated_at"),
        "job_kind": "discovery",
    }
    if response["status"] == "completed" and _row_get(row, "discovery_report_id"):
        report = get_discovery_report(str(_row_get(row, "discovery_report_id")))
        if report is not None:
            response["discovery_report"] = report
    return response


def _analysis_response_from_row(row: Any) -> dict[str, Any]:
    from app.database import get_report

    request = json.loads(_row_get(row, "request_payload") or "{}")
    response: dict[str, Any] = {
        "id": _row_get(row, "id"),
        "status": _row_get(row, "status"),
        "error": _row_get(row, "error"),
        "stage": _row_get(row, "stage"),
        "stage_label": _row_get(row, "stage_label"),
        "analysis_mode": request.get("analysis_mode", "fast"),
        "created_at": _row_get(row, "created_at"),
        "updated_at": _row_get(row, "updated_at"),
    }
    if response["status"] == "completed" and _row_get(row, "report_id"):
        report = get_report(str(_row_get(row, "report_id")))
        if report is not None:
            response["report"] = report
    return response
