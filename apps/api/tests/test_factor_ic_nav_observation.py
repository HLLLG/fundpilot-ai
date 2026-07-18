from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.db_connect import DbConnection
from app.db_migrations import run_migrations
from app.main import app
from app.services.factor_ic_nav_observation import (
    AVAILABILITY_BASIS,
    NAV_OBSERVATION_BATCH_SCHEMA_VERSION,
    REVISION_POLICY,
    FactorIcNavObservationPublishRequest,
    FactorIcNavObservationStorageUnavailable,
    build_nav_observation_batch_from_universe,
    publish_nav_observation_batch,
    read_nav_observation_history,
    read_nav_observation_status,
    validate_nav_observation_publish_request,
)


OBSERVED_AT = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def _initialize(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        run_migrations(connection)
        connection.commit()
    finally:
        connection.close()


def _factory(path: Path):
    def connect() -> DbConnection:
        raw = sqlite3.connect(path)
        raw.row_factory = sqlite3.Row
        return DbConnection(raw, "sqlite")

    return connect


def _request(
    *,
    observed_at: datetime = OBSERVED_AT,
    unit_nav: float = 1.2345,
    source_run_id: str = "run-1",
) -> FactorIcNavObservationPublishRequest:
    return FactorIcNavObservationPublishRequest.model_validate(
        {
            "schema_version": NAV_OBSERVATION_BATCH_SCHEMA_VERSION,
            "observed_at": observed_at.isoformat(),
            "availability_basis": AVAILABILITY_BASIS,
            "source_commit": "a" * 40,
            "source_run_id": source_run_id,
            "source_member_count": 1,
            "missing_observation_count": 0,
            "observations": [
                {
                    "fund_code": "000001",
                    "nav_date": "2026-07-17",
                    "source": "eastmoney.open_fund_rankhandler",
                    "unit_nav": unit_nav,
                    "cumulative_nav": None,
                    "daily_growth_percent": 0.42,
                }
            ],
        }
    )


def test_build_batch_reuses_universe_capture_and_conserves_missing_count() -> None:
    members = []
    for index in range(5):
        metadata = {
            "nav_date": "2026-07-17",
            "latest_nav": str(1.0 + index / 10),
            "daily_growth_percent": "0.1",
        }
        if index == 4:
            metadata = {}
        members.append(
            {
                "fund_code": f"{index + 1:06d}",
                "metadata": metadata,
            }
        )
    payload = {
        "snapshot": {"captured_at": OBSERVED_AT.isoformat()},
        "members": members,
        "source_commit": "b" * 40,
        "source_run_id": "capture-1",
    }

    batch = build_nav_observation_batch_from_universe(payload)

    assert batch["source_member_count"] == 5
    assert batch["missing_observation_count"] == 1
    assert len(batch["observations"]) == 4
    assert batch["observations"][0]["fund_code"] == "000001"
    assert batch["observations"][0]["unit_nav"] == 1.0


def test_publish_is_idempotent_and_preserves_true_first_observed_time(
    tmp_path: Path,
) -> None:
    path = tmp_path / "nav.db"
    _initialize(path)
    factory = _factory(path)

    first = publish_nav_observation_batch(
        _request(),
        connection_factory=factory,
        now=OBSERVED_AT + timedelta(minutes=1),
    )
    repeated = publish_nav_observation_batch(
        _request(observed_at=OBSERVED_AT + timedelta(hours=2), source_run_id="run-2"),
        connection_factory=factory,
        now=OBSERVED_AT + timedelta(hours=2, minutes=1),
    )

    assert first["created_count"] == 1
    assert first["duplicate_count"] == 0
    assert repeated["created_count"] == 0
    assert repeated["duplicate_count"] == 1
    history = read_nav_observation_history(
        fund_codes=["000001"],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 18),
        as_of=OBSERVED_AT + timedelta(hours=3),
        connection_factory=factory,
    )
    assert history["observation_count"] == 1
    assert history["observations"][0]["first_observed_at"] == OBSERVED_AT.isoformat()
    assert history["observations"][0]["source_commit"] == "a" * 40
    assert history["observations"][0]["source_run_id"] == "run-1"
    assert history["availability_basis"] == AVAILABILITY_BASIS
    assert history["revision_policy"] == REVISION_POLICY


def test_corrected_value_appends_revision_but_history_keeps_first_value(
    tmp_path: Path,
) -> None:
    path = tmp_path / "revision.db"
    _initialize(path)
    factory = _factory(path)
    publish_nav_observation_batch(
        _request(unit_nav=1.20),
        connection_factory=factory,
        now=OBSERVED_AT + timedelta(minutes=1),
    )
    corrected_at = OBSERVED_AT + timedelta(hours=4)
    publish_nav_observation_batch(
        _request(
            observed_at=corrected_at,
            unit_nav=1.25,
            source_run_id="run-correction",
        ),
        connection_factory=factory,
        now=corrected_at + timedelta(minutes=1),
    )

    before_correction = read_nav_observation_history(
        fund_codes=["000001"],
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
        as_of=OBSERVED_AT + timedelta(hours=1),
        connection_factory=factory,
    )
    after_correction = read_nav_observation_history(
        fund_codes=["000001"],
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
        as_of=corrected_at + timedelta(hours=1),
        connection_factory=factory,
    )
    status = read_nav_observation_status(connection_factory=factory)

    assert before_correction["observations"][0]["unit_nav"] == 1.20
    assert after_correction["observations"][0]["unit_nav"] == 1.20
    assert after_correction["revision_rows_excluded"] == 1
    assert status["observation_count"] == 2
    assert status["revision_count"] == 1
    assert status["capture_run_count"] == 2
    assert status["full_model_ready"] is False
    assert status["automatic_promotion_allowed"] is False


def test_payload_tampering_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "tampered.db"
    _initialize(path)
    factory = _factory(path)
    publish_nav_observation_batch(
        _request(),
        connection_factory=factory,
        now=OBSERVED_AT + timedelta(minutes=1),
    )
    connection = sqlite3.connect(path)
    try:
        connection.execute("DROP TRIGGER trg_factor_ic_nav_observation_no_update")
        raw_payload = connection.execute(
            "SELECT payload FROM factor_ic_nav_observations"
        ).fetchone()[0]
        payload = json.loads(raw_payload)
        payload["schema_version"] = "factor_ic_nav_observation.v0"
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        content_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        connection.execute(
            """
            UPDATE factor_ic_nav_observations
            SET schema_version = ?, payload = ?, content_hash = ?
            """,
            (payload["schema_version"], serialized, content_hash),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        FactorIcNavObservationStorageUnavailable,
        match="版本|可得性",
    ):
        read_nav_observation_history(
            fund_codes=["000001"],
            start_date=date(2026, 7, 17),
            end_date=date(2026, 7, 17),
            as_of=OBSERVED_AT + timedelta(hours=1),
            connection_factory=factory,
        )


@pytest.mark.parametrize("statement", ["UPDATE", "DELETE"])
def test_database_triggers_physically_block_mutation(
    tmp_path: Path,
    statement: str,
) -> None:
    path = tmp_path / f"blocked-{statement}.db"
    _initialize(path)
    factory = _factory(path)
    publish_nav_observation_batch(
        _request(),
        connection_factory=factory,
        now=OBSERVED_AT + timedelta(minutes=1),
    )
    connection = sqlite3.connect(path)
    try:
        sql = (
            "UPDATE factor_ic_nav_observations SET unit_nav = 2.0"
            if statement == "UPDATE"
            else "DELETE FROM factor_ic_nav_observations"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(sql)
    finally:
        connection.close()


def test_validation_rejects_backdated_or_future_capture() -> None:
    payload = _request().model_dump(mode="json")
    with pytest.raises(ValueError, match="24"):
        validate_nav_observation_publish_request(
            payload,
            now=OBSERVED_AT + timedelta(days=2),
        )
    payload["observed_at"] = (OBSERVED_AT + timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError):
        validate_nav_observation_publish_request(payload, now=OBSERVED_AT)


def test_internal_endpoints_require_token_and_remain_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FUND_AI_FACTOR_IC_PUBLISH_TOKEN", "pytest-factor-token")
    refresh_settings()
    client = TestClient(app)

    unauthorized = client.post(
        "/api/internal/factor-ic-nav-observations",
        json=_request().model_dump(mode="json"),
    )
    assert unauthorized.status_code == 401
    schema = client.get("/openapi.json").json()
    assert "/api/internal/factor-ic-nav-observations" not in schema["paths"]
    assert "/api/internal/factor-ic-nav-observations/query" not in schema["paths"]
