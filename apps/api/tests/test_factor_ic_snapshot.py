from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.services.factor_ic_snapshot import (
    FactorIcNewerSnapshotExists,
    FactorIcStorageUnavailable,
    publish_factor_ic_snapshot,
    read_latest_database_snapshot,
    validate_publish_request,
)


def valid_payload(generated_at: str | None = None) -> dict:
    generated_at = generated_at or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()
    factors = [
        {
            "factor": name,
            "n_periods": 34,
            "mean_ic": 0.01,
            "ic_std": 0.2,
            "icir": 0.05,
            "t_stat": 0.3,
            "positive_ratio": 0.5,
            "significant": False,
        }
        for name in ("momentum", "risk_adjusted", "drawdown", "composite")
    ]
    return {
        "summary": {
            "schema_version": 1,
            "run_date": generated_at[:10],
            "generated_at": generated_at,
            "params": {
                "universe_size": 300,
                "universe_mode": "sampled",
                "sample_pool_size": 500,
                "nav_days": 750,
                "rebalance_step": 21,
                "forward_days": 20,
                "factor_lookback": 250,
            },
            "available": True,
            "universe_size": 300,
            "rebalance_count": 35,
            "forward_days": 20,
            "factors": factors,
        },
        "source_commit": "a" * 40,
        "source_run_id": "12345",
    }


def _use_sqlite(monkeypatch, tmp_path, name: str) -> None:
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / name))
    from app.config import refresh_settings

    refresh_settings()


def test_publish_is_append_only_idempotent_and_reads_latest(
    tmp_path,
    monkeypatch,
) -> None:
    _use_sqlite(monkeypatch, tmp_path, "factor-ic.db")
    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    first = validate_publish_request(
        valid_payload("2026-07-10T08:00:00+00:00"),
        now=now,
    )
    created = publish_factor_ic_snapshot(first, now=now)
    duplicate = publish_factor_ic_snapshot(first, now=now)
    latest = read_latest_database_snapshot()

    assert created["created"] is True
    assert duplicate == {"created": False, "snapshot_id": created["snapshot_id"]}
    assert latest is not None
    assert latest["snapshot_id"] == created["snapshot_id"]
    assert latest["summary"]["universe_size"] == 300
    assert latest["source_commit"] == "a" * 40


def test_newer_snapshot_appends_and_older_snapshot_is_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    _use_sqlite(monkeypatch, tmp_path, "factor-ic-old.db")
    now = datetime(2026, 7, 10, 10, tzinfo=timezone.utc)
    older = validate_publish_request(
        valid_payload("2026-07-10T08:00:00+00:00"),
        now=now,
    )
    newer_payload = valid_payload("2026-07-10T09:00:00+00:00")
    newer_payload["source_commit"] = "b" * 40
    newer = validate_publish_request(newer_payload, now=now)

    publish_factor_ic_snapshot(older, now=now)
    publish_factor_ic_snapshot(newer, now=now)
    latest = read_latest_database_snapshot()
    assert latest is not None
    assert latest["source_commit"] == "b" * 40

    stale_payload = valid_payload("2026-07-10T07:00:00+00:00")
    stale_payload["source_commit"] = "c" * 40
    stale = validate_publish_request(stale_payload, now=now)
    with pytest.raises(FactorIcNewerSnapshotExists):
        publish_factor_ic_snapshot(stale, now=now)

    duplicate_old = publish_factor_ic_snapshot(older, now=now)
    assert duplicate_old["created"] is False


def test_read_latest_database_snapshot_returns_none_for_empty_table(
    tmp_path,
    monkeypatch,
) -> None:
    _use_sqlite(monkeypatch, tmp_path, "factor-ic-empty.db")
    from app.database import _connect

    with _connect():
        pass
    assert read_latest_database_snapshot() is None


def test_non_significant_result_is_publishable() -> None:
    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    payload = valid_payload("2026-07-10T08:00:00+00:00")
    assert all(not row["significant"] for row in payload["summary"]["factors"])
    validate_publish_request(payload, now=now)


@pytest.mark.parametrize("universe_size", [239])
def test_effective_universe_below_threshold_is_rejected(universe_size: int) -> None:
    payload = valid_payload("2026-07-10T08:00:00+00:00")
    payload["summary"]["universe_size"] = universe_size
    with pytest.raises(ValueError, match="有效基金数不足"):
        validate_publish_request(
            payload,
            now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
        )


def test_effective_universe_boundary_240_is_valid() -> None:
    payload = valid_payload("2026-07-10T08:00:00+00:00")
    payload["summary"]["universe_size"] = 240
    validate_publish_request(
        payload,
        now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
    )


def test_rebalance_and_factor_period_boundaries() -> None:
    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    valid = valid_payload("2026-07-10T08:00:00+00:00")
    valid["summary"]["rebalance_count"] = 12
    for row in valid["summary"]["factors"]:
        row["n_periods"] = 12
    validate_publish_request(valid, now=now)

    few_rebalances = deepcopy(valid)
    few_rebalances["summary"]["rebalance_count"] = 11
    with pytest.raises(ValueError, match="回测期数不足"):
        validate_publish_request(few_rebalances, now=now)

    few_factor_periods = deepcopy(valid)
    few_factor_periods["summary"]["factors"][0]["n_periods"] = 11
    with pytest.raises(ValueError, match="有效期数不足"):
        validate_publish_request(few_factor_periods, now=now)


def test_duplicate_or_missing_factors_are_rejected() -> None:
    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    duplicate = valid_payload("2026-07-10T08:00:00+00:00")
    duplicate["summary"]["factors"][-1]["factor"] = "momentum"
    with pytest.raises(ValueError, match="四个因子"):
        validate_publish_request(duplicate, now=now)

    missing = valid_payload("2026-07-10T08:00:00+00:00")
    missing["summary"]["factors"].pop()
    with pytest.raises(ValueError, match="四个因子"):
        validate_publish_request(missing, now=now)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mean_ic", math.nan),
        ("ic_std", math.inf),
        ("icir", -math.inf),
        ("t_stat", math.nan),
        ("positive_ratio", 1.01),
    ],
)
def test_non_finite_or_out_of_range_statistics_are_rejected(
    field: str,
    value: float,
) -> None:
    payload = valid_payload("2026-07-10T08:00:00+00:00")
    payload["summary"]["factors"][0][field] = value
    with pytest.raises(ValueError):
        validate_publish_request(
            payload,
            now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
        )


def test_mysql_configuration_rejects_fallback_sqlite(monkeypatch) -> None:
    from app.config import refresh_settings

    class FallbackConnection:
        dialect = "sqlite"

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            return None

    monkeypatch.setenv(
        "FUND_AI_DATABASE_URL",
        "mysql://user:password@db.example.test:3306/fundpilot",
    )
    refresh_settings()
    now = datetime(2026, 7, 10, 9, tzinfo=timezone.utc)
    request = validate_publish_request(
        valid_payload("2026-07-10T08:00:00+00:00"),
        now=now,
    )
    with pytest.raises(
        FactorIcStorageUnavailable,
        match="拒绝回落到本地 SQLite",
    ):
        publish_factor_ic_snapshot(
            request,
            connection_factory=FallbackConnection,
            now=now,
        )


def test_mysql_bootstrap_contains_factor_ic_snapshot_schema() -> None:
    from app.mysql_bootstrap import ensure_mysql_schema

    statements: list[str] = []

    class Cursor:
        def execute(self, statement: str) -> None:
            statements.append(statement)

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

        def commit(self) -> None:
            return None

    ensure_mysql_schema(Connection())
    ddl = "\n".join(statements)
    assert "CREATE TABLE IF NOT EXISTS factor_ic_snapshots" in ddl
    assert "snapshot_id VARCHAR(64) PRIMARY KEY" in ddl
    assert "INDEX idx_factor_ic_generated (generated_at)" in ddl
