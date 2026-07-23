#!/usr/bin/env python3
"""将本地 SQLite 数据迁移到 MySQL（CloudBase MySQL 或自建）。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

TABLES = [
    (
        "users",
        [
            "id", "userRole", "username", "userAccount", "passwordHash",
            "bio", "avatarUrl", "cloudbaseUid", "createdAt", "updatedAt",
            "isDeleted", "deletedAt", "authVersion", "lastLoginAt",
            "lastActiveAt", "passwordUpdatedAt",
        ],
    ),
    (
        "reports",
        ["id", "created_at", "payload", "summary_payload", "userId"],
    ),
    (
        "report_summaries",
        ["userId", "report_id", "created_at", "summary_payload"],
    ),
    (
        "fund_discovery_reports",
        ["id", "created_at", "payload", "summary_payload", "userId"],
    ),
    (
        "fund_discovery_report_summaries",
        ["userId", "report_id", "created_at", "summary_payload"],
    ),
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
    (
        "analysis_jobs",
        [
            "id", "status", "request_payload", "dedup_key",
            "active_dedup_key", "report_id", "error", "stage",
            "stage_label", "userId", "created_at", "updated_at",
            "heartbeat_at",
        ],
    ),
    (
        "discovery_jobs",
        [
            "id", "status", "request_payload", "dedup_key",
            "active_dedup_key", "discovery_report_id", "error", "stage",
            "stage_label", "userId", "created_at", "updated_at",
            "heartbeat_at",
        ],
    ),
    (
        "stream_sessions",
        [
            "session_id", "userId", "stage", "operator_notes",
            "created_at", "updated_at", "expires_at",
        ],
    ),
    ("discovery_chat_messages", ["id", "discovery_report_id", "role", "content", "created_at"]),
    ("swing_alert_fired", ["userId", "trade_date", "alert_key", "payload", "fired_at"]),
    ("refresh_tokens", ["id", "userId", "tokenHash", "expiresAt", "createdAt", "revokedAt"]),
    (
        "password_reset_tokens",
        [
            "id", "userId", "tokenHash", "expiresAt", "createdAt", "usedAt",
            "revokedAt", "createdByAdminId",
        ],
    ),
    (
        "admin_audit_events",
        [
            "eventId", "actorUserId", "targetUserId", "action", "reason",
            "beforeJson", "afterJson", "createdAt",
        ],
    ),
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
        "factor_ic_universe_snapshots",
        [
            "snapshot_id", "schema_version", "snapshot_date", "available_at",
            "captured_at", "published_at", "source", "source_share_count",
            "deduped_fund_count", "sampled_fund_count", "sample_target",
            "fund_type_count", "source_commit", "source_run_id", "content_hash",
            "payload",
        ],
    ),
    (
        "factor_ic_universe_members",
        [
            "snapshot_id", "fund_code", "fund_name", "fund_type", "share_class",
            "canonical_portfolio_key", "inception_date", "available_at",
            "source_rank", "content_hash", "payload", "created_at",
        ],
    ),
    (
        "factor_ic_nav_observations",
        [
            "observation_id", "schema_version", "fund_code", "nav_date",
            "source", "first_observed_at", "available_at",
            "availability_basis", "unit_nav", "cumulative_nav",
            "daily_growth_percent", "content_hash", "payload",
            "source_commit", "source_run_id", "created_at",
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
        "decision_quality_input_artifacts",
        [
            "userId", "artifact_id", "schema_version", "artifact_type",
            "artifact_schema_version", "logical_key", "source_type",
            "source_report_id", "decision_event_id", "decision_at",
            "available_at", "recorded_at", "store_authority",
            "audit_eligible", "content_hash", "payload", "created_at",
        ],
    ),
    (
        "decision_quality_artifact_receipts",
        [
            "userId", "artifact_id", "receipt_id", "schema_version",
            "receipt_policy", "artifact_type", "artifact_content_hash",
            "source_row_created_at", "source_visible_at", "store_authority",
            "content_hash", "payload", "created_at",
        ],
    ),
    (
        "decision_quality_provider_receipts",
        [
            "receipt_id", "schema_version", "provider", "operation",
            "capture_mode", "request_hash", "adapter_output_sha256",
            "adapter_output_bytes", "normalized_payload_hash",
            "origin_fetched_at", "completed_at", "content_hash", "payload",
            "created_at",
        ],
    ),
    (
        "decision_quality_evaluation_snapshots",
        [
            "userId", "snapshot_id", "schema_version", "evaluation_as_of",
            "evaluator_schema_version", "evaluator_version", "status",
            "evaluation_hash", "input_manifest_hash", "config_hash",
            "readiness_status", "human_review_status",
            "automatic_promotion_allowed", "store_authority",
            "audit_eligible", "content_hash", "payload", "created_at",
        ],
    ),
    (
        "decision_quality_contract_rollouts",
        [
            "contract_name", "schema_version", "contract_version",
            "required_from", "created_at", "hash_algorithm",
            "canonicalization", "marker_hash",
        ],
    ),
    (
        "prompt_shadow_runs",
        [
            "userId", "run_id", "schema_version", "policy_id", "policy_hash",
            "decision_at", "registration_artifact_id",
            "champion_attempt_artifact_id", "champion_output_artifact_id",
            "champion_report_id", "challenger_attempt_artifact_id",
            "challenger_output_artifact_id", "status", "state_version",
            "challenger_deadline_at", "lease_owner_hash", "lease_token_hash",
            "lease_acquired_at", "lease_expires_at",
            "champion_network_started_at", "challenger_network_started_at",
            "budget_scope_key", "budget_date_local", "budget_reserved_at",
            "terminal_reason", "created_at", "updated_at",
        ],
    ),
    (
        "prompt_shadow_budget_counters",
        [
            "scope_key", "budget_date_local", "schema_version", "policy_id",
            "policy_hash", "max_calls", "reserved_calls", "started_calls",
            "completed_calls", "failed_calls", "state_version", "created_at",
            "updated_at",
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
    "users": {
        "authVersion": "1",
        "lastLoginAt": "NULL",
        "lastActiveAt": "NULL",
        "passwordUpdatedAt": "NULL",
    },
    "fund_transactions": {
        "confirmed_shares": "NULL",
        "fee_yuan": "NULL",
        "shares_source": "NULL",
        "in_progress": "0",
        "confirmed_at": "NULL",
    },
    "reports": {"summary_payload": "NULL"},
    "fund_discovery_reports": {"summary_payload": "NULL"},
    "analysis_jobs": {
        "dedup_key": "NULL",
        "active_dedup_key": "NULL",
        "heartbeat_at": "updated_at",
    },
    "discovery_jobs": {
        "dedup_key": "NULL",
        "active_dedup_key": "NULL",
        "heartbeat_at": "updated_at",
    },
    "decision_quality_input_artifacts": {"logical_key": "NULL"},
}


# These V2 records are evidence, not mutable cache rows.  Migration may insert
# an absent identity or skip an identical one, but must never replace it.
# Comparison fields intentionally exclude storage timestamps.
IMMUTABLE_TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "factor_ic_universe_snapshots": (("snapshot_id",), ("content_hash",)),
    "factor_ic_universe_members": (
        ("snapshot_id", "fund_code"),
        ("content_hash",),
    ),
    "factor_ic_nav_observations": (("observation_id",), ("content_hash",)),
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
    "decision_quality_input_artifacts": (
        ("userId", "artifact_id"),
        ("content_hash",),
    ),
    "decision_quality_artifact_receipts": (
        ("userId", "artifact_id"),
        ("content_hash",),
    ),
    "decision_quality_provider_receipts": (
        ("receipt_id",),
        ("content_hash",),
    ),
    "decision_quality_evaluation_snapshots": (
        ("userId", "snapshot_id"),
        ("content_hash",),
    ),
    "decision_quality_contract_rollouts": (
        ("contract_name",),
        ("marker_hash",),
    ),
    "fund_benchmark_mappings": (("userId", "mapping_id"), ("content_hash",)),
    "portfolio_ledger_events": (("event_revision_id",), ("event_hash",)),
    "portfolio_ledger_heads": (
        ("userId", "account_id"),
        ("revision", "chain_hash"),
    ),
    "admin_audit_events": (
        ("eventId",),
        (
            "actorUserId",
            "targetUserId",
            "action",
            "reason",
            "beforeJson",
            "afterJson",
        ),
    ),
}

# An immutable identity may be replayed only when the complete migration
# semantics are already present at the destination.  ``created_at`` and
# ``updated_at`` are storage receipts rather than content identity for the
# legacy immutable ledgers; every payload and every other projected/index
# field is compared.  The rollout ``created_at`` is part of the activation
# boundary itself and therefore is deliberately *not* exempted.
_IMMUTABLE_STORAGE_DIFFERENCE_COLUMNS = frozenset({"created_at", "updated_at"})


_DECISION_QUALITY_REQUIRED_TABLES_V14 = (
    "decision_quality_input_artifacts",
    "decision_quality_evaluation_snapshots",
    "decision_quality_contract_rollouts",
)
_DECISION_QUALITY_REQUIRED_TABLES_V15 = (
    *_DECISION_QUALITY_REQUIRED_TABLES_V14,
    "decision_quality_artifact_receipts",
    "decision_quality_provider_receipts",
)
_DECISION_QUALITY_TABLES = frozenset(_DECISION_QUALITY_REQUIRED_TABLES_V15)
_PROMPT_SHADOW_REQUIRED_TABLES_V16 = (
    "prompt_shadow_runs",
    "prompt_shadow_budget_counters",
)
_NAV_OBSERVATION_REQUIRED_TABLE_V17 = "factor_ic_nav_observations"
_ADMIN_REQUIRED_TABLES_V18 = (
    "password_reset_tokens",
    "admin_audit_events",
)
_PERFORMANCE_REQUIRED_TABLES_V19 = (
    "report_summaries",
    "fund_discovery_report_summaries",
    "stream_sessions",
)
_PERFORMANCE_REQUIRED_COLUMNS_V19 = {
    "reports": frozenset({"summary_payload"}),
    "fund_discovery_reports": frozenset({"summary_payload"}),
    "analysis_jobs": frozenset(
        {"dedup_key", "active_dedup_key", "heartbeat_at"}
    ),
    "discovery_jobs": frozenset(
        {"dedup_key", "active_dedup_key", "heartbeat_at"}
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


def _source_schema_version(connection: sqlite3.Connection) -> int:
    available = _source_table_columns(connection, "schema_meta")
    if available is None:
        return 0
    if not {"id", "version"} <= available:
        raise MigrationError("source schema_meta table is malformed")
    row = connection.execute(
        "SELECT version FROM schema_meta WHERE id = 1"
    ).fetchone()
    if row is None:
        raise MigrationError("source schema_meta has no singleton version row")
    try:
        version = int(row[0])
    except (TypeError, ValueError) as exc:
        raise MigrationError("source schema version is invalid") from exc
    if version < 0:
        raise MigrationError("source schema version is invalid")
    from app.db_migrations import SCHEMA_VERSION

    if version > SCHEMA_VERSION:
        raise MigrationError(
            f"source schema v{version} is newer than this migrator (v{SCHEMA_VERSION})"
        )
    return version


def _normalized_source_sql(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _source_sqlite_table_contract(
    connection: sqlite3.Connection,
    table: str,
) -> dict[str, object] | None:
    columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
    if not columns:
        return None
    column_contract = tuple(
        (
            str(row[1]),
            str(row[2]).upper(),
            int(row[3]),
            None if row[4] is None else str(row[4]),
            int(row[5]),
        )
        for row in columns
    )
    indexes: list[tuple[object, ...]] = []
    for row in connection.execute(f"PRAGMA index_list({table})").fetchall():
        name = str(row[1])
        index_columns = tuple(
            (
                int(item[0]),
                None if item[2] is None else str(item[2]),
                int(item[3]),
                None if item[4] is None else str(item[4]),
                int(item[5]),
            )
            for item in connection.execute(f"PRAGMA index_xinfo({name})").fetchall()
        )
        indexes.append(
            (
                name,
                int(row[2]),
                str(row[3]),
                int(row[4]),
                index_columns,
            )
        )
    triggers = tuple(
        (str(row[0]), _normalized_source_sql(row[1]))
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'trigger' AND tbl_name = ? ORDER BY name",
            (table,),
        ).fetchall()
    )
    foreign_keys = tuple(
        tuple(item) for item in connection.execute(
            f"PRAGMA foreign_key_list({table})"
        ).fetchall()
    )
    return {
        "columns": column_contract,
        "indexes": tuple(sorted(indexes, key=lambda item: str(item[0]))),
        "triggers": triggers,
        "foreign_keys": foreign_keys,
    }


def _v14_legacy_input_artifact_contract(
    current_contract: Mapping[str, object],
) -> dict[str, object]:
    """Derive the exact pre-logical-key v14 table contract.

    ``logical_key`` was added without changing historical artifact payloads or
    hashes.  Real v14 backups may therefore have either the original physical
    table or the additive column/index repair.  No other contract difference
    is accepted.
    """

    columns = tuple(
        item
        for item in current_contract["columns"]  # type: ignore[index]
        if item[0] != "logical_key"
    )
    indexes = tuple(
        item
        for item in current_contract["indexes"]  # type: ignore[index]
        if item[0] != "uq_decision_quality_artifact_logical_key"
    )
    return {
        **dict(current_contract),
        "columns": columns,
        "indexes": indexes,
    }


def _v14_additive_input_artifact_contract(
    current_contract: Mapping[str, object],
) -> dict[str, object]:
    """Allow the v14 ALTER TABLE shape with logical_key appended last."""

    columns = tuple(current_contract["columns"])  # type: ignore[arg-type]
    logical = next(item for item in columns if item[0] == "logical_key")
    reordered = tuple(item for item in columns if item[0] != "logical_key") + (
        logical,
    )
    return {**dict(current_contract), "columns": reordered}


def _validate_source_decision_quality_contract(
    connection: sqlite3.Connection,
) -> int:
    """Validate the immutable source ledgers without repairing the source.

    A current-version backup that lost a table, trigger, unique identity or
    column must not be reported as migration-ready.  Expected semantics are
    derived from a fresh in-memory database built by this checkout, so the
    migration script cannot drift from the application's SQLite contract.
    """

    source_version = _source_schema_version(connection)
    from app.mysql_bootstrap import MYSQL_SCHEMA_VERSION

    if source_version > MYSQL_SCHEMA_VERSION:
        raise MigrationError(
            f"source schema v{source_version} is newer than supported "
            f"v{MYSQL_SCHEMA_VERSION}"
        )
    observed_quality_tables = {
        table
        for table in _DECISION_QUALITY_REQUIRED_TABLES_V15
        if _source_table_columns(connection, table) is not None
    }
    if (
        source_version < 14
        and "decision_quality_contract_rollouts" in observed_quality_tables
    ):
        raise MigrationError(
            "pre-v14 source contains a post-v14 decision-quality rollout table"
        )
    receipt_tables = {
        "decision_quality_artifact_receipts",
        "decision_quality_provider_receipts",
    }
    if source_version < 15 and observed_quality_tables & receipt_tables:
        raise MigrationError(
            "pre-v15 source contains post-v15 decision-quality receipt tables"
        )
    observed_prompt_shadow_tables = {
        table
        for table in _PROMPT_SHADOW_REQUIRED_TABLES_V16
        if _source_table_columns(connection, table) is not None
    }
    if source_version < 16 and observed_prompt_shadow_tables:
        raise MigrationError(
            "pre-v16 source contains post-v16 prompt-shadow operational tables"
        )
    observed_nav_observation_table = (
        _source_table_columns(
            connection,
            _NAV_OBSERVATION_REQUIRED_TABLE_V17,
        )
        is not None
    )
    if source_version < 17 and observed_nav_observation_table:
        raise MigrationError(
            "pre-v17 source contains post-v17 NAV observation ledger"
        )
    observed_admin_tables = {
        table
        for table in _ADMIN_REQUIRED_TABLES_V18
        if _source_table_columns(connection, table) is not None
    }
    if source_version < 18 and observed_admin_tables:
        raise MigrationError(
            "pre-v18 source contains post-v18 admin-management tables"
        )
    from app.db_migrations import run_migrations

    expected = sqlite3.connect(":memory:")
    try:
        run_migrations(expected)
        if source_version >= 15:
            required_tables = _DECISION_QUALITY_REQUIRED_TABLES_V15
        elif source_version == 14:
            required_tables = _DECISION_QUALITY_REQUIRED_TABLES_V14
        else:
            # D1 databases may legitimately contain the first two quality
            # ledgers before the v14 rollout boundary existed.  Validate every
            # observed historical ledger, but keep synthetic/pre-D1 v13 test
            # sources without those optional tables compatible.
            required_tables = tuple(
                table
                for table in (
                    "decision_quality_input_artifacts",
                    "decision_quality_evaluation_snapshots",
                )
                if table in observed_quality_tables
            )
        for table in required_tables:
            actual_contract = _source_sqlite_table_contract(connection, table)
            expected_contract = _source_sqlite_table_contract(expected, table)
            if actual_contract is None:
                raise MigrationError(
                    f"schema v{source_version} source is missing required "
                    f"decision-quality table {table}"
                )
            allowed_contracts = [expected_contract]
            if (
                source_version <= 14
                and table == "decision_quality_input_artifacts"
                and expected_contract is not None
            ):
                allowed_contracts.append(
                    _v14_legacy_input_artifact_contract(expected_contract)
                )
                allowed_contracts.append(
                    _v14_additive_input_artifact_contract(expected_contract)
                )
            if actual_contract not in allowed_contracts:
                raise MigrationError(
                    f"schema v{source_version} source decision-quality contract "
                    f"mismatch: {table}"
                )
        if source_version >= 16:
            for table in _PROMPT_SHADOW_REQUIRED_TABLES_V16:
                actual_contract = _source_sqlite_table_contract(connection, table)
                expected_contract = _source_sqlite_table_contract(expected, table)
                if actual_contract is None:
                    raise MigrationError(
                        f"schema v{source_version} source is missing required "
                        f"prompt-shadow table {table}"
                    )
                if actual_contract != expected_contract:
                    raise MigrationError(
                        f"schema v{source_version} source prompt-shadow contract "
                        f"mismatch: {table}"
                    )
        if source_version >= 17:
            table = _NAV_OBSERVATION_REQUIRED_TABLE_V17
            actual_contract = _source_sqlite_table_contract(connection, table)
            expected_contract = _source_sqlite_table_contract(expected, table)
            if actual_contract is None:
                raise MigrationError(
                    f"schema v{source_version} source is missing required "
                    f"NAV observation ledger {table}"
                )
            if actual_contract != expected_contract:
                raise MigrationError(
                    f"schema v{source_version} source NAV observation contract "
                    f"mismatch: {table}"
                )
        if source_version >= 18:
            for table in _ADMIN_REQUIRED_TABLES_V18:
                actual_contract = _source_sqlite_table_contract(connection, table)
                expected_contract = _source_sqlite_table_contract(expected, table)
                if actual_contract is None:
                    raise MigrationError(
                        f"schema v{source_version} source is missing required "
                        f"admin-management table {table}"
                    )
                if actual_contract != expected_contract:
                    raise MigrationError(
                        f"schema v{source_version} source admin-management "
                        f"contract mismatch: {table}"
                    )
        if source_version >= 19:
            for table, required in _PERFORMANCE_REQUIRED_COLUMNS_V19.items():
                available = _source_table_columns(connection, table)
                # Synthetic migration fixtures may contain only the immutable
                # ledgers. Operational tables remain optional in the existing
                # migration contract and are skipped when absent.
                if available is None:
                    continue
                missing = required - available
                if missing:
                    raise MigrationError(
                        f"schema v{source_version} source performance "
                        f"columns missing from {table}: "
                        + ", ".join(sorted(missing))
                    )
            for table in _PERFORMANCE_REQUIRED_TABLES_V19:
                actual_contract = _source_sqlite_table_contract(
                    connection,
                    table,
                )
                expected_contract = _source_sqlite_table_contract(
                    expected,
                    table,
                )
                if actual_contract is None:
                    raise MigrationError(
                        f"schema v{source_version} source is missing required "
                        f"performance table {table}"
                    )
                if actual_contract != expected_contract:
                    raise MigrationError(
                        f"schema v{source_version} source performance "
                        f"contract mismatch: {table}"
                    )
    finally:
        expected.close()
    return source_version


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


def _source_decision_quality_rollout_marker(
    connection: sqlite3.Connection,
) -> dict[str, str] | None:
    """Return the exact D2 activation marker that MySQL must inherit.

    A v14+ source without this singleton is corrupt and must not be healed by
    seeding a new migration-time boundary.  Older sources may legitimately
    have no marker; the destination bootstrap then creates its first boundary.
    """

    available = _source_table_columns(
        connection,
        "decision_quality_contract_rollouts",
    )
    source_version = _source_schema_version(connection)
    if available is None:
        if source_version >= 14:
            raise MigrationError(
                "schema v14+ source is missing decision-quality rollout table"
            )
        return None
    columns = [
        "contract_name",
        "schema_version",
        "contract_version",
        "required_from",
        "created_at",
        "hash_algorithm",
        "canonicalization",
        "marker_hash",
    ]
    missing = set(columns) - available
    if missing:
        raise MigrationError(
            "decision-quality rollout table is missing columns: "
            + ", ".join(sorted(missing))
        )
    rows = connection.execute(
        "SELECT " + ", ".join(columns)
        + " FROM decision_quality_contract_rollouts ORDER BY contract_name"
    ).fetchall()
    if not rows:
        if source_version >= 14:
            raise MigrationError(
                "schema v14+ source is missing its decision-quality rollout marker"
            )
        return None
    if len(rows) != 1:
        raise MigrationError(
            "decision-quality rollout table must contain exactly one marker"
        )
    from app.services.decision_quality_rollout import (
        normalize_decision_quality_rollout_marker,
    )

    row = rows[0]
    raw = {
        column: row[column] if isinstance(row, sqlite3.Row) else row[index]
        for index, column in enumerate(columns)
    }
    try:
        return normalize_decision_quality_rollout_marker(raw)
    except ValueError as exc:
        raise MigrationError(
            "decision-quality rollout marker failed canonical validation"
        ) from exc


def _row_values(row: object, columns: Sequence[str]) -> tuple[Any, ...]:
    if isinstance(row, Mapping):
        return tuple(row[column] for column in columns)
    if isinstance(row, sqlite3.Row):
        return tuple(row[column] for column in columns)
    return tuple(row)  # type: ignore[arg-type]


def _source_row_mapping(
    row: object,
    *,
    columns: Sequence[str],
) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return {column: row[column] for column in columns}
    if isinstance(row, sqlite3.Row):
        return {column: row[column] for column in columns}
    return dict(zip(columns, tuple(row), strict=True))  # type: ignore[arg-type]


def _strict_decision_quality_payload(
    value: Any,
    *,
    table: str,
) -> dict[str, Any]:
    """Decode one canonical JSON object without accepting duplicate keys."""

    if not isinstance(value, str):
        raise MigrationError(
            f"source decision-quality payload is not JSON text: {table}"
        )

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for key, item in pairs:
            if key in decoded:
                raise ValueError(f"duplicate JSON key: {key}")
            decoded[key] = item
        return decoded

    def reject_nonfinite_constant(constant: str) -> Any:
        raise ValueError(f"non-finite JSON constant: {constant}")

    try:
        payload = json.loads(
            value,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise MigrationError(
            f"source decision-quality payload is invalid canonical JSON: {table}"
        ) from exc
    if not isinstance(payload, dict):
        raise MigrationError(
            f"source decision-quality payload is not an object: {table}"
        )
    from app.services.decision_repository import canonical_json

    if value != canonical_json(payload):
        raise MigrationError(
            f"source decision-quality payload is not canonical JSON: {table}"
        )
    return payload


def _canonical_source_quality_timestamp(value: Any, *, name: str) -> str:
    from app.services.decision_repository import _canonical_aware_timestamp

    normalized = _canonical_aware_timestamp(value, name)
    if value != normalized:
        raise ValueError(f"{name} is not stored in canonical UTC form")
    return normalized


def _one_source_manifest_row(
    connection: sqlite3.Connection,
    *,
    table: str,
    where: str,
    params: Sequence[Any],
) -> sqlite3.Row:
    rows = connection.execute(
        f"SELECT * FROM {table} WHERE {where}",
        tuple(params),
    ).fetchall()
    if len(rows) != 1:
        raise MigrationError(
            f"snapshot manifest reference is missing or duplicated: {table}"
        )
    row = rows[0]
    if not isinstance(row, sqlite3.Row):
        raise MigrationError("snapshot manifest source row has no named columns")
    return row


def _manifest_rows(value: Any, *, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise MigrationError(f"snapshot manifest {name} must be an object list")
    return [dict(row) for row in value]


def _manifest_count(manifest: Mapping[str, Any], key: str, expected: int) -> None:
    value = manifest.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise MigrationError(f"snapshot manifest count mismatch: {key}")


def _manifest_count_map(value: Any, *, name: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise MigrationError(f"snapshot manifest {name} must be a count map")
    result: dict[str, int] = {}
    for raw_key, raw_count in value.items():
        if (
            not isinstance(raw_key, str)
            or not raw_key
            or isinstance(raw_count, bool)
            or not isinstance(raw_count, int)
            or raw_count <= 0
        ):
            raise MigrationError(f"snapshot manifest {name} is invalid")
        result[raw_key] = raw_count
    return result


def _validate_outcome_manifest_source(row: Mapping[str, Any]) -> None:
    from app.services.decision_repository import (
        _observation_hash,
        _observation_is_terminal,
    )

    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        raise MigrationError("snapshot outcome source payload is invalid")
    actual_hash = str(row.get("content_hash") or "").lower()
    if actual_hash != _observation_hash(payload):
        raise MigrationError("snapshot outcome source content hash mismatch")
    status = str(payload.get("status") or "").lower()
    expected = {
        "observation_id": payload.get("observation_id"),
        "decision_event_id": payload.get("decision_event_id")
        or payload.get("event_id"),
        "horizon_trading_days": payload.get("horizon_trading_days"),
        "target_date": payload.get("target_date")
        or payload.get("target_trade_date"),
        "status": status,
        "is_terminal": _observation_is_terminal(payload, status),
    }
    for field, value in expected.items():
        observed = row.get(field)
        if field == "is_terminal":
            observed = bool(observed)
        if observed != value:
            raise MigrationError(
                f"snapshot outcome source index conflicts with payload: {field}"
            )
    revision_no = row.get("revision_no")
    if isinstance(revision_no, bool) or not isinstance(revision_no, int) or revision_no <= 0:
        raise MigrationError("snapshot outcome source revision is invalid")
    created_at = _canonical_guard_compatible_timestamp(
        row.get("created_at"), name="outcome.created_at"
    )
    updated_at = _canonical_guard_compatible_timestamp(
        row.get("updated_at"), name="outcome.updated_at"
    )
    _canonical_guard_compatible_timestamp(
        row.get("observed_at"), name="outcome.observed_at"
    )
    finalized_value = row.get("finalized_at")
    if expected["is_terminal"] is True:
        finalized_at = _canonical_guard_compatible_timestamp(
            finalized_value, name="outcome.finalized_at"
        )
        if not created_at <= finalized_at <= updated_at:
            raise MigrationError("snapshot outcome receipt clocks are out of order")


def _canonical_guard_compatible_timestamp(value: Any, *, name: str) -> datetime:
    normalized = _canonical_source_quality_timestamp(value, name=name)
    return datetime.fromisoformat(normalized)


def _validate_snapshot_manifest_closure(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    manifest: Mapping[str, Any],
    evaluation: Mapping[str, Any] | None = None,
) -> None:
    """Bind every persisted snapshot manifest reference back to its source row."""

    schema_version = manifest.get("schema_version")
    if schema_version == "decision_quality_input_manifest.v2":
        _validate_v2_snapshot_manifest_closure(
            connection,
            user_id=user_id,
            manifest=manifest,
        )
        return
    if schema_version not in {
        "decision_quality_input_manifest.v3",
        "decision_quality_input_manifest.v4",
    }:
        raise MigrationError("snapshot manifest schema_version is unsupported")

    from app.services.decision_quality_snapshot import (
        _artifact_manifest_rows,
        _artifact_receipt_manifest_rows,
        _decode_evidence_row,
        _event_manifest_rows,
        _outcome_manifest_rows,
        _provider_receipt_manifest_rows,
        _validate_event_storage_binding,
    )
    from app.services.decision_repository import (
        _DECISION_QUALITY_ARTIFACT_INDEX_FIELDS,
        _decode_artifact_receipt_row,
        _decode_provider_receipt_row,
        _decode_quality_row,
        normalize_decision_quality_input_artifact,
    )

    events = _manifest_rows(manifest.get("decision_events"), name="decision_events")
    nonformal_events = _manifest_rows(
        manifest.get("nonformal_decision_events"),
        name="nonformal_decision_events",
    )
    outcomes = _manifest_rows(
        manifest.get("terminal_outcomes"), name="terminal_outcomes"
    )
    artifacts = _manifest_rows(
        manifest.get("input_artifacts"), name="input_artifacts"
    )
    ignored_artifacts = _manifest_rows(
        manifest.get("ignored_input_artifacts"),
        name="ignored_input_artifacts",
    )
    artifact_receipts = _manifest_rows(
        manifest.get("artifact_receipts"), name="artifact_receipts"
    )
    provider_receipts = _manifest_rows(
        manifest.get("provider_receipts"), name="provider_receipts"
    )
    candidate_records = _manifest_rows(
        manifest.get("candidate_capture_records"),
        name="candidate_capture_records",
    )
    _manifest_count(manifest, "decision_event_count", len(events))
    _manifest_count(manifest, "nonformal_decision_event_count", len(nonformal_events))
    _manifest_count(
        manifest,
        "observed_decision_event_count",
        len(events) + len(nonformal_events),
    )
    _manifest_count(manifest, "terminal_outcome_count", len(outcomes))
    _manifest_count(
        manifest,
        "input_artifact_count",
        len(artifacts) + len(ignored_artifacts),
    )
    _manifest_count(manifest, "consumed_input_artifact_count", len(artifacts))
    ignored_count = manifest.get("ignored_artifact_count")
    if (
        isinstance(ignored_count, bool)
        or not isinstance(ignored_count, int)
        or ignored_count < len(ignored_artifacts)
    ):
        raise MigrationError("snapshot manifest count mismatch: ignored_artifact_count")
    _manifest_count(manifest, "artifact_receipt_count", len(artifact_receipts))
    _manifest_count(manifest, "provider_receipt_count", len(provider_receipts))
    _manifest_count(manifest, "candidate_capture_count", len(candidate_records))

    capture_status_counts: dict[str, int] = {}
    capture_reason_counts: dict[str, int] = {}
    candidate_artifact_ids: set[str] = set()
    candidate_case_ids: set[str] = set()
    candidate_record_fields = {
        "case_id",
        "artifact_id",
        "artifact_type",
        "capture_status",
        "capture_reason",
        "declared_decision_date_local",
        "live_cohort_date_local",
    }
    for record in candidate_records:
        if set(record) != candidate_record_fields:
            raise MigrationError("snapshot candidate capture record shape is invalid")
        status = str(record.get("capture_status") or "unknown")
        reason = str(record.get("capture_reason") or "unknown")
        capture_status_counts[status] = capture_status_counts.get(status, 0) + 1
        capture_reason_counts[reason] = capture_reason_counts.get(reason, 0) + 1
        artifact_id = str(record.get("artifact_id") or "")
        case_id = str(record.get("case_id") or "")
        if (
            not artifact_id
            or not case_id
            or artifact_id in candidate_artifact_ids
            or case_id in candidate_case_ids
        ):
            raise MigrationError("snapshot candidate capture identity is invalid")
        candidate_artifact_ids.add(artifact_id)
        candidate_case_ids.add(case_id)
    if _manifest_count_map(
        manifest.get("candidate_capture_status_counts"),
        name="candidate_capture_status_counts",
    ) != dict(sorted(capture_status_counts.items())):
        raise MigrationError("snapshot candidate capture status counts mismatch")
    if _manifest_count_map(
        manifest.get("candidate_capture_reason_counts"),
        name="candidate_capture_reason_counts",
    ) != dict(sorted(capture_reason_counts.items())):
        raise MigrationError("snapshot candidate capture reason counts mismatch")
    if candidate_records != sorted(
        candidate_records,
        key=lambda row: (
            str(row.get("artifact_id") or ""),
            str(row.get("case_id") or ""),
        ),
    ):
        raise MigrationError("snapshot candidate capture records are not canonical")

    mature_dates = manifest.get("mature_decision_dates")
    if not isinstance(mature_dates, list):
        raise MigrationError("snapshot mature decision dates must be a list")
    canonical_dates: list[str] = []
    for value in mature_dates:
        if not isinstance(value, str):
            raise MigrationError("snapshot mature decision date is invalid")
        try:
            canonical = datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise MigrationError("snapshot mature decision date is invalid") from exc
        if value != canonical:
            raise MigrationError("snapshot mature decision date is not canonical")
        canonical_dates.append(canonical)
    if canonical_dates != sorted(set(canonical_dates)):
        raise MigrationError("snapshot mature decision dates are not sorted unique")
    _manifest_count(manifest, "mature_decision_day_count", len(canonical_dates))

    seen: dict[str, set[str]] = {}

    def unique(kind: str, identity: str) -> None:
        if not identity or identity in seen.setdefault(kind, set()):
            raise MigrationError(f"snapshot manifest duplicate/empty identity: {kind}")
        seen[kind].add(identity)

    for expected in [*events, *nonformal_events]:
        event_id = str(expected.get("event_id") or "")
        unique("decision_event", event_id)
        row = _one_source_manifest_row(
            connection,
            table="decision_events",
            where="userId = ? AND event_id = ?",
            params=(user_id, event_id),
        )
        event_raw = dict(row)
        event_raw["payload"] = _strict_decision_quality_payload(
            event_raw.get("payload"), table="decision_events"
        )
        decoded_event = _decode_evidence_row(event_raw)
        try:
            _validate_event_storage_binding(decoded_event)
        except Exception as exc:  # noqa: BLE001 - translate source binding faults
            raise MigrationError(
                "snapshot event source content/index/receipt binding mismatch"
            ) from exc
        actual = _event_manifest_rows([decoded_event])[0]
        if actual != expected:
            raise MigrationError("snapshot decision-event manifest binding mismatch")

    for expected in outcomes:
        observation_id = str(expected.get("observation_id") or "")
        unique("outcome_observation", observation_id)
        row = _one_source_manifest_row(
            connection,
            table="outcome_observations",
            where="userId = ? AND observation_id = ?",
            params=(user_id, observation_id),
        )
        outcome_raw = dict(row)
        outcome_raw["payload"] = _strict_decision_quality_payload(
            outcome_raw.get("payload"), table="outcome_observations"
        )
        decoded_outcome = _decode_evidence_row(outcome_raw)
        _validate_outcome_manifest_source(decoded_outcome)
        actual = _outcome_manifest_rows([decoded_outcome])[0]
        if actual != expected:
            raise MigrationError("snapshot outcome manifest binding mismatch")

    artifact_envelopes: dict[str, Mapping[str, Any]] = {}
    for expected in [*artifacts, *ignored_artifacts]:
        artifact_id = str(expected.get("artifact_id") or "")
        unique("input_artifact", artifact_id)
        row = _one_source_manifest_row(
            connection,
            table="decision_quality_input_artifacts",
            where="userId = ? AND artifact_id = ?",
            params=(user_id, artifact_id),
        )
        raw = dict(row)
        raw["payload"] = _strict_decision_quality_payload(
            raw.get("payload"), table="decision_quality_input_artifacts"
        )
        decoded = _decode_quality_row(
            raw,
            normalizer=normalize_decision_quality_input_artifact,
            index_fields=_DECISION_QUALITY_ARTIFACT_INDEX_FIELDS,
        )
        artifact_envelopes[artifact_id] = decoded["payload"]
        actual = _artifact_manifest_rows([decoded])[0]
        if actual != expected:
            raise MigrationError("snapshot artifact manifest binding mismatch")

    for record in candidate_records:
        artifact_id = str(record.get("artifact_id") or "")
        envelope = artifact_envelopes.get(artifact_id)
        if envelope is None or record.get("artifact_type") != envelope.get(
            "artifact_type"
        ):
            raise MigrationError(
                "snapshot candidate capture record is not bound to its artifact"
            )

    for expected in artifact_receipts:
        artifact_id = str(expected.get("artifact_id") or "")
        unique("artifact_receipt", str(expected.get("receipt_id") or ""))
        if expected.get("user_id") != user_id:
            raise MigrationError("snapshot artifact receipt tenant mismatch")
        row = _one_source_manifest_row(
            connection,
            table="decision_quality_artifact_receipts",
            where="userId = ? AND artifact_id = ?",
            params=(user_id, artifact_id),
        )
        raw = dict(row)
        raw["payload"] = _strict_decision_quality_payload(
            raw.get("payload"), table="decision_quality_artifact_receipts"
        )
        actual = _artifact_receipt_manifest_rows(
            [_decode_artifact_receipt_row(raw)]
        )[0]
        if actual != expected:
            raise MigrationError("snapshot artifact receipt binding mismatch")

    for expected in provider_receipts:
        receipt_id = str(expected.get("receipt_id") or "")
        unique("provider_receipt", receipt_id)
        row = _one_source_manifest_row(
            connection,
            table="decision_quality_provider_receipts",
            where="receipt_id = ?",
            params=(receipt_id,),
        )
        raw = dict(row)
        raw["payload"] = _strict_decision_quality_payload(
            raw.get("payload"), table="decision_quality_provider_receipts"
        )
        actual = _provider_receipt_manifest_rows(
            [_decode_provider_receipt_row(raw)]
        )[0]
        if actual != expected:
            raise MigrationError("snapshot provider receipt binding mismatch")

    if schema_version == "decision_quality_input_manifest.v4":
        _validate_v4_prompt_shadow_manifest(
            manifest=manifest,
            evaluation=evaluation,
            artifacts=artifacts,
            artifact_receipts=artifact_receipts,
            artifact_envelopes=artifact_envelopes,
        )


def _validate_v4_prompt_shadow_manifest(
    *,
    manifest: Mapping[str, Any],
    evaluation: Mapping[str, Any] | None,
    artifacts: Sequence[Mapping[str, Any]],
    artifact_receipts: Sequence[Mapping[str, Any]],
    artifact_envelopes: Mapping[str, Mapping[str, Any]],
) -> None:
    from app.services.decision_repository import canonical_hash
    from app.services.prompt_shadow_contracts import (
        PROMPT_GATE_POLICY_ARTIFACT_TYPE,
        PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
        PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE,
        PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE,
    )

    prompt_types = {
        PROMPT_GATE_POLICY_ARTIFACT_TYPE,
        PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE,
        PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
        PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE,
    }
    prompt = manifest.get("prompt_shadow_evidence")
    if not isinstance(prompt, Mapping):
        raise MigrationError("snapshot prompt-shadow manifest is missing")
    material = dict(prompt)
    supplied_hash = material.pop("manifest_hash", None)
    if supplied_hash != canonical_hash(material):
        raise MigrationError("snapshot prompt-shadow manifest hash mismatch")
    if prompt.get("schema_version") != "decision_quality_prompt_shadow_manifest.v1":
        raise MigrationError("snapshot prompt-shadow manifest schema is unsupported")
    artifact_refs = _manifest_rows(
        prompt.get("input_artifact_refs"), name="prompt_shadow.input_artifact_refs"
    )
    receipt_refs = _manifest_rows(
        prompt.get("artifact_receipt_refs"),
        name="prompt_shadow.artifact_receipt_refs",
    )
    paired_refs = _manifest_rows(
        prompt.get("paired_case_refs"), name="prompt_shadow.paired_case_refs"
    )
    gate_refs = _manifest_rows(
        prompt.get("gate_refs"), name="prompt_shadow.gate_refs"
    )
    expected_artifacts = []
    for artifact in artifacts:
        artifact_id = str(artifact.get("artifact_id") or "")
        envelope = artifact_envelopes.get(artifact_id)
        if envelope is None or envelope.get("artifact_type") not in prompt_types:
            continue
        expected_artifacts.append(
            {
                "artifact_id": artifact_id,
                "artifact_type": envelope.get("artifact_type"),
                "artifact_schema_version": envelope.get("artifact_schema_version"),
                "content_hash": artifact.get("content_hash"),
            }
        )
    expected_artifacts.sort(
        key=lambda row: (str(row["artifact_id"]), str(row["content_hash"]))
    )
    if artifact_refs != expected_artifacts:
        raise MigrationError(
            "snapshot prompt-shadow artifact refs do not close over consumed inputs"
        )
    prompt_artifact_ids = {str(row["artifact_id"]) for row in expected_artifacts}
    expected_receipts = sorted(
        [
            {
                "receipt_id": row.get("receipt_id"),
                "content_hash": row.get("content_hash"),
                "artifact_id": row.get("artifact_id"),
                "artifact_content_hash": row.get("artifact_content_hash"),
            }
            for row in artifact_receipts
            if str(row.get("artifact_id") or "") in prompt_artifact_ids
        ],
        key=lambda row: (str(row["artifact_id"]), str(row["receipt_id"])),
    )
    if receipt_refs != expected_receipts:
        raise MigrationError(
            "snapshot prompt-shadow receipt refs do not close over consumed inputs"
        )
    _manifest_count(prompt, "input_artifact_count", len(artifact_refs))
    _manifest_count(prompt, "artifact_receipt_count", len(receipt_refs))
    _manifest_count(prompt, "paired_case_count", len(paired_refs))
    _manifest_count(prompt, "gate_count", len(gate_refs))
    for outer, inner in (
        ("prompt_shadow_input_artifact_count", "input_artifact_count"),
        ("prompt_shadow_artifact_receipt_count", "artifact_receipt_count"),
        ("prompt_shadow_assigned_registration_count", "assigned_registration_count"),
        ("prompt_shadow_paired_case_count", "paired_case_count"),
        ("prompt_shadow_gate_count", "gate_count"),
    ):
        if manifest.get(outer) != prompt.get(inner):
            raise MigrationError(f"snapshot prompt-shadow count mismatch: {outer}")
    if not isinstance(evaluation, Mapping):
        raise MigrationError("snapshot v4 prompt-shadow evaluation is missing")
    history = evaluation.get("prompt_shadow_gate_history")
    if not isinstance(history, list):
        raise MigrationError("snapshot prompt-shadow gate history is invalid")
    expected_gate_refs = [
        {
            "policy_hash": gate.get("policy_hash"),
            "stratum_hash": gate.get("stratum_hash"),
            "gate_hash": gate.get("gate_hash"),
        }
        for gate in history
        if isinstance(gate, Mapping)
    ]
    expected_gate_refs.sort(
        key=lambda row: (str(row["policy_hash"]), str(row["stratum_hash"]))
    )
    expected_paired_refs = sorted(
        [
            dict(ref)
            for gate in history
            if isinstance(gate, Mapping)
            for ref in gate.get("paired_case_refs", [])
            if isinstance(ref, Mapping)
        ],
        key=lambda row: (str(row.get("case_id") or ""), str(row.get("content_hash") or "")),
    )
    if gate_refs != expected_gate_refs or paired_refs != expected_paired_refs:
        raise MigrationError(
            "snapshot prompt-shadow derived refs conflict with evaluation"
        )
    selected = evaluation.get("prompt_shadow_gate")
    if selected != (history[-1] if history else None):
        raise MigrationError("snapshot selected prompt-shadow gate is not canonical")


def _validate_v2_snapshot_manifest_closure(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    manifest: Mapping[str, Any],
) -> None:
    """Validate the compact D2/v14 identity+hash manifest shape."""

    from app.services.decision_quality_snapshot import (
        _decode_evidence_row,
        _validate_event_storage_binding,
    )

    contracts = (
        ("event_refs", "decision_events", "event_id"),
        ("outcome_refs", "outcome_observations", "observation_id"),
        ("artifact_refs", "decision_quality_input_artifacts", "artifact_id"),
    )
    for manifest_key, table, identity_column in contracts:
        references = _manifest_rows(manifest.get(manifest_key), name=manifest_key)
        seen: set[str] = set()
        for reference in references:
            if set(reference) != {identity_column, "content_hash"}:
                raise MigrationError(
                    f"snapshot manifest {manifest_key} reference shape is invalid"
                )
            identity = str(reference.get(identity_column) or "")
            content_hash = str(reference.get("content_hash") or "")
            if not identity or identity in seen:
                raise MigrationError(
                    f"snapshot manifest duplicate/empty identity: {manifest_key}"
                )
            seen.add(identity)
            row = _one_source_manifest_row(
                connection,
                table=table,
                where=f"userId = ? AND {identity_column} = ?",
                params=(user_id, identity),
            )
            if table in {"decision_events", "outcome_observations"}:
                source = dict(row)
                source["payload"] = _strict_decision_quality_payload(
                    source.get("payload"), table=table
                )
                decoded = _decode_evidence_row(source)
                if table == "decision_events":
                    try:
                        _validate_event_storage_binding(decoded)
                    except Exception as exc:  # noqa: BLE001 - storage boundary
                        raise MigrationError(
                            "snapshot event source content/index/receipt binding mismatch"
                        ) from exc
                else:
                    _validate_outcome_manifest_source(decoded)
            if str(row["content_hash"] or "") != content_hash:
                raise MigrationError(
                    f"snapshot manifest {manifest_key} binding mismatch"
                )


def _validate_source_decision_quality_row(
    connection: sqlite3.Connection,
    *,
    table: str,
    row: object,
    columns: Sequence[str],
    rollout_marker: Mapping[str, Any] | None,
) -> None:
    """Run the application's immutable row decoders before migration.

    The SQLite DDL can prevent UPDATE/DELETE, but it cannot prove that an
    INSERT supplied a truthful payload, content hash, or denormalized index
    envelope.  Reusing the repository decoders here makes both dry-run and
    apply reject self-declared evidence rather than faithfully copying it.
    """

    if table == _NAV_OBSERVATION_REQUIRED_TABLE_V17:
        raw = _source_row_mapping(row, columns=columns)
        try:
            from app.services.factor_ic_nav_observation import _validate_stored_row

            _validate_stored_row(raw)
        except Exception as exc:  # noqa: BLE001 - immutable migration boundary
            raise MigrationError(
                "source NAV observation row failed canonical validation"
            ) from exc
        return
    if table not in _DECISION_QUALITY_TABLES:
        return
    raw = _source_row_mapping(row, columns=columns)
    try:
        from app.services.decision_quality_provider_receipts import (
            validate_provider_origin_receipt,
        )
        from app.services.decision_quality_rollout import (
            normalize_decision_quality_rollout_marker,
        )
        from app.services.decision_repository import (
            DecisionQualityIntegrityError,
            _DECISION_QUALITY_ARTIFACT_INDEX_FIELDS,
            _DECISION_QUALITY_SNAPSHOT_INDEX_FIELDS,
            _decode_artifact_receipt_row,
            _decode_provider_receipt_row,
            _decode_quality_row,
            _verify_artifact_receipt_binding,
            normalize_decision_quality_evaluation_snapshot,
            normalize_decision_quality_input_artifact,
        )

        if table != "decision_quality_contract_rollouts":
            raw["payload"] = _strict_decision_quality_payload(
                raw.get("payload"),
                table=table,
            )

        if table == "decision_quality_input_artifacts":
            decoded = _decode_quality_row(
                raw,
                normalizer=normalize_decision_quality_input_artifact,
                index_fields=_DECISION_QUALITY_ARTIFACT_INDEX_FIELDS,
            )
            user_id = raw.get("userId")
            if type(user_id) is not int or user_id <= 0:
                raise DecisionQualityIntegrityError(
                    "stored input artifact tenant is invalid"
                )
            created_at = _canonical_source_quality_timestamp(
                raw.get("created_at"),
                name="input_artifact.created_at",
            )
            if datetime.fromisoformat(created_at) < datetime.fromisoformat(
                str(decoded["payload"]["recorded_at"])
            ):
                raise DecisionQualityIntegrityError(
                    "stored input artifact predates its recorded_at clock"
                )
            return

        if table == "decision_quality_artifact_receipts":
            decoded = _decode_artifact_receipt_row(raw)
            user_id = int(decoded.get("userId") or 0)
            artifact_id = str(decoded.get("artifact_id") or "")
            source_row = connection.execute(
                "SELECT * FROM decision_quality_input_artifacts "
                "WHERE userId = ? AND artifact_id = ?",
                (user_id, artifact_id),
            ).fetchone()
            if source_row is None:
                raise DecisionQualityIntegrityError(
                    "stored artifact receipt has no immutable source artifact"
                )
            source_raw = _source_row_mapping(
                source_row,
                columns=dict(TABLES)["decision_quality_input_artifacts"],
            )
            source_raw["payload"] = _strict_decision_quality_payload(
                source_raw.get("payload"),
                table="decision_quality_input_artifacts",
            )
            _verify_artifact_receipt_binding(
                decoded,
                source_row=source_raw,
                user_id=user_id,
            )
            _canonical_source_quality_timestamp(
                raw.get("created_at"),
                name="artifact_receipt.created_at",
            )
            return

        if table == "decision_quality_provider_receipts":
            decoded = _decode_provider_receipt_row(raw)
            payload = decoded["payload"]
            origin = payload.get("adapter_output")
            if not isinstance(origin, Mapping):
                raise DecisionQualityIntegrityError(
                    "stored provider receipt adapter output is invalid"
                )
            validate_provider_origin_receipt(origin)
            request = origin.get("request")
            response = origin.get("response")
            cache = origin.get("cache")
            if not all(
                isinstance(section, Mapping)
                for section in (request, response, cache)
            ):
                raise DecisionQualityIntegrityError(
                    "stored provider receipt sections are invalid"
                )
            assert isinstance(request, Mapping)
            assert isinstance(response, Mapping)
            assert isinstance(cache, Mapping)
            expected = {
                "provider": origin.get("provider_id"),
                "operation": origin.get("operation"),
                "capture_mode": origin.get("capture_mode"),
                "request_hash": request.get("request_hash"),
                "normalized_payload_hash": response.get(
                    "normalized_payload_hash"
                ),
                "origin_fetched_at": cache.get("origin_fetched_at"),
                "completed_at": response.get("completed_at"),
            }
            if any(payload.get(field) != value for field, value in expected.items()):
                raise DecisionQualityIntegrityError(
                    "stored provider receipt envelope conflicts with adapter output"
                )
            _canonical_source_quality_timestamp(
                raw.get("created_at"),
                name="provider_receipt.created_at",
            )
            return

        if table == "decision_quality_evaluation_snapshots":
            decoded = _decode_quality_row(
                raw,
                normalizer=normalize_decision_quality_evaluation_snapshot,
                index_fields=_DECISION_QUALITY_SNAPSHOT_INDEX_FIELDS,
            )
            user_id = raw.get("userId")
            if type(user_id) is not int or user_id <= 0:
                raise DecisionQualityIntegrityError(
                    "stored evaluation snapshot tenant is invalid"
                )
            _canonical_source_quality_timestamp(
                raw.get("created_at"),
                name="evaluation_snapshot.created_at",
            )
            embedded_marker = decoded["payload"]["input_manifest"].get(
                "contract_rollout_marker"
            )
            if rollout_marker is not None and embedded_marker != dict(rollout_marker):
                raise DecisionQualityIntegrityError(
                    "stored evaluation snapshot rollout marker conflicts with source"
                )
            _validate_snapshot_manifest_closure(
                connection,
                user_id=user_id,
                manifest=decoded["payload"]["input_manifest"],
                evaluation=decoded["payload"]["evaluation"],
            )
            return

        if table == "decision_quality_contract_rollouts":
            normalized_marker = normalize_decision_quality_rollout_marker(raw)
            if rollout_marker is None or normalized_marker != dict(rollout_marker):
                raise DecisionQualityIntegrityError(
                    "stored rollout marker conflicts with source boundary"
                )
            return
    except MigrationError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed at the migration boundary
        raise MigrationError(
            f"source decision-quality row failed canonical validation: {table}"
        ) from exc


def _source_decision_quality_fingerprint(
    connection: sqlite3.Connection,
    *,
    source_version: int,
    rollout_marker: Mapping[str, Any] | None,
    batch_size: int,
) -> str:
    """Hash the exact five-ledger source snapshot used by this apply run."""

    tables: list[dict[str, Any]] = []
    for table, columns in TABLES:
        if table not in _DECISION_QUALITY_TABLES:
            continue
        projection = _source_projection(connection, table=table, columns=columns)
        if projection is None:
            tables.append({"table": table, "status": "absent", "rows": []})
            continue
        select_list, _defaulted = projection
        encoded_rows: list[str] = []
        for rows in _iter_source_batches(
            connection,
            table=table,
            projection=select_list,
            batch_size=batch_size,
        ):
            for row in rows:
                _validate_source_decision_quality_row(
                    connection,
                    table=table,
                    row=row,
                    columns=columns,
                    rollout_marker=rollout_marker,
                )
                encoded_rows.append(
                    json.dumps(
                        _source_row_mapping(row, columns=columns),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                )
        tables.append(
            {"table": table, "status": "present", "rows": sorted(encoded_rows)}
        )
    material = {
        "schema_version": "sqlite_mysql_quality_source_fingerprint.v1",
        "source_schema_version": source_version,
        "rollout_marker": dict(rollout_marker) if rollout_marker is not None else None,
        "tables": tables,
    }
    return hashlib.sha256(
        json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


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
    connection.row_factory = sqlite3.Row
    try:
        # Python's sqlite3 module does not start a transaction for SELECTs.
        # Pin one WAL/read snapshot before the first schema/marker read so a
        # concurrent commit cannot splice artifacts and receipts from two
        # different source states into the plan.
        connection.execute("BEGIN")
        rollout_marker = _source_decision_quality_rollout_marker(connection)
        source_version = _validate_source_decision_quality_contract(connection)
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
            if (
                table in _DECISION_QUALITY_TABLES
                or table == _NAV_OBSERVATION_REQUIRED_TABLE_V17
            ):
                row_count = 0
                for rows in _iter_source_batches(
                    connection,
                    table=table,
                    projection=select_list,
                    batch_size=batch_size,
                ):
                    for row in rows:
                        _validate_source_decision_quality_row(
                            connection,
                            table=table,
                            row=row,
                            columns=columns,
                            rollout_marker=rollout_marker,
                        )
                        row_count += 1
            else:
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
        summary["decision_quality_rollout_marker"] = (
            {
                "status": "preserved",
                "contract_name": rollout_marker["contract_name"],
                "marker_hash": rollout_marker["marker_hash"],
            }
            if rollout_marker is not None
            else {"status": "source_pre_v14_or_absent"}
        )
        summary["source_schema_version"] = source_version
        return summary
    finally:
        # Closing an uncommitted read transaction releases its snapshot and
        # cannot write or repair the source database.
        connection.close()


def _destination_row_values(
    row: object, *, columns: Sequence[str]
) -> tuple[Any, ...]:
    if isinstance(row, Mapping):
        return tuple(row[column] for column in columns)
    return tuple(row)  # type: ignore[arg-type]


def _immutable_comparison_columns(
    *,
    table: str,
    columns: Sequence[str],
) -> tuple[str, ...]:
    excluded: set[str] = set()
    if table not in _DECISION_QUALITY_TABLES:
        excluded.update(_IMMUTABLE_STORAGE_DIFFERENCE_COLUMNS)
    return tuple(column for column in columns if column not in excluded)


def _migration_semantic_value(table: str, column: str, value: Any) -> Any:
    """Normalize DB-driver representation without hiding semantic drift."""

    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if (
        table not in _DECISION_QUALITY_TABLES
        and column == "payload"
        and isinstance(value, str)
    ):
        try:
            return json.dumps(
                json.loads(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            # Non-JSON payloads must still compare byte-for-byte and will be
            # rejected separately for the formal decision-quality ledgers.
            return value
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat) and not isinstance(value, (str, bytes, bytearray)):
        return isoformat()
    return value


def _insert_immutable_row(
    cursor: Any,
    *,
    table: str,
    columns: Sequence[str],
    values: tuple[Any, ...],
) -> str:
    identity_columns, _legacy_comparison_columns = IMMUTABLE_TABLES[table]
    comparison_columns = _immutable_comparison_columns(
        table=table,
        columns=columns,
    )
    value_by_column = dict(zip(columns, values, strict=True))
    where = " AND ".join(f"{column} = %s" for column in identity_columns)
    cursor.execute(
        f"SELECT {', '.join(comparison_columns)} FROM {table} "
        f"WHERE {where} LIMIT 2",
        tuple(value_by_column[column] for column in identity_columns),
    )
    fetchall = getattr(cursor, "fetchall", None)
    if callable(fetchall):
        existing_rows = list(fetchall())
    else:
        # Metadata-free cursors are lightweight unit-test adapters.  Real
        # PyMySQL cursors expose fetchall(), which is the authoritative path
        # for detecting duplicate destination identities.
        first = cursor.fetchone()
        existing_rows = [] if first is None else [first]
    if len(existing_rows) > 1:
        raise ImmutableMigrationConflict(
            f"immutable table {table} contains duplicate destination identities"
        )
    existing = existing_rows[0] if existing_rows else None
    expected = tuple(
        _migration_semantic_value(table, column, value_by_column[column])
        for column in comparison_columns
    )
    if existing is not None:
        actual = tuple(
            _migration_semantic_value(table, column, value)
            for column, value in zip(
                comparison_columns,
                _destination_row_values(existing, columns=comparison_columns),
                strict=True,
            )
        )
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


def _migrate_connections_in_snapshot(
    source: sqlite3.Connection,
    destination: Any,
    *,
    batch_size: int,
    source_version: int,
    rollout_marker: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Copy rows while the caller owns one already-validated read snapshot."""

    cursor = destination.cursor()
    summary: dict[str, Any] = {
        "mode": "apply",
        "batch_size": batch_size,
        "rows_scanned": 0,
        "rows_written": 0,
        "rows_skipped_identical": 0,
        "source_schema_version": source_version,
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
                _validate_source_decision_quality_row(
                    source,
                    table=table,
                    row=row,
                    columns=columns,
                    rollout_marker=rollout_marker,
                )
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


def migrate_connections(
    source: sqlite3.Connection,
    destination: Any,
    *,
    batch_size: int = 500,
) -> dict[str, Any]:
    """Migrate one consistent SQLite snapshot into an initialized target."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if source.in_transaction:
        raise MigrationError(
            "source connection must not have an active transaction before migration"
        )
    source.row_factory = sqlite3.Row
    source.execute("BEGIN")
    try:
        rollout_marker = _source_decision_quality_rollout_marker(source)
        source_version = _validate_source_decision_quality_contract(source)
        return _migrate_connections_in_snapshot(
            source,
            destination,
            batch_size=batch_size,
            source_version=source_version,
            rollout_marker=rollout_marker,
        )
    finally:
        # The source is read-only.  Explicit rollback releases the pinned WAL
        # snapshot while leaving commit/rollback ownership of the destination
        # unchanged for the caller.
        if source.in_transaction:
            source.rollback()


def _validate_mysql_migration_target_engines(destination: Any) -> None:
    """Require transaction rollback support for every table apply can mutate."""

    from app.mysql_bootstrap import MYSQL_MIGRATION_GUARD_TABLE

    required = {table for table, _columns in TABLES} | {
        "schema_meta",
        MYSQL_MIGRATION_GUARD_TABLE,
    }
    cursor = destination.cursor()
    fetchall = getattr(cursor, "fetchall", None)
    if not callable(fetchall):
        raise MigrationError("MySQL target engine metadata cannot be verified")
    quoted = ", ".join(f"'{table}'" for table in sorted(required))
    cursor.execute(
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() "
        f"AND TABLE_NAME IN ({quoted}) ORDER BY TABLE_NAME"
    )
    observed: dict[str, str] = {}
    for row in fetchall():
        if isinstance(row, Mapping):
            table = row.get("TABLE_NAME", row.get("table_name"))
            engine = row.get("ENGINE", row.get("engine"))
        else:
            table, engine = row[:2]
        name = str(table or "")
        if name in observed:
            raise MigrationError("MySQL target engine metadata contains duplicates")
        observed[name] = str(engine or "")
    if set(observed) != required:
        missing = sorted(required - set(observed))
        unexpected = sorted(set(observed) - required)
        raise MigrationError(
            "MySQL target engine metadata does not cover the exact migration set: "
            f"missing={missing}, unexpected={unexpected}"
        )
    invalid = sorted(
        table for table, engine in observed.items() if engine.lower() != "innodb"
    )
    if invalid:
        raise MigrationError(
            "MySQL migration target tables must use InnoDB: " + ", ".join(invalid)
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite → MySQL 数据迁移（默认只读 dry-run）"
    )
    parser.add_argument(
        "--sqlite", default=str(ROOT / "data" / "app.db"), help="SQLite 源文件"
    )
    parser.add_argument(
        "--mysql-url",
        default=os.getenv("FUND_AI_DATABASE_URL", ""),
        help=(
            "mysql://user:pass@host:3306/dbname; defaults to "
            "FUND_AI_DATABASE_URL and is required only with --apply"
        ),
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
    if not str(args.mysql_url or "").strip():
        print(
            "migration apply requires --mysql-url or FUND_AI_DATABASE_URL",
            file=sys.stderr,
        )
        return 2

    import pymysql
    from app.mysql_bootstrap import (
        ensure_mysql_schema,
        finalize_mysql_migration_activation,
        prepare_mysql_migration_guard,
    )

    source = sqlite3.connect(sqlite_path)
    destination = None
    try:
        source.row_factory = sqlite3.Row
        source.execute("BEGIN")
        rollout_marker = _source_decision_quality_rollout_marker(source)
        source_version = _validate_source_decision_quality_contract(source)
        source_fingerprint = _source_decision_quality_fingerprint(
            source,
            source_version=source_version,
            rollout_marker=rollout_marker,
            batch_size=args.batch_size,
        )
        destination = pymysql.connect(**parse_mysql_url(args.mysql_url))
        migration_guard = prepare_mysql_migration_guard(
            destination,
            source_schema_version=source_version,
            source_fingerprint=source_fingerprint,
            source_rollout_marker=rollout_marker,
        )
        ensure_mysql_schema(
            destination,
            decision_quality_rollout_marker=migration_guard["rollout_marker"],
            migration_guard=migration_guard,
            defer_activation=True,
            commit=False,
        )
        _validate_mysql_migration_target_engines(destination)
        summary = _migrate_connections_in_snapshot(
            source,
            destination,
            batch_size=args.batch_size,
            source_version=source_version,
            rollout_marker=rollout_marker,
        )
        finalize_mysql_migration_activation(
            destination,
            migration_guard=migration_guard,
        )
        destination.commit()
        summary["source_fingerprint"] = source_fingerprint
        summary["migration_guard_status"] = "complete"
    except Exception as exc:  # noqa: BLE001 - rollback before reporting any migration fault
        if destination is not None:
            destination.rollback()
        print(
            "migration failed; copy/activation transaction rolled back and "
            f"the persistent guard remains fail-closed: {exc}",
            file=sys.stderr,
        )
        return 2
    finally:
        if source.in_transaction:
            source.rollback()
        source.close()
        if destination is not None:
            destination.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
