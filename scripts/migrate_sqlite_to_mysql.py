#!/usr/bin/env python3
"""将本地 SQLite 数据迁移到 MySQL（CloudBase MySQL 或自建）。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

TABLES = [
    ("users", ["id", "userRole", "username", "userAccount", "passwordHash", "bio", "avatarUrl", "cloudbaseUid", "createdAt", "updatedAt", "isDeleted", "deletedAt"]),
    ("reports", ["id", "created_at", "payload", "userId"]),
    ("fund_discovery_reports", ["id", "created_at", "payload", "userId"]),
    ("fund_profiles", ["userId", "fund_code", "fund_name", "payload", "updated_at"]),
    ("portfolio_state", ["userId", "payload", "updated_at"]),
    ("portfolio_daily_snapshots", ["userId", "snapshot_date", "payload", "updated_at"]),
    ("portfolio_intraday_curves", ["userId", "trade_date", "payload", "updated_at"]),
    ("investor_profile_state", ["userId", "payload", "updated_at"]),
    ("analysis_prompt_state", ["userId", "role_prompt", "updated_at"]),
    ("discovery_prompt_state", ["userId", "role_prompt", "updated_at"]),
    ("sector_mappings", ["userId", "sector_label", "source_type", "source_code", "source_name", "confidence", "updated_at"]),
    (
        "fund_primary_sectors",
        [
            "userId", "fund_code", "sector_name", "intraday_index_name",
            "source", "confidence", "detail", "updated_at",
        ],
    ),
    (
        "fund_primary_sectors_global",
        [
            "fund_code", "sector_name", "intraday_index_name", "source",
            "confidence", "detail", "resolved_at",
        ],
    ),
    ("ocr_text_cache", ["userId", "cache_key", "raw_text", "updated_at"]),
    ("report_chat_messages", ["id", "report_id", "role", "content", "created_at"]),
    ("analysis_jobs", ["id", "status", "request_payload", "report_id", "error", "stage", "stage_label", "userId", "created_at", "updated_at"]),
    ("discovery_jobs", ["id", "status", "request_payload", "discovery_report_id", "error", "stage", "stage_label", "userId", "created_at", "updated_at"]),
    ("discovery_chat_messages", ["id", "discovery_report_id", "role", "content", "created_at"]),
    ("swing_alert_fired", ["userId", "trade_date", "alert_key", "payload", "fired_at"]),
    ("refresh_tokens", ["id", "userId", "tokenHash", "expiresAt", "createdAt", "revokedAt"]),
    ("news_cache", ["cache_key", "payload", "updated_at"]),
    ("sector_spot_cache", ["cache_key", "payload", "updated_at"]),
    (
        "factor_ic_snapshots",
        [
            "snapshot_id", "schema_version", "run_date", "generated_at",
            "published_at", "source_commit", "source_run_id", "payload",
        ],
    ),
    (
        "fund_transactions",
        [
            "id", "userId", "fund_code", "fund_name", "direction", "amount_yuan",
            "trade_time", "confirm_date", "status", "shares_delta", "nav_on_confirm",
            "confirmed_shares", "fee_yuan", "shares_source", "in_progress",
            "confirmed_at", "dedup_key", "created_at",
        ],
    ),
    (
        "decision_portfolio_snapshots",
        [
            "userId", "snapshot_id", "account_id", "snapshot_at", "snapshot_date",
            "source_type", "truth_status", "ledger_version", "cash_yuan",
            "total_market_value_yuan", "content_hash", "payload", "created_at",
        ],
    ),
    (
        "decision_events",
        [
            "userId", "event_id", "schema_version", "event_type", "source_type",
            "source_report_id", "decision_at", "decision_date", "fund_code",
            "fund_name", "proposed_action", "final_action", "action_category",
            "eligible", "amount_yuan", "portfolio_snapshot_id",
            "benchmark_mapping_id", "fee_model", "is_backfilled", "metric_eligible",
            "content_hash", "payload", "created_at",
        ],
    ),
    (
        "outcome_observations",
        [
            "userId", "observation_id", "decision_event_id",
            "horizon_trading_days", "target_date", "status", "is_terminal",
            "revision_no", "observed_at", "finalized_at", "content_hash", "payload",
            "created_at", "updated_at",
        ],
    ),
    (
        "outcome_observation_revisions",
        [
            "userId", "observation_id", "revision_no", "status", "is_terminal",
            "observed_at", "content_hash", "payload", "created_at",
        ],
    ),
    (
        "fund_benchmark_mappings",
        [
            "userId", "mapping_id", "fund_code", "benchmark_kind", "completeness",
            "benchmark_name", "benchmark_code", "valid_from", "valid_to", "source",
            "source_ref", "content_hash", "payload", "created_at",
        ],
    ),
    (
        "portfolio_ledger_events",
        [
            "event_revision_id", "logical_event_id", "userId", "account_id",
            "revision_no", "event_type", "fund_code", "effective_at", "recorded_at",
            "status", "source", "source_ref", "event_hash", "previous_hash",
            "payload_hash", "payload", "created_at",
        ],
    ),
    (
        "portfolio_ledger_heads",
        ["userId", "account_id", "revision", "chain_hash", "updated_at"],
    ),
]


class MigrationError(RuntimeError):
    """A migration cannot continue without risking silent data loss."""


class ImmutableMigrationConflict(MigrationError):
    """The destination reused an immutable identity for different content."""


# Columns introduced after the original transaction table shipped.  A legacy
# source row has no confirmed execution truth for them, so migration preserves
# that uncertainty instead of skipping the entire table.
SOURCE_COLUMN_DEFAULTS: dict[str, dict[str, str]] = {
    "fund_transactions": {
        "confirmed_shares": "NULL",
        "fee_yuan": "NULL",
        "shares_source": "NULL",
        "in_progress": "0",
        "confirmed_at": "NULL",
    }
}


# These V2 records are evidence, not mutable cache rows.  Migration may insert
# an absent identity or skip an identical one, but must never replace it.
# Comparison fields intentionally exclude storage timestamps.
IMMUTABLE_TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "decision_portfolio_snapshots": (("userId", "snapshot_id"), ("content_hash",)),
    "decision_events": (("userId", "event_id"), ("content_hash",)),
    "outcome_observations": (
        ("userId", "observation_id"),
        (
            "content_hash",
            "decision_event_id",
            "horizon_trading_days",
            "target_date",
            "status",
            "is_terminal",
            "revision_no",
        ),
    ),
    "outcome_observation_revisions": (
        ("userId", "observation_id", "revision_no"),
        ("content_hash",),
    ),
    "fund_benchmark_mappings": (("userId", "mapping_id"), ("content_hash",)),
    "portfolio_ledger_events": (("event_revision_id",), ("event_hash",)),
    "portfolio_ledger_heads": (
        ("userId", "account_id"),
        ("revision", "chain_hash"),
    ),
}


def parse_mysql_url(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/").lstrip("/"),
        "charset": "utf8mb4",
    }


def _source_table_columns(connection: sqlite3.Connection, table: str) -> set[str] | None:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    if not rows:
        return None
    return {str(row[1]) for row in rows}


def _source_projection(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: Sequence[str],
) -> tuple[str, list[str]] | None:
    available = _source_table_columns(connection, table)
    if available is None:
        return None
    defaults = SOURCE_COLUMN_DEFAULTS.get(table, {})
    missing_required = [
        column for column in columns if column not in available and column not in defaults
    ]
    if missing_required:
        raise MigrationError(
            f"源表 {table} 缺少必要列: {', '.join(missing_required)}；迁移已停止，未静默跳过"
        )
    defaulted = [column for column in columns if column not in available]
    expressions = [
        column if column in available else f"{defaults[column]} AS {column}"
        for column in columns
    ]
    return ", ".join(expressions), defaulted


def _row_values(row: object, columns: Sequence[str]) -> tuple[Any, ...]:
    if isinstance(row, Mapping):
        return tuple(row[column] for column in columns)
    if isinstance(row, sqlite3.Row):
        return tuple(row[column] for column in columns)
    return tuple(row)  # type: ignore[arg-type]


def _iter_source_batches(
    connection: sqlite3.Connection,
    *,
    table: str,
    projection: str,
    batch_size: int,
) -> Any:
    cursor = connection.execute(f"SELECT {projection} FROM {table}")
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        yield rows


def plan_sqlite_source(
    sqlite_path: str | Path,
    *,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Read-only source validation used by the default CLI mode."""

    path = Path(sqlite_path)
    if not path.exists():
        raise FileNotFoundError(path)
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    summary: dict[str, Any] = {
        "mode": "dry-run",
        "batch_size": batch_size,
        "rows_planned": 0,
        "tables": [],
    }
    connection = sqlite3.connect(path)
    try:
        for table, columns in TABLES:
            projection = _source_projection(
                connection, table=table, columns=columns
            )
            if projection is None:
                summary["tables"].append(
                    {"table": table, "status": "source_table_absent", "rows": 0}
                )
                continue
            select_list, defaulted = projection
            # Compile the compatibility projection as part of dry-run so a bad
            # mapping fails before any destination connection is opened.
            connection.execute(
                f"SELECT {select_list} FROM {table} LIMIT 0"
            ).fetchall()
            row_count = int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            summary["rows_planned"] += row_count
            summary["tables"].append(
                {
                    "table": table,
                    "status": "ready",
                    "rows": row_count,
                    "defaulted_columns": defaulted,
                    "write_policy": (
                        "insert_only_compare"
                        if table in IMMUTABLE_TABLES
                        else "replace_legacy_state"
                    ),
                }
            )
        return summary
    finally:
        connection.close()


def _destination_row_values(
    row: object, *, columns: Sequence[str]
) -> tuple[Any, ...]:
    if isinstance(row, Mapping):
        return tuple(row[column] for column in columns)
    return tuple(row)  # type: ignore[arg-type]


def _insert_immutable_row(
    cursor: Any,
    *,
    table: str,
    columns: Sequence[str],
    values: tuple[Any, ...],
) -> str:
    identity_columns, comparison_columns = IMMUTABLE_TABLES[table]
    value_by_column = dict(zip(columns, values, strict=True))
    where = " AND ".join(f"{column} = %s" for column in identity_columns)
    cursor.execute(
        f"SELECT {', '.join(comparison_columns)} FROM {table} WHERE {where}",
        tuple(value_by_column[column] for column in identity_columns),
    )
    existing = cursor.fetchone()
    expected = tuple(value_by_column[column] for column in comparison_columns)
    if existing is not None:
        actual = _destination_row_values(existing, columns=comparison_columns)
        if actual == expected:
            return "identical"
        identity = ", ".join(
            f"{column}={value_by_column[column]!r}" for column in identity_columns
        )
        raise ImmutableMigrationConflict(
            f"不可变表 {table} 的标识已存在但内容不同: {identity}"
        )

    placeholders = ", ".join("%s" for _ in columns)
    cursor.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    return "inserted"


def migrate_connections(
    source: sqlite3.Connection,
    destination: Any,
    *,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Migrate an already-open SQLite source into an initialized MySQL target."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    source.row_factory = sqlite3.Row
    cursor = destination.cursor()
    summary: dict[str, Any] = {
        "mode": "apply",
        "batch_size": batch_size,
        "rows_scanned": 0,
        "rows_written": 0,
        "rows_skipped_identical": 0,
        "tables": [],
    }
    for table, columns in TABLES:
        projection = _source_projection(source, table=table, columns=columns)
        if projection is None:
            summary["tables"].append(
                {"table": table, "status": "source_table_absent", "rows": 0}
            )
            continue
        select_list, defaulted = projection
        table_scanned = 0
        table_written = 0
        table_identical = 0
        placeholders = ", ".join("%s" for _ in columns)
        replace_sql = (
            f"REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        )
        for rows in _iter_source_batches(
            source,
            table=table,
            projection=select_list,
            batch_size=batch_size,
        ):
            for row in rows:
                values = _row_values(row, columns)
                table_scanned += 1
                if table in IMMUTABLE_TABLES:
                    outcome = _insert_immutable_row(
                        cursor,
                        table=table,
                        columns=columns,
                        values=values,
                    )
                    if outcome == "identical":
                        table_identical += 1
                    else:
                        table_written += 1
                else:
                    cursor.execute(replace_sql, values)
                    table_written += 1
        summary["rows_scanned"] += table_scanned
        summary["rows_written"] += table_written
        summary["rows_skipped_identical"] += table_identical
        summary["tables"].append(
            {
                "table": table,
                "status": "migrated",
                "rows": table_scanned,
                "written": table_written,
                "skipped_identical": table_identical,
                "defaulted_columns": defaulted,
                "write_policy": (
                    "insert_only_compare"
                    if table in IMMUTABLE_TABLES
                    else "replace_legacy_state"
                ),
            }
        )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite → MySQL 数据迁移（默认只读 dry-run）"
    )
    parser.add_argument(
        "--sqlite", default=str(ROOT / "data" / "app.db"), help="SQLite 源文件"
    )
    parser.add_argument(
        "--mysql-url", required=True, help="mysql://user:pass@host:3306/dbname"
    )
    parser.add_argument(
        "--batch-size", type=int, default=500, help="源库分批读取行数（默认 500）"
    )
    parser.add_argument(
        "--apply", action="store_true", help="实际写入；不传时只检查源库迁移计划"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    sqlite_path = Path(args.sqlite)
    try:
        plan = plan_sqlite_source(sqlite_path, batch_size=args.batch_size)
    except (FileNotFoundError, ValueError, sqlite3.Error, MigrationError) as exc:
        print(f"迁移检查失败: {exc}", file=sys.stderr)
        return 2
    if not args.apply:
        print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    import pymysql
    from app.mysql_bootstrap import ensure_mysql_schema

    source = sqlite3.connect(sqlite_path)
    destination = None
    try:
        destination = pymysql.connect(**parse_mysql_url(args.mysql_url))
        ensure_mysql_schema(destination)
        summary = migrate_connections(
            source,
            destination,
            batch_size=args.batch_size,
        )
        destination.commit()
    except Exception as exc:  # noqa: BLE001 - rollback before reporting any migration fault
        if destination is not None:
            destination.rollback()
        print(f"迁移失败，数据事务已回滚: {exc}", file=sys.stderr)
        return 2
    finally:
        source.close()
        if destination is not None:
            destination.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
