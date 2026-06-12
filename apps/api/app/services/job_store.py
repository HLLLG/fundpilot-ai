from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.database import _connect
from app.db_connect import uses_mysql
from app.models import AnalysisRequest, Report
from app.request_context import get_request_user_id, reset_request_user_id, set_request_user_id
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
            stage TEXT,
            stage_label TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    # MySQL schema is created with all columns in mysql_bootstrap.ensure_mysql_schema.
    if uses_mysql():
        return
    try:
        connection.execute("ALTER TABLE analysis_jobs ADD COLUMN stage TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        connection.execute("ALTER TABLE analysis_jobs ADD COLUMN stage_label TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        connection.execute("ALTER TABLE analysis_jobs ADD COLUMN userId INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass


def create_analysis_job(request: AnalysisRequest) -> str:
    job_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    payload = request.model_dump(mode="json")
    user_id = get_request_user_id()
    with _lock:
        with _connect() as connection:
            _ensure_jobs_table(connection)
            connection.execute(
                """
                INSERT INTO analysis_jobs (
                    id, status, request_payload, stage, stage_label, userId, created_at, updated_at
                )
                VALUES (?, 'pending', ?, 'queued', '排队中…', ?, ?, ?)
                """,
                (job_id, json.dumps(payload, ensure_ascii=False), user_id, now, now),
            )
            connection.commit()
    _executor.submit(_run_job, job_id, user_id)
    return job_id


def _run_job(job_id: str, user_id: int) -> None:
    ctx_token = set_request_user_id(user_id)
    try:
        _update_job(job_id, status="running", stage="fund_data", stage_label="正在拉取净值与诊断数据…")
        try:
            request = _load_request(job_id)

            def on_progress(stage: str, label: str) -> None:
                _update_job(job_id, status="running", stage=stage, stage_label=label)

            report = run_analysis(request, on_progress=on_progress)
            _update_job(
                job_id,
                status="completed",
                report_id=report.id,
                stage="completed",
                stage_label="报告已生成",
            )
        except Exception as exc:
            _update_job(
                job_id,
                status="failed",
                error=str(exc),
                stage="failed",
                stage_label="分析失败",
            )
    finally:
        reset_request_user_id(ctx_token)


def _load_request(job_id: str) -> AnalysisRequest:
    job = get_job(job_id)
    if job is None:
        raise ValueError("任务不存在")
    return AnalysisRequest.model_validate(job["request"])


def _update_job(
    job_id: str,
    *,
    status: JobStatus | None = None,
    report_id: str | None = None,
    error: str | None = None,
    stage: str | None = None,
    stage_label: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _connect() as connection:
            _ensure_jobs_table(connection)
            row = connection.execute(
                "SELECT status, report_id, error, stage, stage_label FROM analysis_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE analysis_jobs
                SET status = ?, report_id = ?, error = ?, stage = ?, stage_label = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status if status is not None else row["status"],
                    report_id if report_id is not None else row["report_id"],
                    error if error is not None else row["error"],
                    stage if stage is not None else row["stage"],
                    stage_label if stage_label is not None else row["stage_label"],
                    now,
                    job_id,
                ),
            )
            connection.commit()


def get_job(job_id: str) -> dict[str, Any] | None:
    user_id = get_request_user_id()
    with _connect() as connection:
        _ensure_jobs_table(connection)
        row = connection.execute(
            "SELECT * FROM analysis_jobs WHERE id = ? AND userId = ?",
            (job_id, user_id),
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
        "stage": row["stage"] if "stage" in row.keys() else None,
        "stage_label": row["stage_label"] if "stage_label" in row.keys() else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return result


def get_job_response(job_id: str) -> dict[str, Any] | None:
    from app.database import get_report

    job = get_job(job_id)
    if job is None:
        return None
    request = job.get("request") or {}
    response: dict[str, Any] = {
        "id": job["id"],
        "status": job["status"],
        "error": job["error"],
        "stage": job.get("stage"),
        "stage_label": job.get("stage_label"),
        "analysis_mode": request.get("analysis_mode", "fast"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
    if job["status"] == "completed" and job["report_id"]:
        report = get_report(job["report_id"])
        if report is not None:
            response["report"] = report
    return response
