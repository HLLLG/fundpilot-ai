from __future__ import annotations

import sqlite3

import pytest

from app.db_migrations import SCHEMA_VERSION, run_migrations
from app.mysql_bootstrap import MYSQL_SCHEMA_VERSION, ensure_mysql_schema
from app.services.decision_repository import (
    ImmutableRecordConflict,
    LedgerHeadConflict,
    ObservationFinalizedConflict,
    append_portfolio_ledger_event,
    canonical_hash,
    canonical_json,
    compare_and_set_portfolio_ledger_head,
    get_decision_event,
    get_decision_portfolio_snapshot,
    get_effective_fund_benchmark_mapping,
    get_portfolio_ledger_head,
    list_decision_events,
    list_outcome_observation_revisions,
    list_outcome_observations,
    list_portfolio_ledger_events,
    put_decision_event,
    put_decision_portfolio_snapshot,
    put_fund_benchmark_mapping,
    upsert_outcome_observation,
)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    run_migrations(connection)
    return connection


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def test_schema_v9_to_v10_and_transaction_truth_columns() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO schema_meta (id, version) VALUES (1, 9)")
    connection.execute("CREATE TABLE fund_transactions (id TEXT PRIMARY KEY)")

    run_migrations(connection)

    assert SCHEMA_VERSION == 10
    assert connection.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()[0] == 10
    expected = {
        "decision_portfolio_snapshots",
        "decision_events",
        "outcome_observations",
        "outcome_observation_revisions",
        "fund_benchmark_mappings",
        "portfolio_ledger_events",
        "portfolio_ledger_heads",
    }
    assert expected <= _table_names(connection)
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(fund_transactions)").fetchall()
    }
    assert {
        "confirmed_shares",
        "fee_yuan",
        "shares_source",
        "in_progress",
        "confirmed_at",
    } <= columns


def test_current_version_repairs_missing_v10_tables_and_columns() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE schema_meta (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
    )
    connection.execute("INSERT INTO schema_meta (id, version) VALUES (1, 10)")
    connection.execute("CREATE TABLE fund_transactions (id TEXT PRIMARY KEY)")

    run_migrations(connection)
    connection.execute("DROP TABLE outcome_observation_revisions")
    run_migrations(connection)

    assert "outcome_observation_revisions" in _table_names(connection)
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(fund_transactions)").fetchall()
    }
    assert "confirmed_shares" in columns


def test_canonical_json_and_hash_are_order_independent() -> None:
    left = {"基金": "测试", "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "基金": "测试"}

    assert canonical_json(left) == canonical_json(right)
    assert canonical_hash(left) == canonical_hash(right)


def test_repository_owned_sqlite_connection_uses_full_application_bootstrap(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "owned-repository.db"
    monkeypatch.setenv("FUND_AI_DB_PATH", str(path))
    snapshot = {
        "snapshot_id": "owned-snapshot",
        "snapshot_at": "2026-07-12T01:00:00+00:00",
        "source_type": "estimated_profile",
        "truth_status": "estimated",
        "positions": [],
    }

    put_decision_portfolio_snapshot(user_id=1, snapshot=snapshot)

    raw = sqlite3.connect(path)
    try:
        tables = _table_names(raw)
        assert {"reports", "fund_transactions", "decision_portfolio_snapshots"} <= tables
        assert raw.execute(
            "SELECT COUNT(*) FROM decision_portfolio_snapshots"
        ).fetchone()[0] == 1
    finally:
        raw.close()


def test_snapshot_and_event_are_immutable_and_isolated_by_user() -> None:
    connection = _connection()
    snapshot = {
        "snapshot_id": "snap-1",
        "snapshot_at": "2026-07-12T01:00:00+00:00",
        "snapshot_date": "2026-07-12",
        "source_type": "confirmed_ledger",
        "truth_status": "confirmed",
        "positions": [{"fund_code": "000001", "shares": 100}],
    }
    first = put_decision_portfolio_snapshot(
        user_id=1, snapshot=snapshot, connection=connection
    )
    retry = put_decision_portfolio_snapshot(
        user_id=1, snapshot=dict(reversed(list(snapshot.items()))), connection=connection
    )
    other_user = put_decision_portfolio_snapshot(
        user_id=2, snapshot=snapshot, connection=connection
    )
    assert first["content_hash"] == retry["content_hash"]
    assert other_user["userId"] == 2
    assert get_decision_portfolio_snapshot(
        user_id=1, snapshot_id="snap-1", connection=connection
    )["payload"]["positions"][0]["shares"] == 100
    assert first["payload"]["account_id"] == "default"
    assert first["payload"]["ledger_version"] is None

    changed_snapshot = {**snapshot, "positions": [{"fund_code": "000001", "shares": 99}]}
    with pytest.raises(ImmutableRecordConflict):
        put_decision_portfolio_snapshot(
            user_id=1, snapshot=changed_snapshot, connection=connection
        )

    event = {
        "schema_version": "decision_event.v1",
        "event_id": "daily:report-1:000001",
        "event_type": "fund_daily_decision",
        "report_id": "report-1",
        "decision_at": "2026-07-12T01:02:00+00:00",
        "fund_code": "000001",
        "fund_name": "测试基金",
        "action": "持有",
        "action_category": "observation",
        "eligible": False,
        "portfolio_snapshot_id": "snap-1",
    }
    put_decision_event(user_id=1, event=event, connection=connection)
    put_decision_event(user_id=2, event=event, connection=connection)
    assert get_decision_event(
        user_id=1, event_id=event["event_id"], connection=connection
    )["final_action"] == "持有"
    stored_event = get_decision_event(
        user_id=1, event_id=event["event_id"], connection=connection
    )
    assert stored_event["payload"]["source_type"] == "daily"
    assert stored_event["payload"]["final_action"] == "持有"
    assert stored_event["payload"]["metric_eligible"] is True
    assert len(list_decision_events(user_id=1, connection=connection)) == 1
    assert len(list_decision_events(user_id=2, connection=connection)) == 1

    with pytest.raises(ImmutableRecordConflict):
        put_decision_event(
            user_id=1, event={**event, "action": "买入"}, connection=connection
        )


def test_pending_observation_revises_then_terminal_evidence_locks() -> None:
    connection = _connection()
    pending = {
        "schema_version": "outcome_observation.v1",
        "observation_id": "daily:r1:000001:T+5",
        "event_id": "daily:r1:000001",
        "horizon_trading_days": 5,
        "target_date": "2026-07-20",
        "observation_at": "2026-07-12T01:00:00+00:00",
        "status": "pending",
        "return_percent": None,
    }
    first = upsert_outcome_observation(
        user_id=7, observation=pending, connection=connection
    )
    same_evidence_new_check_time = upsert_outcome_observation(
        user_id=7,
        observation={**pending, "observation_at": "2026-07-13T01:00:00+00:00"},
        connection=connection,
    )
    assert first["revision_no"] == same_evidence_new_check_time["revision_no"] == 1

    immature = {**pending, "status": "immature", "observed_forward_trading_days": 3}
    second = upsert_outcome_observation(
        user_id=7, observation=immature, connection=connection
    )
    assert second["revision_no"] == 2
    assert second["is_terminal"] is False

    mature = {
        **pending,
        "status": "mature",
        "target_nav_date": "2026-07-20",
        "return_percent": 1.25,
    }
    final = upsert_outcome_observation(
        user_id=7, observation=mature, connection=connection
    )
    assert final["revision_no"] == 3
    assert final["is_terminal"] is True
    retry = upsert_outcome_observation(
        user_id=7,
        observation={**mature, "observation_at": "2026-07-21T01:00:00+00:00"},
        connection=connection,
    )
    assert retry["revision_no"] == 3

    with pytest.raises(ObservationFinalizedConflict):
        upsert_outcome_observation(
            user_id=7,
            observation={**mature, "return_percent": 1.3},
            connection=connection,
        )
    assert len(
        list_outcome_observation_revisions(
            user_id=7, observation_id=pending["observation_id"], connection=connection
        )
    ) == 3
    assert len(
        list_outcome_observations(
            user_id=8, decision_event_id=pending["event_id"], connection=connection
        )
    ) == 0


def test_effective_benchmark_prefers_complete_official_contract() -> None:
    connection = _connection()
    proxy = {
        "mapping_id": "map-proxy",
        "fund_code": "000001",
        "benchmark_kind": "category_proxy",
        "completeness": "proxy",
        "benchmark_name": "混合基金类别代理",
        "valid_from": "2020-01-01",
        "source": "internal_mapping",
    }
    official = {
        "mapping_id": "map-official",
        "fund_code": "000001",
        "benchmark_kind": "official_contract",
        "completeness": "complete",
        "benchmark_name": "沪深300×60%+中债综合×40%",
        "valid_from": "2025-01-01",
        "source": "fund_contract",
    }
    put_fund_benchmark_mapping(user_id=1, mapping=proxy, connection=connection)
    put_fund_benchmark_mapping(user_id=1, mapping=official, connection=connection)

    selected = get_effective_fund_benchmark_mapping(
        user_id=1,
        fund_code="000001",
        as_of_date="2026-07-12",
        connection=connection,
    )
    assert selected is not None
    assert selected["mapping_id"] == "map-official"
    assert (
        get_effective_fund_benchmark_mapping(
            user_id=2,
            fund_code="000001",
            as_of_date="2026-07-12",
            connection=connection,
        )
        is None
    )


def test_append_only_ledger_is_idempotent_hash_chained_and_known_at_filterable() -> None:
    connection = _connection()
    first_event = {
        "logical_event_id": "baseline:000001",
        "revision_no": 1,
        "event_type": "position_baseline",
        "fund_code": "000001",
        "effective_at": "2026-07-01T08:00:00+00:00",
        "recorded_at": "2026-07-12T01:00:00+00:00",
        "status": "confirmed",
        "source": "user_confirmed",
        "source_ref": "baseline-form-1",
        "payload": {"shares": 100, "cost_yuan": 125.5},
    }
    first = append_portfolio_ledger_event(
        user_id=3,
        event=first_event,
        expected_head_revision=0,
        expected_head_hash="",
        connection=connection,
    )
    retry = append_portfolio_ledger_event(
        user_id=3, event=first_event, connection=connection
    )
    assert retry["event_revision_id"] == first["event_revision_id"]
    head = get_portfolio_ledger_head(user_id=3, connection=connection)
    assert head["revision"] == 1
    assert head["chain_hash"] == first["event_hash"]

    second_event = {
        "logical_event_id": "trade:order-2",
        "revision_no": 1,
        "event_type": "buy_confirmed",
        "fund_code": "000001",
        "effective_at": "2026-07-13T08:00:00+00:00",
        "recorded_at": "2026-07-14T01:00:00+00:00",
        "status": "confirmed",
        "source": "manual_trade",
        "source_ref": "order-2",
        "previous_hash": first["event_hash"],
        "payload": {"shares_delta": 10, "fee_yuan": 1},
    }
    second = append_portfolio_ledger_event(
        user_id=3,
        event=second_event,
        expected_head_revision=1,
        expected_head_hash=first["event_hash"],
        connection=connection,
    )
    assert second["previous_hash"] == first["event_hash"]
    assert get_portfolio_ledger_head(user_id=3, connection=connection)["revision"] == 2
    assert len(list_portfolio_ledger_events(user_id=3, connection=connection)) == 2
    assert len(
        list_portfolio_ledger_events(
            user_id=3,
            recorded_at_lte="2026-07-13T23:59:59+00:00",
            connection=connection,
        )
    ) == 1
    assert len(list_portfolio_ledger_events(user_id=4, connection=connection)) == 0

    with pytest.raises(LedgerHeadConflict):
        append_portfolio_ledger_event(
            user_id=3,
            event={**second_event, "source_ref": "order-3", "logical_event_id": "trade:order-3"},
            expected_head_revision=1,
            connection=connection,
        )

    with pytest.raises(ValueError, match="recorded_at"):
        append_portfolio_ledger_event(
            user_id=3,
            event={
                key: value
                for key, value in first_event.items()
                if key != "recorded_at"
            }
            | {"logical_event_id": "missing-recorded-at"},
            connection=connection,
        )


def test_ledger_head_compare_and_set_primitive() -> None:
    connection = _connection()
    assert compare_and_set_portfolio_ledger_head(
        user_id=9,
        expected_revision=0,
        expected_chain_hash="",
        new_revision=1,
        new_chain_hash="abc",
        connection=connection,
    )
    assert not compare_and_set_portfolio_ledger_head(
        user_id=9,
        expected_revision=0,
        expected_chain_hash="",
        new_revision=1,
        new_chain_hash="other",
        connection=connection,
    )


def test_mysql_bootstrap_has_v10_durable_decision_and_ledger_ddl() -> None:
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
    assert MYSQL_SCHEMA_VERSION == 10
    for table in (
        "decision_portfolio_snapshots",
        "decision_events",
        "outcome_observations",
        "outcome_observation_revisions",
        "fund_benchmark_mappings",
        "portfolio_ledger_events",
        "portfolio_ledger_heads",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
    assert "ON DUPLICATE KEY UPDATE version" in ddl
    assert "confirmed_shares DOUBLE NULL" in ddl
    assert "ledger_version VARCHAR(128) NULL" in ddl
