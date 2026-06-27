from __future__ import annotations

import sqlite3

from app.services import job_status_service


def test_job_status_returns_transient_running_when_database_is_temporarily_unavailable(monkeypatch):
    monkeypatch.setattr(job_status_service, "get_request_user_id", lambda: 1)

    def _raise_locked():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(job_status_service, "_connect", _raise_locked)

    payload = job_status_service.resolve_job_status_single_connection("job-1")

    assert payload["id"] == "job-1"
    assert payload["status"] == "running"
    assert payload["transient_unavailable"] is True
