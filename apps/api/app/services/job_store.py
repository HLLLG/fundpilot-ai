from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from app.config import get_settings
from app.database import _connect
from app.models import AnalysisRequest
from app.request_context import (
    get_request_user_id,
    reset_request_user_id,
    set_request_user_id,
)
from app.services.analyze_pipeline import run_analysis
from app.services.job_limits import (
    JobCapacity,
    JobQueueFull,
    canonical_job_dedup_key,
)

JobStatus = Literal["pending", "running", "completed", "failed"]

_settings = get_settings()
_executor = ThreadPoolExecutor(
    max_workers=max(1, int(_settings.async_job_max_workers)),
    thread_name_prefix="fund-ai-job",
)
_capacity = JobCapacity(
    workers=_settings.async_job_max_workers,
    queued=_settings.async_job_queue_capacity,
)
_lock = threading.Lock()
_schema_lock = threading.Lock()
_schema_ready_key: tuple[str, str] | None = None


def analysis_job_capacity_snapshot() -> dict[str, int]:
    return _capacity.snapshot()


def _ensure_jobs_table(connection: sqlite3.Connection) -> None:
    global _schema_ready_key
    dialect = str(getattr(connection, "dialect", "sqlite"))
    settings = get_settings()
    key = (
        dialect,
        settings.database_url or str(settings.db_path.resolve()),
    )
    if _schema_ready_key == key:
        return
    with _schema_lock:
        if _schema_ready_key == key:
            return
        # MySQL DDL is owned by the one-shot administrative bootstrap.  Never
        # acquire metadata locks again on a request or heartbeat path.
        if dialect == "mysql":
            _schema_ready_key = key
            return
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                request_payload TEXT NOT NULL,
                dedup_key TEXT,
                active_dedup_key TEXT,
                report_id TEXT,
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
        additions = {
            "stage": "TEXT",
            "stage_label": "TEXT",
            "userId": "INTEGER NOT NULL DEFAULT 1",
            "dedup_key": "TEXT",
            "active_dedup_key": "TEXT",
            "heartbeat_at": "TEXT",
        }
        for column, definition in additions.items():
            try:
                connection.execute(
                    f"ALTER TABLE analysis_jobs ADD COLUMN {column} {definition}"
                )
            except sqlite3.OperationalError:
                pass
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_analysis_jobs_active_dedup
            ON analysis_jobs (userId, active_dedup_key)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_jobs_heartbeat
            ON analysis_jobs (status, heartbeat_at)
            """
        )
        _schema_ready_key = key


def _active_job_id(user_id: int, dedup_key: str) -> str | None:
    with _connect() as connection:
        _ensure_jobs_table(connection)
        row = connection.execute(
            """
            SELECT id FROM analysis_jobs
            WHERE userId = ? AND active_dedup_key = ?
              AND status IN ('pending', 'running')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (user_id, dedup_key),
        ).fetchone()
    return str(row["id"]) if row is not None else None


def create_analysis_job(request: AnalysisRequest) -> str:
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
            raise JobQueueFull("analysis job queue is full")
        job_id = uuid4().hex
        try:
            with _connect() as connection:
                _ensure_jobs_table(connection)
                connection.execute(
                    """
                    INSERT INTO analysis_jobs (
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
                    UPDATE analysis_jobs
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
    started_at = time.perf_counter()
    outcome = "failed"
    ctx_token = set_request_user_id(user_id)
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(job_id, heartbeat_stop),
        name=f"fund-ai-job-heartbeat-{job_id[:8]}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        _update_job(
            job_id,
            status="running",
            stage="fund_data",
            stage_label="正在拉取净值与诊断数据…",
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

            report = run_analysis(request, on_progress=on_progress)
            _update_job(
                job_id,
                status="completed",
                report_id=report.id,
                stage="completed",
                stage_label="报告已生成",
            )
            outcome = "completed"
        except Exception as exc:
            _update_job(
                job_id,
                status="failed",
                error=str(exc),
                stage="failed",
                stage_label="分析失败",
            )
    finally:
        from app.services.performance_metrics import record_background_job

        record_background_job(
            "analysis",
            time.perf_counter() - started_at,
            outcome=outcome,
        )
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1.0)
        reset_request_user_id(ctx_token)
        if release_capacity:
            _capacity.release()


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
                """
                SELECT status, report_id, error, stage, stage_label,
                       active_dedup_key
                FROM analysis_jobs WHERE id = ?
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
                UPDATE analysis_jobs
                SET status = ?, report_id = ?, error = ?, stage = ?,
                    stage_label = ?, active_dedup_key = ?,
                    updated_at = ?, heartbeat_at = ?
                WHERE id = ?
                """,
                (
                    next_status,
                    report_id if report_id is not None else row["report_id"],
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


def cleanup_stale_analysis_jobs(*, stale_seconds: float | None = None) -> int:
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
            _ensure_jobs_table(connection)
            cursor = connection.execute(
                """
                UPDATE analysis_jobs
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
    return {
        "id": row["id"],
        "status": row["status"],
        "request": payload,
        "report_id": row["report_id"],
        "error": row["error"],
        "stage": row["stage"],
        "stage_label": row["stage_label"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "heartbeat_at": row["heartbeat_at"],
    }


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
