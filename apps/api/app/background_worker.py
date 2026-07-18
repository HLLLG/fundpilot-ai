from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import signal
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from app.config import get_settings
from app.db_connect import initialize_database_connection
from app.services.cross_process_lock import (
    CrossProcessLockError,
    CrossProcessLockTimeout,
    cross_process_lock,
)
from app.services.sector_quote_cache import mark_process_boot


logger = logging.getLogger(__name__)
HEARTBEAT_SCHEMA_VERSION = "background_worker_heartbeat.v1"
LEADER_LOCK_RESOURCE = "background-worker:leader:v1"
_HEARTBEAT_PATH_ENV = "FUND_AI_BACKGROUND_WORKER_HEARTBEAT_PATH"


@dataclass(frozen=True)
class BackgroundJobSpec:
    name: str
    target: Callable[[], None]
    persistent: bool


@dataclass(frozen=True)
class RunningBackgroundJob:
    spec: BackgroundJobSpec
    thread: threading.Thread


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def heartbeat_path() -> Path:
    configured = os.getenv(_HEARTBEAT_PATH_ENV, "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "fundpilot-background-worker-heartbeat.json"


def configured_background_jobs() -> tuple[BackgroundJobSpec, ...]:
    settings = get_settings()
    jobs: list[BackgroundJobSpec] = []

    if settings.theme_board_refresh_enabled or settings.market_breadth_enabled:
        from app.services.market_shared_refresh import (
            market_shared_refresh_loop,
            run_startup_market_refresh,
        )

        jobs.extend(
            (
                BackgroundJobSpec(
                    name="market-startup-refresh",
                    target=run_startup_market_refresh,
                    persistent=False,
                ),
                BackgroundJobSpec(
                    name="market-shared-refresh",
                    target=market_shared_refresh_loop,
                    persistent=True,
                ),
            )
        )

    if settings.sector_quotes_enabled:
        from app.services.portfolio_sector_refresh import portfolio_sector_refresh_loop

        jobs.append(
            BackgroundJobSpec(
                name="portfolio-sector-refresh",
                target=portfolio_sector_refresh_loop,
                persistent=True,
            )
        )

    if (
        settings.fund_primary_sector_global_enabled
        and settings.fund_primary_sector_precompute_enabled
    ):
        from app.services.fund_primary_sector_precompute_loop import (
            fund_primary_sector_precompute_loop,
        )

        jobs.append(
            BackgroundJobSpec(
                name="fund-primary-sector-precompute",
                target=fund_primary_sector_precompute_loop,
                persistent=True,
            )
        )

    if settings.fund_primary_sector_backfill_enabled:
        from app.services.fund_primary_sector_backfill import (
            run_fund_primary_sector_backfill_once_at_startup,
        )

        jobs.append(
            BackgroundJobSpec(
                name="fund-primary-sector-backfill",
                target=run_fund_primary_sector_backfill_once_at_startup,
                persistent=False,
            )
        )

    if (
        settings.prompt_shadow_enabled
        and settings.prompt_shadow_assignment_secret
        and settings.deepseek_configured
    ):
        from app.services.prompt_shadow_worker import prompt_shadow_worker_loop

        jobs.append(
            BackgroundJobSpec(
                name="prompt-shadow-worker",
                target=prompt_shadow_worker_loop,
                persistent=True,
            )
        )

    return tuple(jobs)


def _run_job(spec: BackgroundJobSpec) -> None:
    if spec.persistent:
        spec.target()
        return
    try:
        spec.target()
    except Exception:
        logger.exception("one-shot background job failed job=%s", spec.name)


def start_background_jobs(
    specs: Sequence[BackgroundJobSpec] | None = None,
) -> tuple[RunningBackgroundJob, ...]:
    running: list[RunningBackgroundJob] = []
    for spec in tuple(specs) if specs is not None else configured_background_jobs():
        thread = threading.Thread(
            target=_run_job,
            args=(spec,),
            name=spec.name,
            daemon=True,
        )
        thread.start()
        running.append(RunningBackgroundJob(spec=spec, thread=thread))
    return tuple(running)


def _heartbeat_payload(
    *,
    worker_id: str,
    started_at: datetime,
    jobs: Sequence[RunningBackgroundJob],
) -> dict[str, object]:
    return {
        "schema_version": HEARTBEAT_SCHEMA_VERSION,
        "status": "leader",
        "worker_id": worker_id,
        "pid": os.getpid(),
        "started_at": started_at.isoformat(),
        "heartbeat_at": _utc_now().isoformat(),
        "jobs": [
            {
                "name": item.spec.name,
                "persistent": item.spec.persistent,
                "alive": item.thread.is_alive(),
            }
            for item in jobs
        ],
    }


def write_worker_heartbeat(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    # ``os.kill(pid, 0)`` maps to TerminateProcess on Windows. Query the
    # process handle instead so a health check can never stop the worker it is
    # inspecting.
    import ctypes

    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    return True


def _remove_owned_heartbeat(path: Path, *, worker_id: str) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if payload.get("worker_id") != worker_id:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def inspect_worker_health(
    path: Path | None = None,
    *,
    stale_after_seconds: float | None = None,
    now: datetime | None = None,
    verify_process: bool = True,
) -> dict[str, object]:
    """Inspect the latest leader heartbeat.

    ``verify_process`` stays enabled for the worker container's own Docker
    healthcheck.  The request-only API container shares the heartbeat file but
    not the worker PID namespace, so its evidence console deliberately verifies
    the signed-by-contract heartbeat freshness and job state without pretending
    that a cross-container PID lookup is meaningful.
    """
    resolved_path = path or heartbeat_path()
    stale_limit = float(
        stale_after_seconds
        if stale_after_seconds is not None
        else max(5.0, get_settings().background_worker_heartbeat_stale_seconds)
    )
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"healthy": False, "reason": "heartbeat_missing"}
    except (OSError, json.JSONDecodeError):
        return {"healthy": False, "reason": "heartbeat_invalid"}

    if (
        payload.get("schema_version") != HEARTBEAT_SCHEMA_VERSION
        or payload.get("status") != "leader"
    ):
        return {"healthy": False, "reason": "heartbeat_contract_invalid"}
    try:
        heartbeat_at = datetime.fromisoformat(
            str(payload["heartbeat_at"]).replace("Z", "+00:00")
        )
        if heartbeat_at.tzinfo is None:
            heartbeat_at = heartbeat_at.replace(tzinfo=timezone.utc)
        age_seconds = (
            (now or _utc_now()) - heartbeat_at.astimezone(timezone.utc)
        ).total_seconds()
        pid = int(payload["pid"])
    except (KeyError, TypeError, ValueError):
        return {"healthy": False, "reason": "heartbeat_contract_invalid"}
    heartbeat_at_iso = heartbeat_at.astimezone(timezone.utc).isoformat()
    if age_seconds < -5.0 or age_seconds > stale_limit:
        return {
            "healthy": False,
            "reason": "heartbeat_stale",
            "age_seconds": round(age_seconds, 3),
            "heartbeat_at": heartbeat_at_iso,
        }
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        return {"healthy": False, "reason": "heartbeat_contract_invalid"}
    dead_persistent = sorted(
        str(job.get("name") or "unknown")
        for job in jobs
        if job.get("persistent") is True and job.get("alive") is not True
    )
    if dead_persistent:
        return {
            "healthy": False,
            "reason": "persistent_job_missing",
            "age_seconds": round(age_seconds, 3),
            "heartbeat_at": heartbeat_at_iso,
            "dead_jobs": dead_persistent,
        }
    if verify_process and not _process_exists(pid):
        return {
            "healthy": False,
            "reason": "worker_process_missing",
            "heartbeat_at": heartbeat_at_iso,
        }
    return {
        "healthy": True,
        "reason": "ok",
        "age_seconds": round(age_seconds, 3),
        "started_at": payload.get("started_at"),
        "heartbeat_at": heartbeat_at_iso,
        "worker_id": payload.get("worker_id"),
        "jobs": jobs,
    }


def _supervise_leader(
    *,
    stop_event: threading.Event,
    worker_id: str,
    lease,
    path: Path,
) -> None:
    started_at = _utc_now()
    jobs = start_background_jobs()
    interval = float(
        max(1.0, get_settings().background_worker_heartbeat_interval_seconds)
    )
    try:
        while not stop_event.is_set():
            dead_persistent = [
                item.spec.name
                for item in jobs
                if item.spec.persistent and not item.thread.is_alive()
            ]
            if dead_persistent:
                raise RuntimeError(
                    "persistent background jobs exited: "
                    + ", ".join(sorted(dead_persistent))
                )
            lease.heartbeat()
            write_worker_heartbeat(
                path,
                _heartbeat_payload(
                    worker_id=worker_id,
                    started_at=started_at,
                    jobs=jobs,
                ),
            )
            stop_event.wait(interval)
    finally:
        _remove_owned_heartbeat(path, worker_id=worker_id)


def run_background_worker(stop_event: threading.Event | None = None) -> None:
    settings = get_settings()
    if settings.runtime_role not in {"all", "worker"}:
        raise RuntimeError(
            f"background worker cannot run with runtime role {settings.runtime_role!r}"
        )
    initialize_database_connection()
    mark_process_boot()
    stop = stop_event or threading.Event()
    worker_id = f"worker-{secrets.token_hex(12)}"
    path = heartbeat_path()
    lock_timeout = float(max(0.0, settings.background_worker_lock_timeout_seconds))
    retry_seconds = float(max(1.0, settings.background_worker_retry_seconds))

    while not stop.is_set():
        became_leader = False
        try:
            with cross_process_lock(
                LEADER_LOCK_RESOURCE,
                timeout_seconds=lock_timeout,
            ) as lease:
                became_leader = True
                logger.info("background worker became leader worker_id=%s", worker_id)
                _supervise_leader(
                    stop_event=stop,
                    worker_id=worker_id,
                    lease=lease,
                    path=path,
                )
                return
        except CrossProcessLockTimeout:
            logger.info("background worker leader is already active; retrying")
        except CrossProcessLockError:
            if became_leader:
                raise
            logger.exception("background worker coordination unavailable; retrying")
        if stop.wait(retry_seconds):
            return


def start_inline_background_worker() -> tuple[threading.Event, threading.Thread] | None:
    if get_settings().runtime_role != "all":
        return None
    stop_event = threading.Event()

    def _run() -> None:
        try:
            run_background_worker(stop_event)
        except Exception:
            logger.exception("inline background worker supervisor stopped")

    thread = threading.Thread(
        target=_run,
        name="background-worker-supervisor",
        daemon=True,
    )
    thread.start()
    return stop_event, thread


def _healthcheck() -> int:
    result = inspect_worker_health()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("healthy") is True else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FundPilot dedicated background worker")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args(argv)
    if args.healthcheck:
        return _healthcheck()

    stop_event = threading.Event()

    def _stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    run_background_worker(stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
