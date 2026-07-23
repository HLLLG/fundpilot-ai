from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.database import _connect
from app.models import AnalysisRequest, DiscoveryRequest, Holding, InvestorProfile
from app.services import discovery_job_store, job_store
from app.services.job_limits import JobQueueFull


class _Executor:
    def __init__(self) -> None:
        self.submissions: list[tuple] = []

    def submit(self, *args):
        self.submissions.append(args)
        return object()


class _Capacity:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.acquired = 0
        self.released = 0

    def try_acquire(self) -> bool:
        if not self.allowed:
            return False
        self.acquired += 1
        return True

    def release(self) -> None:
        self.released += 1


def _analysis_request() -> AnalysisRequest:
    return AnalysisRequest(
        holdings=[
            Holding(
                fund_code="000001",
                fund_name="测试基金",
                holding_amount=10_000,
            )
        ]
    )


def _discovery_request() -> DiscoveryRequest:
    return DiscoveryRequest(
        profile=InvestorProfile(),
        holdings=[
            Holding(
                fund_code="000001",
                fund_name="测试基金",
                holding_amount=10_000,
            )
        ],
    )


def test_analysis_duplicate_submission_reuses_one_active_row(monkeypatch) -> None:
    executor = _Executor()
    capacity = _Capacity()
    monkeypatch.setattr(job_store, "_executor", executor)
    monkeypatch.setattr(job_store, "_capacity", capacity)

    first = job_store.create_analysis_job(_analysis_request())
    second = job_store.create_analysis_job(_analysis_request())

    assert second == first
    assert len(executor.submissions) == 1
    assert capacity.acquired == 1
    with _connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS value FROM analysis_jobs"
        ).fetchone()["value"]
    assert count == 1


def test_discovery_duplicate_submission_reuses_one_active_row(
    monkeypatch,
) -> None:
    executor = _Executor()
    capacity = _Capacity()
    monkeypatch.setattr(discovery_job_store, "_executor", executor)
    monkeypatch.setattr(discovery_job_store, "_capacity", capacity)

    first = discovery_job_store.create_discovery_job(_discovery_request())
    second = discovery_job_store.create_discovery_job(_discovery_request())

    assert second == first
    assert len(executor.submissions) == 1
    with _connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS value FROM discovery_jobs"
        ).fetchone()["value"]
    assert count == 1


def test_stale_jobs_are_failed_and_release_dedup_key(monkeypatch) -> None:
    monkeypatch.setattr(job_store, "_executor", _Executor())
    monkeypatch.setattr(job_store, "_capacity", _Capacity())
    job_id = job_store.create_analysis_job(_analysis_request())
    stale = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE analysis_jobs
            SET status = 'running', heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (stale, stale, job_id),
        )

    assert job_store.cleanup_stale_analysis_jobs(stale_seconds=60) == 1
    job = job_store.get_job(job_id)
    assert job is not None
    assert job["status"] == "failed"
    with _connect() as connection:
        active = connection.execute(
            "SELECT active_dedup_key FROM analysis_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()["active_dedup_key"]
    assert active is None


def test_queue_full_fails_before_job_row_is_inserted(monkeypatch) -> None:
    monkeypatch.setattr(job_store, "_executor", _Executor())
    monkeypatch.setattr(job_store, "_capacity", _Capacity(allowed=False))

    with pytest.raises(JobQueueFull):
        job_store.create_analysis_job(_analysis_request())

    with _connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS value FROM analysis_jobs"
        ).fetchone()["value"]
    assert count == 0
