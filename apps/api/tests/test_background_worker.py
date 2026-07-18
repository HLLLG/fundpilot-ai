from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import background_worker
from app.config import refresh_settings


def _disabled_settings(**overrides):
    values = {
        "runtime_role": "api",
        "theme_board_refresh_enabled": False,
        "market_breadth_enabled": False,
        "sector_quotes_enabled": False,
        "fund_primary_sector_global_enabled": False,
        "fund_primary_sector_precompute_enabled": False,
        "fund_primary_sector_backfill_enabled": False,
        "prompt_shadow_enabled": False,
        "prompt_shadow_assignment_secret": None,
        "deepseek_configured": False,
        "background_worker_heartbeat_stale_seconds": 45.0,
        "background_worker_heartbeat_interval_seconds": 10.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_api_runtime_role_never_starts_inline_background_jobs(monkeypatch) -> None:
    monkeypatch.setattr(
        background_worker,
        "get_settings",
        lambda: _disabled_settings(runtime_role="api"),
    )

    assert background_worker.start_inline_background_worker() is None


def test_disabled_background_features_produce_no_worker_threads(monkeypatch) -> None:
    monkeypatch.setattr(
        background_worker,
        "get_settings",
        lambda: _disabled_settings(runtime_role="worker"),
    )

    assert background_worker.configured_background_jobs() == ()


def test_worker_heartbeat_is_atomic_current_and_process_bound(tmp_path) -> None:
    path = tmp_path / "worker-heartbeat.json"
    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": background_worker.HEARTBEAT_SCHEMA_VERSION,
        "status": "leader",
        "worker_id": "worker-test",
        "pid": os.getpid(),
        "started_at": now.isoformat(),
        "heartbeat_at": now.isoformat(),
        "jobs": [],
    }

    background_worker.write_worker_heartbeat(path, payload)

    result = background_worker.inspect_worker_health(
        path,
        stale_after_seconds=45,
        now=now + timedelta(seconds=10),
    )
    assert result["healthy"] is True
    assert result["reason"] == "ok"
    assert list(tmp_path.glob("*.tmp")) == []


def test_worker_health_fails_closed_for_stale_or_dead_heartbeat(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "worker-heartbeat.json"
    now = datetime.now(timezone.utc)
    payload = {
        "schema_version": background_worker.HEARTBEAT_SCHEMA_VERSION,
        "status": "leader",
        "worker_id": "worker-test",
        "pid": os.getpid(),
        "started_at": now.isoformat(),
        "heartbeat_at": now.isoformat(),
        "jobs": [],
    }
    background_worker.write_worker_heartbeat(path, payload)

    stale = background_worker.inspect_worker_health(
        path,
        stale_after_seconds=45,
        now=now + timedelta(seconds=46),
    )
    assert stale == {
        "healthy": False,
        "reason": "heartbeat_stale",
        "age_seconds": 46.0,
    }

    monkeypatch.setattr(background_worker, "_process_exists", lambda _pid: False)
    dead = background_worker.inspect_worker_health(
        path,
        stale_after_seconds=45,
        now=now,
    )
    assert dead == {"healthy": False, "reason": "worker_process_missing"}


def test_supervisor_fails_when_a_persistent_job_exits(tmp_path, monkeypatch) -> None:
    spec = background_worker.BackgroundJobSpec(
        name="dead-persistent-job",
        target=lambda: None,
        persistent=True,
    )
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join(timeout=1)
    running = background_worker.RunningBackgroundJob(spec=spec, thread=thread)
    monkeypatch.setattr(background_worker, "start_background_jobs", lambda: (running,))
    monkeypatch.setattr(
        background_worker,
        "get_settings",
        lambda: _disabled_settings(runtime_role="worker"),
    )

    lease = SimpleNamespace(heartbeat=lambda: None)
    with pytest.raises(RuntimeError, match="dead-persistent-job"):
        background_worker._supervise_leader(
            stop_event=threading.Event(),
            worker_id="worker-test",
            lease=lease,
            path=tmp_path / "heartbeat.json",
        )


def test_sqlite_worker_acquires_leadership_and_serves_healthcheck(
    tmp_path,
    monkeypatch,
) -> None:
    heartbeat = tmp_path / "worker-heartbeat.json"
    monkeypatch.setenv("FUND_AI_RUNTIME_ROLE", "worker")
    monkeypatch.setenv("FUND_AI_BACKGROUND_WORKER_HEARTBEAT_PATH", str(heartbeat))
    monkeypatch.setenv("FUND_AI_BACKGROUND_WORKER_HEARTBEAT_INTERVAL_SECONDS", "1")
    monkeypatch.setenv("FUND_AI_THEME_BOARD_REFRESH_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_MARKET_BREADTH_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_SECTOR_QUOTES_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_FUND_PRIMARY_SECTOR_GLOBAL_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_FUND_PRIMARY_SECTOR_BACKFILL_ENABLED", "false")
    monkeypatch.setenv("FUND_AI_PROMPT_SHADOW_ENABLED", "false")
    refresh_settings()

    stop_event = threading.Event()
    worker = threading.Thread(
        target=background_worker.run_background_worker,
        args=(stop_event,),
    )
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not heartbeat.is_file():
            time.sleep(0.05)
        result = background_worker.inspect_worker_health(
            heartbeat,
            stale_after_seconds=5,
        )
        assert result["healthy"] is True
    finally:
        stop_event.set()
        worker.join(timeout=5)
        monkeypatch.setenv("FUND_AI_RUNTIME_ROLE", "api")
        refresh_settings()

    assert not worker.is_alive()
    assert not heartbeat.exists()
