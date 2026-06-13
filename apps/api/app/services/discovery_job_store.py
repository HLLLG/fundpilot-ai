from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.database import _connect, get_discovery_report
from app.db_connect import uses_mysql
from app.models import DiscoveryRequest, FundDiscoveryReport
from app.request_context import get_request_user_id, reset_request_user_id, set_request_user_id
from app.services.discovery_pipeline import run_discovery

JobStatus = Literal["pending", "running", "completed", "failed"]

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fund-discovery-job")
_lock = threading.Lock()


def _ensure_discovery_jobs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            discovery_report_id TEXT,
            error TEXT,
            stage TEXT,
            stage_label TEXT,
            userId INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def create_discovery_job(request: DiscoveryRequest) -> str:
    job_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    payload = request.model_dump(mode="json")
    user_id = get_request_user_id()
    with _lock:
        with _connect() as connection:
            _ensure_discovery_jobs_table(connection)
            connection.execute(
                """
                INSERT INTO discovery_jobs (
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
        _update_job(job_id, status="running", stage="sector_heat", stage_label="计算板块热度…")
        try:
            request = _load_request(job_id)

            def on_progress(stage: str, label: str) -> None:
                _update_job(job_id, status="running", stage=stage, stage_label=label)

            report = run_discovery(request, on_progress=on_progress)
            _update_job(
                job_id,
                status="completed",
                discovery_report_id=report.id,
                stage="completed",
                stage_label="推荐报告已生成",
            )
        except Exception as exc:
            _update_job(
                job_id,
                status="failed",
                error=str(exc),
                stage="failed",
                stage_label="生成失败",
            )
    finally:
        reset_request_user_id(ctx_token)


def _load_request(job_id: str) -> DiscoveryRequest:
    job = get_discovery_job(job_id)
    if job is None:
        raise ValueError("任务不存在")
    return DiscoveryRequest.model_validate(job["request"])


def _update_job(
    job_id: str,
    *,
    status: JobStatus | None = None,
    discovery_report_id: str | None = None,
    error: str | None = None,
    stage: str | None = None,
    stage_label: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _connect() as connection:
            _ensure_discovery_jobs_table(connection)
            row = connection.execute(
                """
                SELECT status, discovery_report_id, error, stage, stage_label
                FROM discovery_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE discovery_jobs
                SET status = ?, discovery_report_id = ?, error = ?, stage = ?, stage_label = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status if status is not None else row["status"],
                    discovery_report_id if discovery_report_id is not None else row["discovery_report_id"],
                    error if error is not None else row["error"],
                    stage if stage is not None else row["stage"],
                    stage_label if stage_label is not None else row["stage_label"],
                    now,
                    job_id,
                ),
            )
            connection.commit()


def get_discovery_job(job_id: str) -> dict[str, Any] | None:
    user_id = get_request_user_id()
    with _connect() as connection:
        _ensure_discovery_jobs_table(connection)
        row = connection.execute(
            "SELECT * FROM discovery_jobs WHERE id = ? AND userId = ?",
            (job_id, user_id),
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["request_payload"])
    return {
        "id": row["id"],
        "status": row["status"],
        "request": payload,
        "discovery_report_id": row["discovery_report_id"],
        "error": row["error"],
        "stage": row["stage"],
        "stage_label": row["stage_label"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_discovery_job_response(job_id: str) -> dict[str, Any] | None:
    job = get_discovery_job(job_id)
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
        "job_kind": "discovery",
    }
    if job["status"] == "completed" and job.get("discovery_report_id"):
        report = get_discovery_report(job["discovery_report_id"])
        if report is not None:
            response["discovery_report"] = report
    return response
