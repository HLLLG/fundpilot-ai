from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# CI runs pytest with ``apps/api`` as cwd while the operational migration
# scripts intentionally live at the repository root. Make that root explicit
# before importing the scripts so local and CI collection use the same files.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from app.db_migrations import run_migrations
from scripts.backfill_decision_events_v2 import backfill_database
from app.services.decision_repository import put_decision_event
from scripts.migrate_sqlite_to_mysql import (
    TABLES,
    ImmutableMigrationConflict,
    MigrationError,
    main as migration_main,
    migrate_connections,
    plan_sqlite_source,
)


def _initialise_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE reports (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            userId INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE fund_discovery_reports (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            payload TEXT NOT NULL,
            userId INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    run_migrations(connection)
    connection.commit()
    connection.close()


def _daily_payload(report_id: str, created_at: str, *, formal_v2: bool = False) -> dict:
    payload = {
        "id": report_id,
        "created_at": created_at,
        "provider": "historical-provider",
        "holdings": [
            {
                "fund_code": "000001",
                "fund_name": "历史持仓基金",
                "holding_amount": 1234.5,
                "return_percent": 3.2,
            }
        ],
        "analysis_facts": {
            "portfolio": {"round_trip_fee_percent": 9.9},
            "benchmark_specs": {
                "000001": {
                    "status": "complete",
                    "benchmark_name": "不应套用的当前基准",
                }
            },
            "holdings": [
                {
                    "fund_code": "000001",
                    "fund_name": "历史持仓基金",
                    "holding_amount": 1234.5,
                }
            ],
        },
        "fund_recommendations": [
            {
                "fund_code": "000001",
                "fund_name": "历史持仓基金",
                "action": "分批加仓",
                "amount_yuan": 200,
            }
        ],
    }
    if formal_v2:
        payload["decision_contract"] = {
            "schema_version": "decision_contract.v1",
            "persistence": "persisted",
        }
    return payload


def _discovery_payload(report_id: str, created_at: str) -> dict:
    return {
        "id": report_id,
        "created_at": created_at,
        "provider": "historical-provider",
        "discovery_facts": {
            "profile": {"round_trip_fee_percent": 8.8},
            "benchmark_specs": {
                "000002": {
                    "status": "complete",
                    "benchmark_name": "不应套用的当前基准",
                }
            },
        },
        "recommendations": [
            {
                "fund_code": "000002",
                "fund_name": "历史荐基",
                "action": "建议买入",
                "suggested_amount_yuan": 500,
            }
        ],
    }


def _insert_reports(path: Path) -> tuple[int, int, dict[tuple[str, str], str]]:
    base = datetime(2025, 1, 1, 2, tzinfo=timezone.utc)
    daily_count = 52  # deliberately exceeds the old list_reports LIMIT 50
    discovery_count = 31  # deliberately exceeds the old discovery LIMIT 30
    original_payloads: dict[tuple[str, str], str] = {}
    connection = sqlite3.connect(path)
    for index in range(daily_count):
        # The first two are on one China-local date; the second is the latest.
        day_offset = max(0, index - 1)
        created = base + timedelta(days=day_offset, minutes=index)
        report_id = f"daily-{index:03d}"
        raw = json.dumps(
            _daily_payload(report_id, created.isoformat()), ensure_ascii=False
        )
        connection.execute(
            "INSERT INTO reports (id, created_at, payload, userId) VALUES (?, ?, ?, 1)",
            (report_id, created.isoformat(), raw),
        )
        original_payloads[("reports", report_id)] = raw

    formal_created = base + timedelta(days=100)
    formal_id = "daily-formal-v2"
    formal_raw = json.dumps(
        _daily_payload(formal_id, formal_created.isoformat(), formal_v2=True),
        ensure_ascii=False,
    )
    connection.execute(
        "INSERT INTO reports (id, created_at, payload, userId) VALUES (?, ?, ?, 1)",
        (formal_id, formal_created.isoformat(), formal_raw),
    )
    original_payloads[("reports", formal_id)] = formal_raw

    for index in range(discovery_count):
        created = base + timedelta(days=200 + index)
        report_id = f"discovery-{index:03d}"
        raw = json.dumps(
            _discovery_payload(report_id, created.isoformat()), ensure_ascii=False
        )
        connection.execute(
            """
            INSERT INTO fund_discovery_reports (id, created_at, payload, userId)
            VALUES (?, ?, ?, 1)
            """,
            (report_id, created.isoformat(), raw),
        )
        original_payloads[("fund_discovery_reports", report_id)] = raw
    connection.commit()
    connection.close()
    return daily_count, discovery_count, original_payloads


def test_backfill_is_dry_run_by_default_full_scan_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    _initialise_database(path)
    daily_count, discovery_count, original_payloads = _insert_reports(path)

    dry_run = backfill_database(path, batch_size=7)

    assert dry_run["mode"] == "dry-run"
    assert dry_run["daily_reports_scanned"] == daily_count + 1
    assert dry_run["discovery_reports_scanned"] == discovery_count
    assert dry_run["reports_skipped_v2"] == 1
    assert dry_run["events_planned"] == daily_count + discovery_count
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 0
    assert (
        connection.execute("SELECT COUNT(*) FROM decision_portfolio_snapshots").fetchone()[0]
        == 0
    )
    connection.close()

    applied = backfill_database(path, apply=True, batch_size=7)

    assert applied["errors"] == []
    assert applied["events_inserted"] == daily_count + discovery_count
    assert applied["snapshots_inserted"] == daily_count
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    events = connection.execute(
        "SELECT * FROM decision_events ORDER BY source_type, source_report_id"
    ).fetchall()
    assert len(events) == daily_count + discovery_count
    assert all(row["is_backfilled"] == 1 for row in events)
    assert all(row["metric_eligible"] == 0 for row in events)
    assert connection.execute("SELECT COUNT(*) FROM outcome_observations").fetchone()[0] == 0

    older = json.loads(
        connection.execute(
            "SELECT payload FROM decision_events WHERE source_report_id = 'daily-000'"
        ).fetchone()[0]
    )
    latest = json.loads(
        connection.execute(
            "SELECT payload FROM decision_events WHERE source_report_id = 'daily-001'"
        ).fetchone()[0]
    )
    assert older["audit_status"] == "superseded_same_day"
    assert older["audit_eligible"] is False
    assert latest["audit_status"] == "legacy_backfilled"
    assert latest["audit_eligible"] is True

    # Historical facts intentionally contain tempting current-looking values;
    # the backfill must still freeze both contracts as unavailable.
    assert latest["benchmark"]["status"] == "unavailable"
    assert latest["benchmark"]["components"] == []
    assert latest["benchmark_mapping_id"] is None
    assert latest["fee_policy"]["round_trip_fee_percent"] is None
    assert latest["fee_policy"]["fee_source"] == "unavailable"

    snapshot = json.loads(
        connection.execute(
            "SELECT payload FROM decision_portfolio_snapshots LIMIT 1"
        ).fetchone()[0]
    )
    assert snapshot["authoritative"] is False
    assert snapshot["position_complete"] is False
    assert snapshot["source_paths"] == [
        "report.holdings",
        "analysis_facts.holdings",
    ]

    discovery_event = connection.execute(
        """
        SELECT portfolio_snapshot_id, payload FROM decision_events
        WHERE source_report_id = 'discovery-000'
        """
    ).fetchone()
    assert discovery_event["portfolio_snapshot_id"] is None
    assert json.loads(discovery_event["payload"])["position_truth_status"] == "unknown"

    # Source report JSON is byte-for-byte unchanged.
    for (table, report_id), original in original_payloads.items():
        current = connection.execute(
            f"SELECT payload FROM {table} WHERE id = ?", (report_id,)
        ).fetchone()[0]
        assert current == original
    connection.close()

    repeated = backfill_database(path, apply=True, batch_size=5)
    assert repeated["errors"] == []
    assert repeated["events_inserted"] == 0
    assert repeated["snapshots_inserted"] == 0
    assert repeated["events_existing"] == daily_count + discovery_count
    assert repeated["snapshots_existing"] == daily_count

    connection = sqlite3.connect(path)
    assert (
        connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0]
        == daily_count + discovery_count
    )
    connection.close()


def test_discovery_snapshot_is_only_built_from_embedded_historical_positions(
    tmp_path: Path,
) -> None:
    path = tmp_path / "discovery-position.db"
    _initialise_database(path)
    created = "2025-02-03T01:00:00+00:00"
    without_positions = _discovery_payload("no-position", created)
    with_positions = _discovery_payload("with-position", created)
    with_positions["discovery_facts"]["portfolio_position_snapshot"] = {
        "positions": [
            {
                "fund_code": "000003",
                "fund_name": "当时持仓",
                "settled_shares": 88,
                "market_value_cny": 99,
            }
        ]
    }
    connection = sqlite3.connect(path)
    for payload in (without_positions, with_positions):
        connection.execute(
            """
            INSERT INTO fund_discovery_reports (id, created_at, payload, userId)
            VALUES (?, ?, ?, 1)
            """,
            (payload["id"], created, json.dumps(payload, ensure_ascii=False)),
        )
    connection.commit()
    connection.close()

    result = backfill_database(path, apply=True, batch_size=1)

    assert result["snapshots_inserted"] == 1
    connection = sqlite3.connect(path)
    rows = dict(
        connection.execute(
            "SELECT source_report_id, portfolio_snapshot_id FROM decision_events"
        ).fetchall()
    )
    assert rows["no-position"] is None
    assert rows["with-position"] is not None
    payload = json.loads(
        connection.execute(
            "SELECT payload FROM decision_portfolio_snapshots"
        ).fetchone()[0]
    )
    assert payload["authoritative"] is False
    assert payload["position_complete"] is False
    connection.close()


def test_mysql_migration_list_includes_decision_v2_and_transaction_truth_tables() -> None:
    table_columns = {table: columns for table, columns in TABLES}
    assert {
        "fund_discovery_reports",
        "decision_portfolio_snapshots",
        "decision_events",
        "outcome_observations",
        "outcome_observation_revisions",
        "fund_benchmark_mappings",
        "portfolio_ledger_events",
        "portfolio_ledger_heads",
        "fund_primary_sectors",
        "fund_primary_sectors_global",
        "discovery_jobs",
        "discovery_chat_messages",
        "factor_ic_snapshots",
    }.issubset(table_columns)
    assert {
        "confirmed_shares",
        "fee_yuan",
        "shares_source",
        "in_progress",
        "confirmed_at",
    }.issubset(table_columns["fund_transactions"])


def test_backfill_v2_presence_is_scoped_by_daily_or_discovery(tmp_path: Path) -> None:
    path = tmp_path / "cross-surface.db"
    _initialise_database(path)
    created = "2025-03-01T02:00:00+00:00"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    put_decision_event(
        user_id=1,
        event={
            "schema_version": "decision_event.v2",
            "event_id": "daily:shared-report:0:000001",
            "event_type": "daily_fund_decision",
            "source_type": "daily",
            "source_report_id": "shared-report",
            "decision_at": created,
            "decision_date": "2025-03-01",
            "fund_code": "000001",
            "fund_name": "日报基金",
            "final_action": "观察",
            "action_category": "observation",
            "is_backfilled": False,
            "metric_eligible": True,
        },
        connection=connection,
    )
    discovery = _discovery_payload("shared-report", created)
    connection.execute(
        """
        INSERT INTO fund_discovery_reports (id, created_at, payload, userId)
        VALUES (?, ?, ?, 1)
        """,
        ("shared-report", created, json.dumps(discovery, ensure_ascii=False)),
    )
    connection.commit()
    connection.close()

    result = backfill_database(path, apply=True, batch_size=1)

    assert result["reports_skipped_v2"] == 0
    assert result["events_inserted"] == 1
    connection = sqlite3.connect(path)
    assert (
        connection.execute(
            """
            SELECT COUNT(*) FROM decision_events
            WHERE source_type = 'discovery' AND source_report_id = 'shared-report'
            """
        ).fetchone()[0]
        == 1
    )
    connection.close()


def test_backfill_dry_run_preflights_immutable_hash_conflicts(tmp_path: Path) -> None:
    path = tmp_path / "backfill-conflict.db"
    _initialise_database(path)
    created = "2025-04-01T02:00:00+00:00"
    original = _daily_payload("conflicting-report", created)
    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO reports (id, created_at, payload, userId) VALUES (?, ?, ?, 1)",
        ("conflicting-report", created, json.dumps(original, ensure_ascii=False)),
    )
    connection.commit()
    connection.close()
    first = backfill_database(path, apply=True)
    assert first["events_inserted"] == 1

    changed = _daily_payload("conflicting-report", created)
    changed["fund_recommendations"][0]["action"] = "清仓"
    connection = sqlite3.connect(path)
    before = connection.execute(
        "SELECT content_hash FROM decision_events"
    ).fetchone()[0]
    connection.execute(
        "UPDATE reports SET payload = ? WHERE id = ?",
        (json.dumps(changed, ensure_ascii=False), "conflicting-report"),
    )
    connection.commit()
    connection.close()

    dry_run = backfill_database(path)

    assert dry_run["mode"] == "dry-run"
    assert dry_run["immutable_conflicts"] == 1
    assert dry_run["errors"][0]["error"] == "immutable_content_hash_conflict"
    applied = backfill_database(path, apply=True)
    assert applied["immutable_conflicts"] == 1
    assert applied["events_inserted"] == 0
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM decision_events").fetchone()[0] == 1
    assert connection.execute("SELECT content_hash FROM decision_events").fetchone()[0] == before
    connection.close()


class _SqliteAsMysqlCursor:
    def __init__(self, connection: sqlite3.Connection, statements: list[str]) -> None:
        self._connection = connection
        self._cursor: sqlite3.Cursor | None = None
        self._statements = statements

    def execute(self, sql: str, params: Sequence[Any] = ()) -> "_SqliteAsMysqlCursor":
        self._statements.append(sql)
        self._cursor = self._connection.execute(sql.replace("%s", "?"), tuple(params))
        return self

    def fetchone(self) -> object:
        assert self._cursor is not None
        return self._cursor.fetchone()


class _SqliteAsMysqlConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.statements: list[str] = []

    def cursor(self) -> _SqliteAsMysqlCursor:
        return _SqliteAsMysqlCursor(self.connection, self.statements)


def _create_table_for_columns(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
) -> None:
    connection.execute(
        f"CREATE TABLE {table} ({', '.join(f'{column} TEXT' for column in columns)})"
    )


def test_mysql_migration_defaults_new_transaction_truth_columns_and_batches(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "old-transactions.db"
    source = sqlite3.connect(source_path)
    source.executescript(
        """
        CREATE TABLE fund_transactions (
            id TEXT PRIMARY KEY,
            userId INTEGER NOT NULL,
            fund_code TEXT,
            fund_name TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount_yuan REAL NOT NULL,
            trade_time TEXT NOT NULL,
            confirm_date TEXT NOT NULL,
            status TEXT NOT NULL,
            shares_delta REAL,
            nav_on_confirm REAL,
            dedup_key TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO fund_transactions VALUES (
            'tx-1', 1, '000001', '旧交易', 'buy', 100,
            '2025-01-01 10:00:00', '2025-01-02', 'confirmed',
            50, 2, 'dedup-1', '2025-01-01T02:00:00+00:00'
        );
        INSERT INTO fund_transactions VALUES (
            'tx-2', 1, '000002', '旧交易2', 'sell', 80,
            '2025-01-03 10:00:00', '2025-01-06', 'confirmed',
            -40, 2, 'dedup-2', '2025-01-03T02:00:00+00:00'
        );
        """
    )
    source.commit()

    plan = plan_sqlite_source(source_path, batch_size=1)
    transaction_plan = next(
        item for item in plan["tables"] if item["table"] == "fund_transactions"
    )
    assert transaction_plan["rows"] == 2
    assert set(transaction_plan["defaulted_columns"]) == {
        "confirmed_shares",
        "fee_yuan",
        "shares_source",
        "in_progress",
        "confirmed_at",
    }

    destination_raw = sqlite3.connect(":memory:")
    transaction_columns = dict(TABLES)["fund_transactions"]
    _create_table_for_columns(destination_raw, "fund_transactions", transaction_columns)
    destination = _SqliteAsMysqlConnection(destination_raw)

    summary = migrate_connections(source, destination, batch_size=1)

    assert summary["rows_written"] == 2
    rows = destination_raw.execute(
        """
        SELECT confirmed_shares, fee_yuan, shares_source, in_progress, confirmed_at
        FROM fund_transactions ORDER BY id
        """
    ).fetchall()
    assert rows == [(None, None, None, "0", None), (None, None, None, "0", None)]
    source.close()
    destination_raw.close()


def test_mysql_migration_never_replaces_immutable_evidence(tmp_path: Path) -> None:
    source = sqlite3.connect(tmp_path / "immutable-source.db")
    columns = dict(TABLES)["decision_events"]
    _create_table_for_columns(source, "decision_events", columns)
    values = {
        column: f"value-{column}" for column in columns
    } | {
        "userId": "1",
        "event_id": "event-1",
        "content_hash": "hash-1",
    }
    source.execute(
        f"INSERT INTO decision_events ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )
    source.commit()

    destination_raw = sqlite3.connect(":memory:")
    _create_table_for_columns(destination_raw, "decision_events", columns)
    destination = _SqliteAsMysqlConnection(destination_raw)
    first = migrate_connections(source, destination, batch_size=1)
    assert first["rows_written"] == 1
    assert not any(
        statement.startswith("REPLACE INTO decision_events")
        for statement in destination.statements
    )

    second = migrate_connections(source, destination, batch_size=1)
    assert second["rows_written"] == 0
    assert second["rows_skipped_identical"] == 1
    destination_raw.execute(
        "UPDATE decision_events SET content_hash = 'different' WHERE event_id = 'event-1'"
    )
    destination_raw.commit()

    try:
        migrate_connections(source, destination, batch_size=1)
    except ImmutableMigrationConflict as exc:
        assert "decision_events" in str(exc)
        assert "event-1" in str(exc)
    else:  # pragma: no cover - regression guard
        raise AssertionError("immutable conflict must stop migration")
    assert (
        destination_raw.execute(
            "SELECT content_hash FROM decision_events WHERE event_id = 'event-1'"
        ).fetchone()[0]
        == "different"
    )
    source.close()
    destination_raw.close()


def test_outcome_migration_same_hash_still_requires_current_revision_projection(
    tmp_path: Path,
) -> None:
    columns = dict(TABLES)["outcome_observations"]
    source = sqlite3.connect(tmp_path / "outcome-source.db")
    _create_table_for_columns(source, "outcome_observations", columns)
    values = {column: f"value-{column}" for column in columns} | {
        "userId": "1",
        "observation_id": "obs-1",
        "decision_event_id": "event-1",
        "horizon_trading_days": "5",
        "target_date": None,
        "status": "pending",
        "is_terminal": "0",
        "revision_no": "3",
        "content_hash": "same-substantive-hash",
    }
    source.execute(
        f"INSERT INTO outcome_observations ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )
    source.commit()

    destination_raw = sqlite3.connect(":memory:")
    _create_table_for_columns(destination_raw, "outcome_observations", columns)
    destination_values = {**values, "revision_no": "1"}
    destination_raw.execute(
        f"INSERT INTO outcome_observations ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(destination_values[column] for column in columns),
    )
    destination_raw.commit()
    destination = _SqliteAsMysqlConnection(destination_raw)

    try:
        migrate_connections(source, destination, batch_size=1)
    except ImmutableMigrationConflict as exc:
        assert "outcome_observations" in str(exc)
        assert "obs-1" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("current outcome revision drift must not be skipped")

    source.close()
    destination_raw.close()


def test_mysql_migration_cli_is_dry_run_without_apply(
    tmp_path: Path, capsys: Any
) -> None:
    path = tmp_path / "dry-run.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE news_cache (cache_key TEXT, payload TEXT, updated_at TEXT)"
    )
    connection.execute(
        "INSERT INTO news_cache VALUES ('key', '{}', '2025-01-01T00:00:00+00:00')"
    )
    connection.commit()
    connection.close()

    exit_code = migration_main(
        [
            "--sqlite",
            str(path),
            "--mysql-url",
            "mysql://not-used:not-used@127.0.0.1/not-used",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry-run"
    assert output["rows_planned"] == 1
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM news_cache").fetchone()[0] == 1
    connection.close()


def test_mysql_migration_reports_missing_required_columns_instead_of_skipping(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bad-schema.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE reports (id TEXT, created_at TEXT, payload TEXT)"
    )
    connection.commit()
    connection.close()

    try:
        plan_sqlite_source(path)
    except MigrationError as exc:
        assert "reports" in str(exc)
        assert "userId" in str(exc)
        assert "未静默跳过" in str(exc)
    else:  # pragma: no cover - regression guard
        raise AssertionError("missing required columns must fail the dry-run")
