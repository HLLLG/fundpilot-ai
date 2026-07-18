from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app import database
from app.db_migrations import SCHEMA_VERSION, run_migrations
from app.mysql_bootstrap import MYSQL_SCHEMA_VERSION, ensure_mysql_schema
from app.services.fund_holdings_snapshot import (
    build_fund_holdings_snapshot,
    compute_fund_holdings_snapshot_hash,
    validate_fund_holdings_snapshot_hash,
)


def _snapshot(
    *,
    snapshot_hash: str = "2" * 64,
    source_hash: str = "1" * 64,
    available_at: str | None = "2026-01-20T09:00:00+08:00",
    status: str = "qualified",
    qualified: bool = True,
    fund_code: str = "000001",
) -> dict[str, Any]:
    payload = {
        "schema_version": "fund_holdings_snapshot.v1",
        "fund_code": fund_code,
        "fund_master_key": fund_code,
        "report_period": "2025-Q4",
        "as_of_date": "2025-12-31",
        "available_at": available_at,
        "first_observed_at": None,
        "source": {"provider": "fixture"},
        "status": status,
        "qualified": qualified,
        "reason_codes": [] if qualified else ["publication_time_unknown"],
        "source_validation": {
            "schema_version": "fund_holdings_source_validation.v1",
            "status": status,
            "qualified": qualified,
            "valid_snapshot": qualified,
            "available_at_known": available_at is not None,
            "disclosure_scope_identified": qualified,
            "weight_validation_passed": qualified,
            "reason_codes": [] if qualified else ["publication_time_unknown"],
        },
        "qualification": {
            "status": status,
            "qualified": qualified,
            "valid_snapshot": qualified,
            "pit_eligible": qualified,
            "disclosure_scope_identified": qualified,
            "weight_validation_passed": qualified,
            "disclosed_overlap_lower_bound_eligible": qualified,
            "exact_full_portfolio_overlap_eligible": False,
            "current_holdings_inference_eligible": False,
            "reason_codes": [] if qualified else ["publication_time_unknown"],
        },
        "scope": {
            "kind": "top10",
            "completeness": "partial",
            "basis": "quarterly_disclosure",
        },
        "coverage": {
            "disclosed_holding_count": 2,
            "weight_sum_percent": 18.0,
            "is_complete_portfolio": False,
            "coverage_ratio": None,
        },
        "holdings": [
            {"security_code": "600000", "weight_percent": 10.0},
            {"security_code": "600001", "weight_percent": 8.0},
        ],
        "source_hash": source_hash,
        "snapshot_hash": snapshot_hash,
        "fixture_revision": snapshot_hash,
        "family_hint": {
            "status": "unverified_hint",
            "verified": False,
            "hard_merge_applied": False,
            "hinted_master_key": "family-unsafe",
            "related_codes": [fund_code],
        },
    }
    # Every persisted fixture must exercise the same content-addressed trust
    # boundary as production snapshots, including unavailable audit records.
    payload["snapshot_hash"] = compute_fund_holdings_snapshot_hash(payload)
    return payload


def test_sqlite_migration_creates_append_only_holdings_schema() -> None:
    assert SCHEMA_VERSION == 17
    connection = sqlite3.connect(":memory:")
    run_migrations(connection)

    columns = {
        row[1]: row for row in connection.execute("PRAGMA table_info(fund_holdings_snapshots)")
    }
    assert set(columns) == {
        "id",
        "fund_master_key",
        "fund_code",
        "report_period",
        "as_of_date",
        "available_at",
        "first_observed_at",
        "source_hash",
        "snapshot_hash",
        "schema_version",
        "status",
        "payload_json",
        "created_at",
    }
    assert columns["available_at"][3] == 0
    assert columns["first_observed_at"][3] == 1
    indexes = {
        row[1] for row in connection.execute("PRAGMA index_list(fund_holdings_snapshots)")
    }
    assert "idx_fund_holdings_snapshots_code_pit" in indexes
    assert "idx_fund_holdings_snapshots_master_pit" in indexes
    assert "uq_fund_holdings_snapshot_hash" in indexes


def test_mysql_bootstrap_has_equivalent_holdings_schema() -> None:
    statements: list[str] = []

    class Cursor:
        def execute(self, statement: str) -> None:
            statements.append(statement)

    class Connection:
        def cursor(self) -> Cursor:
            return Cursor()

        def commit(self) -> None:
            pass

    ensure_mysql_schema(Connection())

    assert MYSQL_SCHEMA_VERSION == 17
    ddl = next(
        statement
        for statement in statements
        if "CREATE TABLE IF NOT EXISTS fund_holdings_snapshots" in statement
    )
    assert "available_at VARCHAR(64) NULL" in ddl
    assert "UNIQUE KEY uq_fund_holdings_snapshot_hash (snapshot_hash)" in ddl
    assert "idx_fund_holdings_snapshots_code_pit" in ddl
    assert "idx_fund_holdings_snapshots_master_pit" in ddl


def test_save_is_idempotent_and_new_hash_appends_revision() -> None:
    first = database.save_fund_holdings_snapshot(_snapshot())
    duplicate = database.save_fund_holdings_snapshot(_snapshot())
    revised = database.save_fund_holdings_snapshot(
        _snapshot(snapshot_hash="4" * 64, source_hash="3" * 64)
    )

    assert first["stored"] is True and first["duplicate"] is False
    assert duplicate["stored"] is False and duplicate["duplicate"] is True
    assert duplicate["id"] == first["id"]
    assert (
        duplicate["record"]["first_observed_at"]
        == first["record"]["first_observed_at"]
    )
    assert revised["stored"] is True and revised["id"] != first["id"]
    records = database.list_fund_holdings_snapshots(fund_code="000001")
    assert {record["snapshot_hash"] for record in records} == {
        first["record"]["snapshot_hash"],
        revised["record"]["snapshot_hash"],
    }


def test_pit_query_filters_future_unknown_and_status_without_losing_audit() -> None:
    now = datetime.now(timezone.utc)
    eligible = database.save_fund_holdings_snapshot(
        _snapshot(available_at=(now - timedelta(days=10)).isoformat())
    )
    unavailable = database.save_fund_holdings_snapshot(
        _snapshot(
            snapshot_hash="3" * 64,
            source_hash="3" * 64,
            available_at=(now - timedelta(days=5)).isoformat(),
            status="unavailable",
            qualified=False,
        )
    )
    future = database.save_fund_holdings_snapshot(
        _snapshot(
            snapshot_hash="4" * 64,
            source_hash="4" * 64,
            available_at=(now + timedelta(days=10)).isoformat(),
            status="unavailable",
            qualified=False,
        )
    )
    invalid = database.save_fund_holdings_snapshot(
        _snapshot(
            snapshot_hash="5" * 64,
            source_hash="5" * 64,
            available_at=None,
            status="invalid",
            qualified=False,
        )
    )

    decision_at = now + timedelta(days=1)
    qualified = database.get_latest_fund_holdings_snapshot(
        fund_code="000001", decision_at=decision_at
    )
    all_statuses = database.get_latest_fund_holdings_snapshot(
        fund_master_key="000001",
        decision_at=decision_at,
        qualified_only=False,
    )
    eligible_hash = eligible["record"]["snapshot_hash"]
    unavailable_hash = unavailable["record"]["snapshot_hash"]
    future_hash = future["record"]["snapshot_hash"]
    invalid_hash = invalid["record"]["snapshot_hash"]
    assert qualified is not None and qualified["snapshot_hash"] == eligible_hash
    assert all_statuses is not None and all_statuses["snapshot_hash"] == unavailable_hash

    pit_rows = database.list_fund_holdings_snapshots(
        fund_code="000001", decision_at=decision_at
    )
    assert {row["snapshot_hash"] for row in pit_rows} == {
        eligible_hash,
        unavailable_hash,
    }
    audit_rows = database.list_fund_holdings_snapshots(fund_code="000001")
    assert {row["snapshot_hash"] for row in audit_rows} == {
        eligible_hash,
        unavailable_hash,
        future_hash,
        invalid_hash,
    }


def test_pit_query_never_backfills_a_late_first_observation() -> None:
    stored = database.save_fund_holdings_snapshot(
        _snapshot(available_at="2026-01-10T00:00:00+00:00")
    )
    first_observed = datetime.fromisoformat(stored["record"]["first_observed_at"])
    historical_decision = first_observed - timedelta(microseconds=1)

    assert historical_decision > datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert (
        database.get_latest_fund_holdings_snapshot(
            fund_code="000001",
            decision_at=historical_decision,
        )
        is None
    )


def test_qualified_reads_recheck_payload_instead_of_trusting_indexed_status() -> None:
    stored = database.save_fund_holdings_snapshot(_snapshot())
    decision_at = (
        datetime.fromisoformat(stored["record"]["first_observed_at"])
        + timedelta(seconds=1)
    )
    tampered = dict(stored["snapshot"])
    tampered["qualified"] = False
    with database._connect() as connection:
        connection.execute(
            "UPDATE fund_holdings_snapshots SET payload_json = ? WHERE id = ?",
            (json.dumps(tampered), stored["id"]),
        )

    assert (
        database.get_latest_fund_holdings_snapshot(
            fund_code="000001",
            decision_at=decision_at,
        )
        is None
    )
    audited = database.get_latest_fund_holdings_snapshot(
        fund_code="000001",
        decision_at=decision_at,
        qualified_only=False,
    )
    assert audited is not None and audited["status"] == "qualified"


def test_qualified_reads_reject_content_address_tampering() -> None:
    stored = database.save_fund_holdings_snapshot(_snapshot())
    decision_at = (
        datetime.fromisoformat(stored["record"]["first_observed_at"])
        + timedelta(seconds=1)
    )
    tampered = dict(stored["snapshot"])
    tampered["holdings"] = [
        {"security_code": "600000", "weight_percent": 99.0},
    ]
    with database._connect() as connection:
        connection.execute(
            "UPDATE fund_holdings_snapshots SET payload_json = ? WHERE id = ?",
            (json.dumps(tampered), stored["id"]),
        )

    assert (
        database.get_latest_fund_holdings_snapshot(
            fund_code="000001",
            decision_at=decision_at,
        )
        is None
    )
    audited = database.get_latest_fund_holdings_snapshot(
        fund_code="000001",
        decision_at=decision_at,
        qualified_only=False,
    )
    assert (
        audited is not None
        and audited["snapshot_hash"] == stored["record"]["snapshot_hash"]
    )


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (
            lambda: database.save_fund_holdings_snapshot(
                _snapshot(available_at="2026-01-20T09:00:00")
            ),
            "available_at must be timezone-aware",
        ),
        (
            lambda: database.get_latest_fund_holdings_snapshot(
                fund_code="000001", decision_at="2026-01-21T00:00:00"
            ),
            "decision_at must be timezone-aware",
        ),
    ],
)
def test_storage_requires_aware_clocks(operation, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        operation()


def test_unverified_family_hint_never_changes_master_key() -> None:
    payload = _snapshot()
    payload.pop("fund_master_key")
    payload["snapshot_hash"] = compute_fund_holdings_snapshot_hash(payload)
    stored = database.save_fund_holdings_snapshot(payload)
    assert stored["record"]["fund_master_key"] == "000001"

    unsafe = _snapshot(snapshot_hash="9" * 64)
    unsafe["fund_master_key"] = "family-unsafe"
    unsafe["snapshot_hash"] = compute_fund_holdings_snapshot_hash(unsafe)
    with pytest.raises(ValueError, match="verified hard-merge"):
        database.save_fund_holdings_snapshot(unsafe)


def test_store_rejects_invalid_content_address_before_hash_can_be_reserved() -> None:
    valid = _snapshot()
    tampered = dict(valid)
    tampered["holdings"] = [
        {"security_code": "600000", "weight_percent": 99.0},
    ]

    with pytest.raises(ValueError, match="canonical snapshot content"):
        database.save_fund_holdings_snapshot(tampered)

    stored = database.save_fund_holdings_snapshot(valid)
    assert stored["stored"] is True


def test_store_preserves_content_addressed_timestamp_representation() -> None:
    snapshot = build_fund_holdings_snapshot(
        [
            {
                "基金代码": "000001",
                "季度": "2026年1季度",
                "股票代码": "600000",
                "股票名称": "浦发银行",
                "占净值比例": 10.0,
            }
        ],
        [
            {
                "基金代码": "000001",
                "公告标题": "甲基金2026年第1季度报告",
                "公告日期": "2026-04-20",
                "报告ID": "report-1",
            }
        ],
        fund_code="000001",
        decision_at="2026-04-22T09:00:00+08:00",
    )
    assert snapshot["status"] == "qualified"
    assert snapshot["available_at"].endswith("+08:00")

    stored = database.save_fund_holdings_snapshot(snapshot)

    assert stored["record"]["available_at"].endswith("+00:00")
    assert stored["snapshot"]["available_at"] == snapshot["available_at"]
    assert validate_fund_holdings_snapshot_hash(stored["snapshot"]) is True
