from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.database import _connect
from app.models import AnalysisRequest, Report
from app.services.analyze_pipeline import run_analysis

JobStatus = Literal["pending", "running", "completed", "failed"]

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fund-ai-job")
_lock = threading.Lock()


def _ensure_jobs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            report_id TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def create_analysis_job(request: AnalysisRequest) -> str:
    job_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    payload = request.model_dump(mode="json")
    with _lock:
        with _connect() as connection:
            _ensure_jobs_table(connection)
            connection.execute(
                """
                INSERT INTO analysis_jobs (id, status, request_payload, created_at, updated_at)
                VALUES (?, 'pending', ?, ?, ?)
                """,
                (job_id, json.dumps(payload, ensure_ascii=False), now, now),
            )
            connection.commit()
    _executor.submit(_run_job, job_id)
    return job_id


def _run_job(job_id: str) -> None:
    _update_job(job_id, status="running")
    try:
        request = _load_request(job_id)
        report = run_analysis(request)
        _update_job(
            job_id,
            status="completed",
            report_id=report.id,
        )
    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc))


def _load_request(job_id: str) -> AnalysisRequest:
    job = get_job(job_id)
    if job is None:
        raise ValueError("任务不存在")
    return AnalysisRequest.model_validate(job["request"])


def _update_job(
    job_id: str,
    *,
    status: JobStatus,
    report_id: str | None = None,
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _connect() as connection:
            _ensure_jobs_table(connection)
            connection.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, report_id = COALESCE(?, report_id), error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, report_id, error, now, job_id),
            )
            connection.commit()


def get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        _ensure_jobs_table(connection)
        row = connection.execute(
            "SELECT * FROM analysis_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["request_payload"])
    result: dict[str, Any] = {
        "id": row["id"],
        "status": row["status"],
        "request": payload,
        "report_id": row["report_id"],
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return result


def get_job_response(job_id: str) -> dict[str, Any] | None:
    from app.database import get_report

    job = get_job(job_id)
    if job is None:
        return None
    response: dict[str, Any] = {
        "id": job["id"],
        "status": job["status"],
        "error": job["error"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
    if job["status"] == "completed" and job["report_id"]:
        report = get_report(job["report_id"])
        if report is not None:
            response["report"] = report
    return response
