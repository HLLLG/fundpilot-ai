from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from app.config import get_settings
from app.database import _connect, get_discovery_report
from app.db_connect import uses_mysql
from app.models import DiscoveryRequest
from app.request_context import (
    get_request_user_id,
    reset_request_user_id,
    set_request_user_id,
)
from app.services.discovery_pipeline import run_discovery
from app.services.job_limits import (
    JobCapacity,
    JobQueueFull,
    canonical_job_dedup_key,
)

JobStatus = Literal["pending", "running", "completed", "failed"]

_settings = get_settings()
_executor = ThreadPoolExecutor(
    max_workers=max(1, int(_settings.async_job_max_workers)),
    thread_name_prefix="fund-discovery-job",
)
_capacity = JobCapacity(
    workers=_settings.async_job_max_workers,
    queued=_settings.async_job_queue_capacity,
)
_lock = threading.Lock()


def _ensure_discovery_jobs_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            request_payload TEXT NOT NULL,
            dedup_key TEXT,
            active_dedup_key TEXT,
            discovery_report_id TEXT,
            error TEXT,
            stage TEXT,
            stage_label TEXT,
            userId INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            heartbeat_at TEXT
        )
        """
    )
    if uses_mysql():
        return
    additions = {
        "dedup_key": "TEXT",
        "active_dedup_key": "TEXT",
        "heartbeat_at": "TEXT",
    }
    for column, definition in additions.items():
        try:
            connection.execute(
                f"ALTER TABLE discovery_jobs ADD COLUMN {column} {definition}"
            )
        except sqlite3.OperationalError:
            pass
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_jobs_active_dedup
        ON discovery_jobs (userId, active_dedup_key)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discovery_jobs_heartbeat
        ON discovery_jobs (status, heartbeat_at)
        """
    )


def _active_job_id(user_id: int, dedup_key: str) -> str | None:
    with _connect() as connection:
        _ensure_discovery_jobs_table(connection)
        row = connection.execute(
            """
            SELECT id FROM discovery_jobs
            WHERE userId = ? AND active_dedup_key = ?
              AND status IN ('pending', 'running')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id, dedup_key),
        ).fetchone()
    return str(row["id"]) if row is not None else None


def create_discovery_job(request: DiscoveryRequest) -> str:
    now = datetime.now(timezone.utc).isoformat()
    payload = request.model_dump(mode="json")
    serialized = json.dumps(payload, ensure_ascii=False)
    dedup_key = canonical_job_dedup_key(payload)
    user_id = get_request_user_id()

    with _lock:
        existing = _active_job_id(user_id, dedup_key)
        if existing is not None:
            return existing
        if not _capacity.try_acquire():
            raise JobQueueFull("discovery job queue is full")
        job_id = uuid4().hex
        try:
            with _connect() as connection:
                _ensure_discovery_jobs_table(connection)
                connection.execute(
                    """
                    INSERT INTO discovery_jobs (
                        id, status, request_payload, dedup_key,
                        active_dedup_key, stage, stage_label, userId,
                        created_at, updated_at, heartbeat_at
                    )
                    VALUES (
                        ?, 'pending', ?, ?, ?, 'queued', '排队中',
                        ?, ?, ?, ?
                    )
                    """,
                    (
                        job_id,
                        serialized,
                        dedup_key,
                        dedup_key,
                        user_id,
                        now,
                        now,
                        now,
                    ),
                )
        except Exception:
            _capacity.release()
            existing = _active_job_id(user_id, dedup_key)
            if existing is not None:
                return existing
            raise

        try:
            _executor.submit(_run_job, job_id, user_id, True)
        except Exception:
            failed_at = datetime.now(timezone.utc).isoformat()
            with _connect() as connection:
                connection.execute(
                    """
                    UPDATE discovery_jobs
                    SET status = 'failed', error = '任务执行器不可用',
                        stage = 'failed', stage_label = '提交失败',
                        active_dedup_key = NULL, updated_at = ?,
                        heartbeat_at = ?
                    WHERE id = ?
                    """,
                    (failed_at, failed_at, job_id),
                )
            _capacity.release()
            raise
    return job_id


def _heartbeat_loop(job_id: str, stop_event: threading.Event) -> None:
    interval = max(
        1.0,
        float(get_settings().async_job_heartbeat_interval_seconds),
    )
    while not stop_event.wait(interval):
        _update_job(job_id)


def _run_job(
    job_id: str,
    user_id: int,
    release_capacity: bool = False,
) -> None:
    ctx_token = set_request_user_id(user_id)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(job_id, heartbeat_stop),
        name=f"fund-discovery-heartbeat-{job_id[:8]}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        _update_job(
            job_id,
            status="running",
            stage="sector_heat",
            stage_label="计算板块热度…",
        )
        try:
            request = _load_request(job_id)

            def on_progress(stage: str, label: str) -> None:
                _update_job(
                    job_id,
                    status="running",
                    stage=stage,
                    stage_label=label,
                )

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
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1.0)
        reset_request_user_id(ctx_token)
        if release_capacity:
            _capacity.release()


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
                SELECT status, discovery_report_id, error, stage, stage_label,
                       active_dedup_key
                FROM discovery_jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                return
            next_status = status if status is not None else row["status"]
            active_dedup_key = (
                None
                if next_status in {"completed", "failed"}
                else row["active_dedup_key"]
            )
            connection.execute(
                """
                UPDATE discovery_jobs
                SET status = ?, discovery_report_id = ?, error = ?,
                    stage = ?, stage_label = ?, active_dedup_key = ?,
                    updated_at = ?, heartbeat_at = ?
                WHERE id = ?
                """,
                (
                    next_status,
                    discovery_report_id
                    if discovery_report_id is not None
                    else row["discovery_report_id"],
                    error if error is not None else row["error"],
                    stage if stage is not None else row["stage"],
                    stage_label
                    if stage_label is not None
                    else row["stage_label"],
                    active_dedup_key,
                    now,
                    now,
                    job_id,
                ),
            )


def cleanup_stale_discovery_jobs(
    *,
    stale_seconds: float | None = None,
) -> int:
    seconds = (
        float(get_settings().async_job_stale_seconds)
        if stale_seconds is None
        else float(stale_seconds)
    )
    stale_before = (
        datetime.now(timezone.utc) - timedelta(seconds=max(1.0, seconds))
    ).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    with _lock:
        with _connect() as connection:
            _ensure_discovery_jobs_table(connection)
            cursor = connection.execute(
                """
                UPDATE discovery_jobs
                SET status = 'failed',
                    error = COALESCE(error, '进程重启后任务心跳已过期'),
                    stage = 'failed',
                    stage_label = '任务心跳过期',
                    active_dedup_key = NULL,
                    updated_at = ?,
                    heartbeat_at = ?
                WHERE status IN ('pending', 'running')
                  AND COALESCE(heartbeat_at, updated_at) < ?
                """,
                (now, now, stale_before),
            )
            return max(0, int(cursor.rowcount or 0))


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
        "heartbeat_at": row["heartbeat_at"],
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
