"""Mutable D5.1 prompt-shadow run coordination.

The rows in this module are recovery/control-plane state only.  They are not
accepted as evaluation evidence; policy, registration, attempt, output, and
paired-case evidence must be read from the immutable decision-quality ledger
with valid post-commit receipts.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any


PROMPT_SHADOW_RUN_SCHEMA_VERSION = "prompt_shadow_run.v1"
PROMPT_SHADOW_BUDGET_SCHEMA_VERSION = "prompt_shadow_budget_counter.v1"

PROMPT_SHADOW_RUN_STATUSES = frozenset(
    {
        "registration_pending_receipt",
        "registration_failed",
        "champion_attempt_pending_receipt",
        "champion_ready",
        "champion_call_started",
        "champion_output_pending_receipt",
        "champion_succeeded",
        "champion_failed",
        "champion_indeterminate",
        "challenger_leased",
        "challenger_attempt_pending_receipt",
        "challenger_ready",
        "challenger_call_started",
        "challenger_output_pending_receipt",
        "completed",
        "budget_skipped",
        "challenger_failed",
        "challenger_timed_out",
        "challenger_indeterminate",
    }
)
PROMPT_SHADOW_TERMINAL_STATUSES = frozenset(
    {
        "registration_failed",
        "champion_failed",
        "champion_indeterminate",
        "completed",
        "budget_skipped",
        "challenger_failed",
        "challenger_timed_out",
        "challenger_indeterminate",
    }
)

_TRANSITIONS = {
    "registration_pending_receipt": {
        "champion_attempt_pending_receipt",
        "registration_failed",
    },
    "champion_attempt_pending_receipt": {"champion_ready", "champion_failed"},
    "champion_ready": {"champion_call_started", "champion_failed"},
    "champion_call_started": {
        "champion_output_pending_receipt",
        "champion_indeterminate",
    },
    "champion_output_pending_receipt": {
        "champion_succeeded",
        "champion_failed",
    },
    "champion_succeeded": {
        "challenger_leased",
        "budget_skipped",
        "challenger_timed_out",
    },
    "challenger_leased": {
        "challenger_attempt_pending_receipt",
        "challenger_failed",
        "challenger_timed_out",
    },
    "challenger_attempt_pending_receipt": {
        "challenger_ready",
        "challenger_failed",
        "challenger_timed_out",
    },
    "challenger_ready": {
        "challenger_call_started",
        "challenger_failed",
        "challenger_timed_out",
    },
    "challenger_call_started": {
        "challenger_output_pending_receipt",
        "challenger_indeterminate",
    },
    "challenger_output_pending_receipt": {
        "completed",
        "challenger_failed",
    },
}

_RUN_FIELDS = (
    "userId",
    "run_id",
    "schema_version",
    "policy_id",
    "policy_hash",
    "decision_at",
    "registration_artifact_id",
    "champion_attempt_artifact_id",
    "champion_output_artifact_id",
    "champion_report_id",
    "challenger_attempt_artifact_id",
    "challenger_output_artifact_id",
    "status",
    "state_version",
    "challenger_deadline_at",
    "lease_owner_hash",
    "lease_token_hash",
    "lease_acquired_at",
    "lease_expires_at",
    "champion_network_started_at",
    "challenger_network_started_at",
    "budget_scope_key",
    "budget_date_local",
    "budget_reserved_at",
    "terminal_reason",
    "created_at",
    "updated_at",
)
_BUDGET_FIELDS = (
    "scope_key",
    "budget_date_local",
    "schema_version",
    "policy_id",
    "policy_hash",
    "max_calls",
    "reserved_calls",
    "started_calls",
    "completed_calls",
    "failed_calls",
    "state_version",
    "created_at",
    "updated_at",
)
_MUTABLE_RUN_FIELDS = frozenset(_RUN_FIELDS) - {
    "userId",
    "run_id",
    "schema_version",
    "policy_id",
    "policy_hash",
    "decision_at",
    "registration_artifact_id",
    "state_version",
    "created_at",
    "updated_at",
}
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ASCII_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]+$")


class PromptShadowRepositoryError(RuntimeError):
    """Base error for prompt-shadow operational state."""


class PromptShadowIntegrityError(PromptShadowRepositoryError):
    """Stored state is malformed or violates a one-way invariant."""


class PromptShadowConflict(PromptShadowRepositoryError):
    """A run identity or budget scope was reused with conflicting metadata."""


class PromptShadowCasConflict(PromptShadowRepositoryError):
    """The expected run status/state version no longer owns the transition."""


def _dialect(connection: Any) -> str:
    dialect = getattr(connection, "dialect", None)
    if dialect in {"sqlite", "mysql"}:
        return str(dialect)
    if isinstance(connection, sqlite3.Connection):
        return "sqlite"
    module = type(connection).__module__.lower()
    return "mysql" if "mysql" in module or "pymysql" in module else "sqlite"


def _execute(connection: Any, sql: str, params: Sequence[Any] = ()) -> Any:
    if _dialect(connection) == "mysql":
        statement = sql.replace("?", "%s")
        raw = getattr(connection, "_raw", None)
        if raw is not None:
            import pymysql

            cursor = raw.cursor(pymysql.cursors.DictCursor)
            cursor.execute(statement, tuple(params))
            return cursor
        execute = getattr(connection, "execute", None)
        if callable(execute):
            return execute(statement, tuple(params))
        cursor = connection.cursor()
        cursor.execute(statement, tuple(params))
        return cursor
    execute = getattr(connection, "execute", None)
    if callable(execute):
        return execute(sql, tuple(params))
    cursor = connection.cursor()
    cursor.execute(sql, tuple(params))
    return cursor


def _row_dict(cursor: Any, row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        names = [str(column[0]) for column in (cursor.description or ())]
        return dict(zip(names, row, strict=False))


def _fetchone(
    connection: Any,
    sql: str,
    params: Sequence[Any] = (),
) -> dict[str, Any] | None:
    cursor = _execute(connection, sql, params)
    return _row_dict(cursor, cursor.fetchone())


def _fetchall(
    connection: Any,
    sql: str,
    params: Sequence[Any] = (),
) -> list[dict[str, Any]]:
    cursor = _execute(connection, sql, params)
    return [
        row
        for raw in cursor.fetchall()
        if (row := _row_dict(cursor, raw)) is not None
    ]


@contextmanager
def _connection_scope(connection: Any | None) -> Iterator[Any]:
    if connection is not None:
        yield connection
        return
    from app.database import _connect

    owned = _connect()
    try:
        yield owned
        owned.commit()
    except Exception:
        owned.rollback()
        raise
    finally:
        owned.close()


def _raw_connection(connection: Any) -> Any:
    return getattr(connection, "_raw", connection)


def _begin_serialized_write(connection: Any) -> None:
    if _dialect(connection) != "sqlite":
        return
    raw = _raw_connection(connection)
    if not bool(getattr(raw, "in_transaction", False)):
        _execute(connection, "BEGIN IMMEDIATE")


def _user_id(value: object) -> int:
    if isinstance(value, bool):
        raise PromptShadowIntegrityError("user_id must be a positive integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PromptShadowIntegrityError("user_id must be a positive integer") from exc
    if result <= 0:
        raise PromptShadowIntegrityError("user_id must be a positive integer")
    return result


def _text(value: object, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromptShadowIntegrityError(f"{name} is required")
    result = value.strip()
    if len(result) > maximum:
        raise PromptShadowIntegrityError(f"{name} is too long")
    return result


def _optional_text(value: object, name: str, *, maximum: int) -> str | None:
    return None if value is None else _text(value, name, maximum=maximum)


def _identifier(value: object, name: str, *, maximum: int) -> str:
    result = _text(value, name, maximum=maximum)
    if not _ASCII_IDENTIFIER.fullmatch(result):
        raise PromptShadowIntegrityError(f"{name} contains unsupported characters")
    return result


def _optional_identifier(value: object, name: str, *, maximum: int) -> str | None:
    return None if value is None else _identifier(value, name, maximum=maximum)


def _sha256(value: object, name: str) -> str:
    result = _text(value, name, maximum=64)
    if not _LOWER_SHA256.fullmatch(result):
        raise PromptShadowIntegrityError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _optional_sha256(value: object, name: str) -> str | None:
    return None if value is None else _sha256(value, name)


def _timestamp(value: object, name: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise PromptShadowIntegrityError(f"{name} must be an ISO timestamp") from exc
    else:
        raise PromptShadowIntegrityError(f"{name} must be an ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PromptShadowIntegrityError(f"{name} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _optional_timestamp(value: object, name: str) -> str | None:
    return None if value is None else _timestamp(value, name)


def _date(value: object, name: str) -> str:
    result = _text(value, name, maximum=10)
    try:
        return date.fromisoformat(result).isoformat()
    except ValueError as exc:
        raise PromptShadowIntegrityError(f"{name} must be an ISO date") from exc


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise PromptShadowIntegrityError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise PromptShadowIntegrityError(f"{name} must be an integer") from exc
    if result < minimum:
        raise PromptShadowIntegrityError(f"{name} must be at least {minimum}")
    return result


def normalize_prompt_shadow_run(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(value)
    if set(raw) != set(_RUN_FIELDS):
        raise PromptShadowIntegrityError("prompt-shadow run fields conflict with schema")
    status = _text(raw["status"], "status", maximum=64)
    if status not in PROMPT_SHADOW_RUN_STATUSES:
        raise PromptShadowIntegrityError("prompt-shadow run status is unsupported")
    result = {
        "userId": _user_id(raw["userId"]),
        "run_id": _identifier(raw["run_id"], "run_id", maximum=96),
        "schema_version": _text(raw["schema_version"], "schema_version", maximum=64),
        "policy_id": _identifier(raw["policy_id"], "policy_id", maximum=96),
        "policy_hash": _sha256(raw["policy_hash"], "policy_hash"),
        "decision_at": _timestamp(raw["decision_at"], "decision_at"),
        "registration_artifact_id": _identifier(
            raw["registration_artifact_id"], "registration_artifact_id", maximum=96
        ),
        "champion_attempt_artifact_id": _optional_identifier(
            raw["champion_attempt_artifact_id"],
            "champion_attempt_artifact_id",
            maximum=96,
        ),
        "champion_output_artifact_id": _optional_identifier(
            raw["champion_output_artifact_id"],
            "champion_output_artifact_id",
            maximum=96,
        ),
        "champion_report_id": _optional_text(
            raw["champion_report_id"], "champion_report_id", maximum=128
        ),
        "challenger_attempt_artifact_id": _optional_identifier(
            raw["challenger_attempt_artifact_id"],
            "challenger_attempt_artifact_id",
            maximum=96,
        ),
        "challenger_output_artifact_id": _optional_identifier(
            raw["challenger_output_artifact_id"],
            "challenger_output_artifact_id",
            maximum=96,
        ),
        "status": status,
        "state_version": _integer(raw["state_version"], "state_version"),
        "challenger_deadline_at": _optional_timestamp(
            raw["challenger_deadline_at"], "challenger_deadline_at"
        ),
        "lease_owner_hash": _optional_sha256(raw["lease_owner_hash"], "lease_owner_hash"),
        "lease_token_hash": _optional_sha256(raw["lease_token_hash"], "lease_token_hash"),
        "lease_acquired_at": _optional_timestamp(
            raw["lease_acquired_at"], "lease_acquired_at"
        ),
        "lease_expires_at": _optional_timestamp(raw["lease_expires_at"], "lease_expires_at"),
        "champion_network_started_at": _optional_timestamp(
            raw["champion_network_started_at"], "champion_network_started_at"
        ),
        "challenger_network_started_at": _optional_timestamp(
            raw["challenger_network_started_at"], "challenger_network_started_at"
        ),
        "budget_scope_key": _optional_identifier(
            raw["budget_scope_key"], "budget_scope_key", maximum=128
        ),
        "budget_date_local": (
            None
            if raw["budget_date_local"] is None
            else _date(raw["budget_date_local"], "budget_date_local")
        ),
        "budget_reserved_at": _optional_timestamp(
            raw["budget_reserved_at"], "budget_reserved_at"
        ),
        "terminal_reason": _optional_text(
            raw["terminal_reason"], "terminal_reason", maximum=128
        ),
        "created_at": _timestamp(raw["created_at"], "created_at"),
        "updated_at": _timestamp(raw["updated_at"], "updated_at"),
    }
    if result["schema_version"] != PROMPT_SHADOW_RUN_SCHEMA_VERSION:
        raise PromptShadowIntegrityError("prompt-shadow run schema is unsupported")
    if result["updated_at"] < result["created_at"]:
        raise PromptShadowIntegrityError("run updated_at predates created_at")
    lease = (
        result["lease_owner_hash"],
        result["lease_token_hash"],
        result["lease_acquired_at"],
        result["lease_expires_at"],
    )
    if any(item is not None for item in lease) and not all(item is not None for item in lease):
        raise PromptShadowIntegrityError("challenger lease fields must be written together")
    if result["lease_acquired_at"] is not None and not (
        result["lease_acquired_at"] < result["lease_expires_at"]
    ):
        raise PromptShadowIntegrityError("challenger lease must expire after acquisition")
    budget = (
        result["budget_scope_key"],
        result["budget_date_local"],
        result["budget_reserved_at"],
    )
    if any(item is not None for item in budget) and not all(item is not None for item in budget):
        raise PromptShadowIntegrityError("budget reservation fields must be written together")
    if result["budget_reserved_at"] is not None and result["lease_acquired_at"] is None:
        raise PromptShadowIntegrityError("budget reservation requires a challenger lease")
    if result["champion_network_started_at"] is not None and status in {
        "registration_pending_receipt",
        "champion_attempt_pending_receipt",
        "champion_ready",
    }:
        raise PromptShadowIntegrityError("champion network start conflicts with status")
    if status in {
        "champion_call_started",
        "champion_output_pending_receipt",
        "champion_succeeded",
        "champion_indeterminate",
        "challenger_leased",
        "challenger_attempt_pending_receipt",
        "challenger_ready",
        "challenger_call_started",
        "challenger_output_pending_receipt",
        "completed",
        "budget_skipped",
        "challenger_failed",
        "challenger_timed_out",
        "challenger_indeterminate",
    } and result["champion_network_started_at"] is None:
        raise PromptShadowIntegrityError("status requires a committed champion network start")
    if result["challenger_network_started_at"] is not None and status in {
        "registration_pending_receipt",
        "champion_attempt_pending_receipt",
        "champion_ready",
        "champion_call_started",
        "champion_output_pending_receipt",
        "champion_succeeded",
        "challenger_leased",
        "challenger_attempt_pending_receipt",
        "challenger_ready",
    }:
        raise PromptShadowIntegrityError("challenger network start conflicts with status")
    if status in {
        "challenger_call_started",
        "challenger_output_pending_receipt",
        "completed",
        "challenger_indeterminate",
    } and result["challenger_network_started_at"] is None:
        raise PromptShadowIntegrityError("status requires a committed challenger network start")
    if status in {
        "champion_attempt_pending_receipt",
        "champion_ready",
        "champion_call_started",
        "champion_output_pending_receipt",
        "champion_succeeded",
        "champion_indeterminate",
        "challenger_leased",
        "challenger_attempt_pending_receipt",
        "challenger_ready",
        "challenger_call_started",
        "challenger_output_pending_receipt",
        "completed",
        "budget_skipped",
        "challenger_failed",
        "challenger_timed_out",
        "challenger_indeterminate",
    } and result["champion_attempt_artifact_id"] is None:
        raise PromptShadowIntegrityError("status requires a champion attempt artifact")
    if status in {
        "champion_succeeded",
        "challenger_leased",
        "challenger_attempt_pending_receipt",
        "challenger_ready",
        "challenger_call_started",
        "challenger_output_pending_receipt",
        "completed",
        "budget_skipped",
        "challenger_failed",
        "challenger_timed_out",
        "challenger_indeterminate",
    } and result["champion_output_artifact_id"] is None:
        raise PromptShadowIntegrityError("status requires a champion output artifact")
    if status in {
        "challenger_attempt_pending_receipt",
        "challenger_ready",
        "challenger_call_started",
        "challenger_output_pending_receipt",
        "completed",
        "challenger_indeterminate",
    } and result["challenger_attempt_artifact_id"] is None:
        raise PromptShadowIntegrityError("status requires a challenger attempt artifact")
    if status == "completed" and result["challenger_output_artifact_id"] is None:
        raise PromptShadowIntegrityError("completed run requires a challenger output artifact")
    if status == "champion_succeeded" and result["challenger_deadline_at"] is None:
        raise PromptShadowIntegrityError("champion success requires a challenger deadline")
    if status in {
        "challenger_leased",
        "challenger_attempt_pending_receipt",
        "challenger_ready",
        "challenger_call_started",
        "challenger_output_pending_receipt",
        "completed",
        "challenger_failed",
        "challenger_indeterminate",
    } and (result["lease_acquired_at"] is None or result["budget_reserved_at"] is None):
        raise PromptShadowIntegrityError("challenger execution requires lease and budget")
    if status in PROMPT_SHADOW_TERMINAL_STATUSES and result["terminal_reason"] is None:
        raise PromptShadowIntegrityError("terminal prompt-shadow run requires a reason")
    if status not in PROMPT_SHADOW_TERMINAL_STATUSES and result["terminal_reason"] is not None:
        raise PromptShadowIntegrityError("nonterminal prompt-shadow run cannot have a reason")
    return result


def normalize_prompt_shadow_budget(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(value)
    if set(raw) != set(_BUDGET_FIELDS):
        raise PromptShadowIntegrityError("prompt-shadow budget fields conflict with schema")
    result = {
        "scope_key": _identifier(raw["scope_key"], "scope_key", maximum=128),
        "budget_date_local": _date(raw["budget_date_local"], "budget_date_local"),
        "schema_version": _text(raw["schema_version"], "schema_version", maximum=64),
        "policy_id": _identifier(raw["policy_id"], "policy_id", maximum=96),
        "policy_hash": _sha256(raw["policy_hash"], "policy_hash"),
        "max_calls": _integer(raw["max_calls"], "max_calls", minimum=1),
        "reserved_calls": _integer(raw["reserved_calls"], "reserved_calls"),
        "started_calls": _integer(raw["started_calls"], "started_calls"),
        "completed_calls": _integer(raw["completed_calls"], "completed_calls"),
        "failed_calls": _integer(raw["failed_calls"], "failed_calls"),
        "state_version": _integer(raw["state_version"], "state_version"),
        "created_at": _timestamp(raw["created_at"], "created_at"),
        "updated_at": _timestamp(raw["updated_at"], "updated_at"),
    }
    if result["schema_version"] != PROMPT_SHADOW_BUDGET_SCHEMA_VERSION:
        raise PromptShadowIntegrityError("prompt-shadow budget schema is unsupported")
    if not (
        result["completed_calls"] + result["failed_calls"]
        <= result["started_calls"]
        <= result["reserved_calls"]
        <= result["max_calls"]
    ):
        raise PromptShadowIntegrityError("prompt-shadow budget counters violate ordering")
    if result["updated_at"] < result["created_at"]:
        raise PromptShadowIntegrityError("budget updated_at predates created_at")
    return result


def _load_run_locked(connection: Any, user_id: int, run_id: str) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if _dialect(connection) == "mysql" else ""
    row = _fetchone(
        connection,
        "SELECT * FROM prompt_shadow_runs WHERE userId = ? AND run_id = ?" + suffix,
        (user_id, run_id),
    )
    return None if row is None else normalize_prompt_shadow_run(row)


def get_prompt_shadow_run(
    *,
    user_id: int,
    run_id: str,
    connection: Any | None = None,
) -> dict[str, Any] | None:
    uid = _user_id(user_id)
    rid = _identifier(run_id, "run_id", maximum=96)
    with _connection_scope(connection) as db:
        row = _fetchone(
            db,
            "SELECT * FROM prompt_shadow_runs WHERE userId = ? AND run_id = ?",
            (uid, rid),
        )
        return None if row is None else normalize_prompt_shadow_run(row)


def list_prompt_shadow_runs(
    *,
    user_id: int,
    statuses: Sequence[str] | None = None,
    limit: int = 100,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    uid = _user_id(user_id)
    safe_limit = _integer(limit, "limit", minimum=1)
    if safe_limit > 1_000:
        raise PromptShadowIntegrityError("limit exceeds 1000")
    clauses = ["userId = ?"]
    params: list[Any] = [uid]
    if statuses is not None:
        normalized_statuses = tuple(dict.fromkeys(statuses))
        if not normalized_statuses or any(
            status not in PROMPT_SHADOW_RUN_STATUSES for status in normalized_statuses
        ):
            raise PromptShadowIntegrityError("status filter is unsupported")
        clauses.append(
            "status IN (" + ", ".join("?" for _ in normalized_statuses) + ")"
        )
        params.extend(normalized_statuses)
    with _connection_scope(connection) as db:
        rows = _fetchall(
            db,
            "SELECT * FROM prompt_shadow_runs WHERE "
            + " AND ".join(clauses)
            + " ORDER BY decision_at, run_id"
            + f" LIMIT {safe_limit}",
            params,
        )
        return [normalize_prompt_shadow_run(row) for row in rows]


def list_prompt_shadow_worker_candidates(
    *,
    statuses: Sequence[str],
    limit: int = 100,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    """System-worker queue scan; every mutation still carries the row tenant.

    This is intentionally not used by request-facing APIs.  It returns the
    tenant id with each row so the worker can immediately re-enter the normal
    tenant-qualified CAS path.
    """

    normalized_statuses = tuple(dict.fromkeys(statuses))
    if not normalized_statuses or any(
        status not in PROMPT_SHADOW_RUN_STATUSES for status in normalized_statuses
    ):
        raise PromptShadowIntegrityError("worker status filter is unsupported")
    safe_limit = _integer(limit, "limit", minimum=1)
    if safe_limit > 1_000:
        raise PromptShadowIntegrityError("limit exceeds 1000")
    placeholders = ", ".join("?" for _ in normalized_statuses)
    with _connection_scope(connection) as db:
        rows = _fetchall(
            db,
            "SELECT * FROM prompt_shadow_runs WHERE status IN ("
            + placeholders
            + ") ORDER BY updated_at, userId, run_id"
            + f" LIMIT {safe_limit}",
            normalized_statuses,
        )
        return [normalize_prompt_shadow_run(row) for row in rows]


def create_prompt_shadow_run(
    *,
    user_id: int,
    run_id: str,
    policy_id: str,
    policy_hash: str,
    decision_at: str,
    registration_artifact_id: str,
    created_at: str,
    connection: Any | None = None,
) -> dict[str, Any]:
    now = _timestamp(created_at, "created_at")
    candidate = normalize_prompt_shadow_run(
        {
            "userId": _user_id(user_id),
            "run_id": _identifier(run_id, "run_id", maximum=96),
            "schema_version": PROMPT_SHADOW_RUN_SCHEMA_VERSION,
            "policy_id": _identifier(policy_id, "policy_id", maximum=96),
            "policy_hash": _sha256(policy_hash, "policy_hash"),
            "decision_at": _timestamp(decision_at, "decision_at"),
            "registration_artifact_id": _identifier(
                registration_artifact_id, "registration_artifact_id", maximum=96
            ),
            "champion_attempt_artifact_id": None,
            "champion_output_artifact_id": None,
            "champion_report_id": None,
            "challenger_attempt_artifact_id": None,
            "challenger_output_artifact_id": None,
            "status": "registration_pending_receipt",
            "state_version": 0,
            "challenger_deadline_at": None,
            "lease_owner_hash": None,
            "lease_token_hash": None,
            "lease_acquired_at": None,
            "lease_expires_at": None,
            "champion_network_started_at": None,
            "challenger_network_started_at": None,
            "budget_scope_key": None,
            "budget_date_local": None,
            "budget_reserved_at": None,
            "terminal_reason": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    columns = _RUN_FIELDS
    with _connection_scope(connection) as db:
        try:
            _execute(
                db,
                "INSERT INTO prompt_shadow_runs "
                f"({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(candidate[column] for column in columns),
            )
        except Exception:
            existing = get_prompt_shadow_run(
                user_id=candidate["userId"],
                run_id=candidate["run_id"],
                connection=db,
            )
            immutable_fields = (
                "userId",
                "run_id",
                "schema_version",
                "policy_id",
                "policy_hash",
                "decision_at",
                "registration_artifact_id",
            )
            if existing is None or any(
                existing[field] != candidate[field] for field in immutable_fields
            ):
                raise PromptShadowConflict(
                    "prompt-shadow run identity already has different state"
                )
            return existing
        saved = get_prompt_shadow_run(
            user_id=candidate["userId"],
            run_id=candidate["run_id"],
            connection=db,
        )
        if saved != candidate:
            raise PromptShadowIntegrityError("stored prompt-shadow run changed on insert")
        return saved


def _assert_one_way_fields(previous: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    for field in (
        "champion_attempt_artifact_id",
        "champion_output_artifact_id",
        "champion_report_id",
        "challenger_attempt_artifact_id",
        "challenger_output_artifact_id",
        "challenger_deadline_at",
        "champion_network_started_at",
        "challenger_network_started_at",
        "budget_scope_key",
        "budget_date_local",
        "budget_reserved_at",
    ):
        old = previous[field]
        if old is not None and candidate[field] != old:
            raise PromptShadowIntegrityError(f"{field} is immutable once recorded")
    old_lease = tuple(previous[field] for field in (
        "lease_owner_hash",
        "lease_token_hash",
        "lease_acquired_at",
        "lease_expires_at",
    ))
    new_lease = tuple(candidate[field] for field in (
        "lease_owner_hash",
        "lease_token_hash",
        "lease_acquired_at",
        "lease_expires_at",
    ))
    if any(value is not None for value in old_lease) and new_lease != old_lease:
        raise PromptShadowIntegrityError("challenger lease is immutable once recorded")


def transition_prompt_shadow_run(
    *,
    user_id: int,
    run_id: str,
    expected_status: str,
    expected_state_version: int,
    new_status: str,
    updated_at: str,
    updates: Mapping[str, Any] | None = None,
    connection: Any | None = None,
) -> dict[str, Any]:
    uid = _user_id(user_id)
    rid = _identifier(run_id, "run_id", maximum=96)
    version = _integer(expected_state_version, "expected_state_version")
    if expected_status not in PROMPT_SHADOW_RUN_STATUSES:
        raise PromptShadowIntegrityError("expected status is unsupported")
    if new_status not in _TRANSITIONS.get(expected_status, set()):
        raise PromptShadowIntegrityError(
            f"unsupported prompt-shadow transition {expected_status} -> {new_status}"
        )
    changes = dict(updates or {})
    unsupported = set(changes) - _MUTABLE_RUN_FIELDS
    if unsupported or "status" in changes:
        raise PromptShadowIntegrityError("transition contains unsupported fields")
    with _connection_scope(connection) as db:
        previous = get_prompt_shadow_run(user_id=uid, run_id=rid, connection=db)
        if previous is None:
            raise PromptShadowCasConflict("prompt-shadow run does not exist")
        candidate = dict(previous)
        candidate.update(changes)
        candidate["status"] = new_status
        candidate["state_version"] = version + 1
        candidate["updated_at"] = _timestamp(updated_at, "updated_at")
        candidate = normalize_prompt_shadow_run(candidate)
        _assert_one_way_fields(previous, candidate)
        assignments = [
            "status = ?",
            "state_version = state_version + 1",
            "updated_at = ?",
        ]
        params: list[Any] = [new_status, candidate["updated_at"]]
        for field in changes:
            assignments.append(f"{field} = ?")
            params.append(candidate[field])
        params.extend((uid, rid, expected_status, version))
        cursor = _execute(
            db,
            "UPDATE prompt_shadow_runs SET "
            + ", ".join(assignments)
            + " WHERE userId = ? AND run_id = ? AND status = ? AND state_version = ?",
            params,
        )
        if int(getattr(cursor, "rowcount", 0)) != 1:
            raise PromptShadowCasConflict("prompt-shadow run CAS was lost")
        saved = get_prompt_shadow_run(user_id=uid, run_id=rid, connection=db)
        if saved != candidate:
            raise PromptShadowIntegrityError("stored transition differs from candidate")
        return saved


def get_prompt_shadow_budget(
    *,
    scope_key: str,
    budget_date_local: str,
    connection: Any | None = None,
) -> dict[str, Any] | None:
    scope = _identifier(scope_key, "scope_key", maximum=128)
    day = _date(budget_date_local, "budget_date_local")
    with _connection_scope(connection) as db:
        row = _fetchone(
            db,
            "SELECT * FROM prompt_shadow_budget_counters "
            "WHERE scope_key = ? AND budget_date_local = ?",
            (scope, day),
        )
        return None if row is None else normalize_prompt_shadow_budget(row)


def _reserve_budget_locked(
    connection: Any,
    *,
    scope_key: str,
    budget_date_local: str,
    policy_id: str,
    policy_hash: str,
    max_calls: int,
    reserved_at: str,
) -> tuple[dict[str, Any], int | None]:
    scope = _identifier(scope_key, "scope_key", maximum=128)
    day = _date(budget_date_local, "budget_date_local")
    pid = _identifier(policy_id, "policy_id", maximum=96)
    phash = _sha256(policy_hash, "policy_hash")
    maximum = _integer(max_calls, "max_calls", minimum=1)
    now = _timestamp(reserved_at, "reserved_at")
    suffix = " FOR UPDATE" if _dialect(connection) == "mysql" else ""
    row = _fetchone(
        connection,
        "SELECT * FROM prompt_shadow_budget_counters "
        "WHERE scope_key = ? AND budget_date_local = ?" + suffix,
        (scope, day),
    )
    if row is None:
        candidate = normalize_prompt_shadow_budget(
            {
                "scope_key": scope,
                "budget_date_local": day,
                "schema_version": PROMPT_SHADOW_BUDGET_SCHEMA_VERSION,
                "policy_id": pid,
                "policy_hash": phash,
                "max_calls": maximum,
                "reserved_calls": 1,
                "started_calls": 0,
                "completed_calls": 0,
                "failed_calls": 0,
                "state_version": 0,
                "created_at": now,
                "updated_at": now,
            }
        )
        columns = _BUDGET_FIELDS
        try:
            _execute(
                connection,
                "INSERT INTO prompt_shadow_budget_counters "
                f"({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                tuple(candidate[column] for column in columns),
            )
            return candidate, 1
        except Exception:
            row = _fetchone(
                connection,
                "SELECT * FROM prompt_shadow_budget_counters "
                "WHERE scope_key = ? AND budget_date_local = ?" + suffix,
                (scope, day),
            )
            if row is None:
                raise
    current = normalize_prompt_shadow_budget(row)
    if (
        current["policy_id"] != pid
        or current["policy_hash"] != phash
        or current["max_calls"] != maximum
    ):
        raise PromptShadowConflict("daily prompt-shadow budget contract changed in place")
    if current["reserved_calls"] >= maximum:
        return current, None
    cursor = _execute(
        connection,
        "UPDATE prompt_shadow_budget_counters "
        "SET reserved_calls = reserved_calls + 1, state_version = state_version + 1, "
        "updated_at = ? WHERE scope_key = ? AND budget_date_local = ? "
        "AND state_version = ? AND reserved_calls < max_calls",
        (now, scope, day, current["state_version"]),
    )
    if int(getattr(cursor, "rowcount", 0)) != 1:
        raise PromptShadowCasConflict("prompt-shadow budget CAS was lost")
    saved = get_prompt_shadow_budget(
        scope_key=scope,
        budget_date_local=day,
        connection=connection,
    )
    assert saved is not None
    return saved, saved["reserved_calls"]


def lease_prompt_shadow_run(
    *,
    user_id: int,
    run_id: str,
    expected_state_version: int,
    lease_owner_hash: str,
    lease_token_hash: str,
    lease_acquired_at: str,
    lease_expires_at: str,
    scope_key: str,
    budget_date_local: str,
    policy_id: str,
    policy_hash: str,
    max_calls: int,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Atomically reserve daily budget and lease one champion-complete run."""

    uid = _user_id(user_id)
    rid = _identifier(run_id, "run_id", maximum=96)
    version = _integer(expected_state_version, "expected_state_version")
    acquired = _timestamp(lease_acquired_at, "lease_acquired_at")
    expires = _timestamp(lease_expires_at, "lease_expires_at")
    if acquired >= expires:
        raise PromptShadowIntegrityError("lease must expire after acquisition")
    with _connection_scope(connection) as db:
        _begin_serialized_write(db)
        run = _load_run_locked(db, uid, rid)
        if (
            run is None
            or run["status"] != "champion_succeeded"
            or run["state_version"] != version
        ):
            raise PromptShadowCasConflict("prompt-shadow run is no longer leaseable")
        if run["policy_id"] != policy_id or run["policy_hash"] != policy_hash:
            raise PromptShadowConflict("run and budget policy do not match")
        if run["challenger_deadline_at"] is None:
            raise PromptShadowIntegrityError("leaseable run has no challenger deadline")
        if acquired > run["challenger_deadline_at"]:
            timed_out = transition_prompt_shadow_run(
                user_id=uid,
                run_id=rid,
                expected_status="champion_succeeded",
                expected_state_version=version,
                new_status="challenger_timed_out",
                updated_at=acquired,
                updates={"terminal_reason": "challenger_deadline_elapsed"},
                connection=db,
            )
            return {"reserved": False, "run": timed_out, "budget": None, "ordinal": None}
        budget, ordinal = _reserve_budget_locked(
            db,
            scope_key=scope_key,
            budget_date_local=budget_date_local,
            policy_id=policy_id,
            policy_hash=policy_hash,
            max_calls=max_calls,
            reserved_at=acquired,
        )
        if ordinal is None:
            skipped = transition_prompt_shadow_run(
                user_id=uid,
                run_id=rid,
                expected_status="champion_succeeded",
                expected_state_version=version,
                new_status="budget_skipped",
                updated_at=acquired,
                updates={"terminal_reason": "daily_challenger_budget_exhausted"},
                connection=db,
            )
            return {"reserved": False, "run": skipped, "budget": budget, "ordinal": None}
        leased = transition_prompt_shadow_run(
            user_id=uid,
            run_id=rid,
            expected_status="champion_succeeded",
            expected_state_version=version,
            new_status="challenger_leased",
            updated_at=acquired,
            updates={
                "lease_owner_hash": _sha256(lease_owner_hash, "lease_owner_hash"),
                "lease_token_hash": _sha256(lease_token_hash, "lease_token_hash"),
                "lease_acquired_at": acquired,
                "lease_expires_at": expires,
                "budget_scope_key": _identifier(scope_key, "scope_key", maximum=128),
                "budget_date_local": _date(budget_date_local, "budget_date_local"),
                "budget_reserved_at": acquired,
            },
            connection=db,
        )
        return {"reserved": True, "run": leased, "budget": budget, "ordinal": ordinal}


def advance_prompt_shadow_budget(
    *,
    scope_key: str,
    budget_date_local: str,
    action: str,
    updated_at: str,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Advance one reserved call to started, then exactly one terminal bucket."""

    if action not in {"started", "completed", "failed"}:
        raise PromptShadowIntegrityError("budget action is unsupported")
    scope = _identifier(scope_key, "scope_key", maximum=128)
    day = _date(budget_date_local, "budget_date_local")
    now = _timestamp(updated_at, "updated_at")
    with _connection_scope(connection) as db:
        _begin_serialized_write(db)
        suffix = " FOR UPDATE" if _dialect(db) == "mysql" else ""
        row = _fetchone(
            db,
            "SELECT * FROM prompt_shadow_budget_counters "
            "WHERE scope_key = ? AND budget_date_local = ?" + suffix,
            (scope, day),
        )
        if row is None:
            raise PromptShadowConflict("prompt-shadow budget does not exist")
        current = normalize_prompt_shadow_budget(row)
        if action == "started":
            if current["started_calls"] >= current["reserved_calls"]:
                raise PromptShadowConflict("no reserved challenger call can start")
            field = "started_calls"
        else:
            terminal_count = current["completed_calls"] + current["failed_calls"]
            if terminal_count >= current["started_calls"]:
                raise PromptShadowConflict("no started challenger call can finish")
            field = "completed_calls" if action == "completed" else "failed_calls"
        cursor = _execute(
            db,
            f"UPDATE prompt_shadow_budget_counters SET {field} = {field} + 1, "
            "state_version = state_version + 1, updated_at = ? "
            "WHERE scope_key = ? AND budget_date_local = ? AND state_version = ?",
            (now, scope, day, current["state_version"]),
        )
        if int(getattr(cursor, "rowcount", 0)) != 1:
            raise PromptShadowCasConflict("prompt-shadow budget CAS was lost")
        saved = get_prompt_shadow_budget(
            scope_key=scope,
            budget_date_local=day,
            connection=db,
        )
        assert saved is not None
        return saved


def finalize_prompt_shadow_challenger(
    *,
    user_id: int,
    run_id: str,
    expected_status: str,
    expected_state_version: int,
    budget_action: str,
    new_status: str,
    updated_at: str,
    terminal_reason: str,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Atomically terminalize one started call and its global budget slot.

    A retry after a lost commit response is idempotent only when the already
    stored terminal status/reason exactly match.  It never increments another
    call's terminal counter and never makes a provider request retryable.
    """

    if budget_action not in {"completed", "failed"}:
        raise PromptShadowIntegrityError(
            "challenger terminal budget action is unsupported"
        )
    allowed = {
        ("challenger_output_pending_receipt", "completed"),
        ("challenger_output_pending_receipt", "challenger_failed"),
        ("challenger_call_started", "challenger_indeterminate"),
    }
    if (expected_status, new_status) not in allowed:
        raise PromptShadowIntegrityError(
            "challenger atomic terminal transition is unsupported"
        )
    uid = _user_id(user_id)
    rid = _identifier(run_id, "run_id", maximum=96)
    version = _integer(expected_state_version, "expected_state_version")
    now = _timestamp(updated_at, "updated_at")
    reason = _identifier(terminal_reason, "terminal_reason", maximum=512)
    with _connection_scope(connection) as db:
        _begin_serialized_write(db)
        current = get_prompt_shadow_run(user_id=uid, run_id=rid, connection=db)
        if current is None:
            raise PromptShadowCasConflict("prompt-shadow run does not exist")
        if current["status"] in PROMPT_SHADOW_TERMINAL_STATUSES:
            if (
                current["status"] == new_status
                and current.get("terminal_reason") == reason
            ):
                return current
            raise PromptShadowCasConflict(
                "prompt-shadow run already has a different terminal result"
            )
        if current["status"] != expected_status or current["state_version"] != version:
            raise PromptShadowCasConflict(
                "prompt-shadow terminal transition lost its run ownership"
            )
        scope_key = current.get("budget_scope_key")
        budget_date = current.get("budget_date_local")
        if not isinstance(scope_key, str) or not isinstance(budget_date, str):
            raise PromptShadowIntegrityError(
                "prompt-shadow terminal run has no budget reservation"
            )
        advance_prompt_shadow_budget(
            scope_key=scope_key,
            budget_date_local=budget_date,
            action=budget_action,
            updated_at=now,
            connection=db,
        )
        return transition_prompt_shadow_run(
            user_id=uid,
            run_id=rid,
            expected_status=expected_status,
            expected_state_version=version,
            new_status=new_status,
            updated_at=now,
            updates={"terminal_reason": reason},
            connection=db,
        )
