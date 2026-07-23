from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterator, Mapping, Sequence

from app.services.decision_quality_rollout import (
    DECISION_QUALITY_ROLLOUT_CONTRACT_NAME,
    normalize_decision_quality_rollout_marker,
    post_rollout_decision_event_error,
)


class DecisionRepositoryError(RuntimeError):
    """Base error for durable decision evidence."""


class ImmutableRecordConflict(DecisionRepositoryError):
    """An immutable identifier was reused for different content."""


class ObservationFinalizedConflict(ImmutableRecordConflict):
    """A terminal observation was recomputed with different evidence."""


class LedgerHeadConflict(DecisionRepositoryError):
    """The append-only ledger head changed before an event could be appended."""


class DecisionQualityIntegrityError(DecisionRepositoryError):
    """Stored decision-quality evidence failed its hash or envelope contract."""


class DecisionQualityPrimaryStoreUnavailable(DecisionRepositoryError):
    """A formal quality snapshot was routed to a configured database fallback."""


DECISION_QUALITY_INPUT_ARTIFACT_SCHEMA_VERSION = (
    "decision_quality_input_artifact.v1"
)
DECISION_QUALITY_EVALUATION_SNAPSHOT_SCHEMA_VERSION = (
    "decision_quality_evaluation_snapshot.v1"
)
DECISION_QUALITY_EVALUATION_SCHEMA_VERSION = "decision_quality_evaluation.v1"
DECISION_QUALITY_ARTIFACT_RECEIPT_SCHEMA_VERSION = (
    "decision_quality_artifact_receipt.v1"
)
DECISION_QUALITY_ARTIFACT_RECEIPT_POLICY = (
    "decision_quality_post_commit_visibility.v1"
)
DECISION_QUALITY_PROVIDER_RECEIPT_SCHEMA_VERSION = (
    "decision_quality_provider_receipt.v1"
)
DECISION_QUALITY_PROVIDER_ADAPTER_OUTPUT_MAX_BYTES = 1_048_576
DECISION_QUALITY_EVALUATION_STATUSES = frozenset({"available", "unavailable"})
DECISION_QUALITY_READINESS_STATUSES = frozenset(
    {
        "insufficient_data",
        "shadow_evaluation",
        "ready_for_manual_review",
    }
)
DECISION_QUALITY_HUMAN_REVIEW_STATUSES = frozenset(
    {
        "not_evaluated",
        "blocked",
        "eligible_for_human_review",
    }
)
_DECISION_QUALITY_TENANT_SCAN_PAGE_SIZE = 1_000
_DECISION_QUALITY_ARTIFACT_FIELDS = {
    "schema_version",
    "artifact_id",
    "artifact_type",
    "artifact_schema_version",
    "logical_key",
    "source_type",
    "source_report_id",
    "decision_event_id",
    "decision_at",
    "available_at",
    "recorded_at",
    "store_authority",
    "audit_eligible",
    "content_hash",
    "artifact",
}
_DECISION_QUALITY_SNAPSHOT_FIELDS = {
    "schema_version",
    "snapshot_id",
    "evaluation_as_of",
    "evaluator_schema_version",
    "evaluator_version",
    "status",
    "evaluation_hash",
    "input_manifest",
    "input_manifest_hash",
    "config",
    "config_hash",
    "readiness_status",
    "human_review_status",
    "automatic_promotion_allowed",
    "store_authority",
    "audit_eligible",
    "evaluation",
    "content_hash",
}
_DECISION_QUALITY_ARTIFACT_RECEIPT_FIELDS = {
    "schema_version",
    "receipt_id",
    "receipt_policy",
    "user_id",
    "artifact_id",
    "artifact_type",
    "artifact_content_hash",
    "source_row_created_at",
    "source_visible_at",
    "store_authority",
    "content_hash",
}
_DECISION_QUALITY_PROVIDER_RECEIPT_FIELDS = {
    "schema_version",
    "receipt_id",
    "provider",
    "operation",
    "capture_mode",
    "request_hash",
    "adapter_output",
    "adapter_output_sha256",
    "adapter_output_bytes",
    "normalized_payload_hash",
    "origin_fetched_at",
    "completed_at",
    "content_hash",
}


_NON_TERMINAL_OBSERVATION_STATUSES = {
    "pending",
    "immature",
    "data_unavailable",
    "unavailable",
    "retryable",
}


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    raise TypeError(f"value of type {type(value).__name__} is not JSON serializable")


def canonical_json(value: Any) -> str:
    """Return deterministic UTF-8 JSON suitable for evidence hashing."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decision_quality_storage_receipt(recorded_at: str) -> str:
    """Return an honest write receipt while preserving one-tick Lamport order.

    Event-bound report artifacts use the deterministic one-microsecond successor
    of the event receipt so the post-generation audit is strictly later even on
    clocks whose consecutive reads share a tick.  The artifact is physically
    written after the event, so that single logical tick is admissible; any
    larger caller-claimed future time remains a contract violation.
    """

    wall_clock = datetime.fromisoformat(_utc_now().replace("Z", "+00:00"))
    claimed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    future_skew = claimed - wall_clock
    if future_skew > timedelta(microseconds=1):
        raise DecisionQualityIntegrityError(
            "decision-quality recorded_at exceeds its storage receipt clock"
        )
    return max(wall_clock, claimed).astimezone(timezone.utc).isoformat()


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date_from_timestamp(value: str, name: str) -> str:
    text = value.strip()
    if len(text) >= 10:
        candidate = text[:10]
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError:
            pass
    raise ValueError(f"{name} must contain an ISO date")


def _record_material(record: Mapping[str, Any], *, omit: set[str] | None = None) -> dict[str, Any]:
    ignored = {"content_hash", "created_at", "updated_at"} | (omit or set())
    return {str(key): value for key, value in record.items() if str(key) not in ignored}


def _dialect(connection: Any) -> str:
    dialect = getattr(connection, "dialect", None)
    if dialect in {"sqlite", "mysql"}:
        return str(dialect)
    if isinstance(connection, sqlite3.Connection):
        return "sqlite"
    module = type(connection).__module__.lower()
    return "mysql" if "pymysql" in module or "mysql" in module else "sqlite"


def _execute(connection: Any, sql: str, params: Sequence[Any] = ()) -> Any:
    if _dialect(connection) == "mysql":
        statement = sql.replace("?", "%s")
        raw = getattr(connection, "_raw", None)
        if raw is not None:
            # Do not rely on the process-wide ``uses_mysql`` setting inside the
            # generic wrapper: the same process may currently be serving the
            # SQLite fallback, and repository calls must follow the connection's
            # actual dialect.
            from app.db_connect import execute_mysql_statement

            return execute_mysql_statement(raw, statement, tuple(params))
        execute = getattr(connection, "execute", None)
        if callable(execute):
            return execute(statement, tuple(params))
        from app.db_connect import execute_mysql_statement

        return execute_mysql_statement(connection, statement, tuple(params))

    execute = getattr(connection, "execute", None)
    if callable(execute):
        return execute(sql, tuple(params))
    cursor = connection.cursor()
    cursor.execute(sql, tuple(params))
    return cursor


def _row_dict(cursor: Any, row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if isinstance(row, sqlite3.Row):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        names = [str(column[0]) for column in (cursor.description or ())]
        return dict(zip(names, row, strict=False))


def _fetchone(connection: Any, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    cursor = _execute(connection, sql, params)
    try:
        return _row_dict(cursor, cursor.fetchone())
    finally:
        cursor.close()


def _fetchall(connection: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = _execute(connection, sql, params)
    try:
        return [
            row
            for raw in cursor.fetchall()
            if (row := _row_dict(cursor, raw)) is not None
        ]
    finally:
        cursor.close()


@contextmanager
def _connection_scope(connection: Any | None) -> Iterator[Any]:
    if connection is not None:
        yield connection
        return

    # Imported lazily to keep database/bootstrap imports acyclic.
    from app.database import _connect

    # Use the application's unified bootstrap path so a fresh SQLite/fallback
    # database has both legacy domain tables and v10 evidence tables.
    owned = _connect()
    try:
        yield owned
        owned.commit()
    except Exception:
        owned.rollback()
        raise
    finally:
        owned.close()


def _decode_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    payload = result.get("payload")
    if isinstance(payload, str):
        try:
            result["payload"] = json.loads(payload)
        except json.JSONDecodeError:
            pass
    for key in ("eligible", "is_backfilled", "metric_eligible", "is_terminal"):
        if key in result and result[key] is not None:
            result[key] = bool(result[key])
    return result


def _required_sha256(value: Any, name: str) -> str:
    text = _quality_required_text(value, name)
    if (
        text != text.lower()
        or len(text) != 64
        or any(character not in "0123456789abcdef" for character in text)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return text


def _quality_required_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _quality_optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _quality_required_text(value, name)


def _canonical_aware_timestamp(value: Any, name: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _quality_required_text(value, name)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _required_boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _quality_material(value: Mapping[str, Any], *ignored: str) -> dict[str, Any]:
    omitted = set(ignored)
    return {str(key): item for key, item in value.items() if str(key) not in omitted}


def _automatic_promotion_values(value: Any) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) == "automatic_promotion_allowed":
                found.append(item)
            found.extend(_automatic_promotion_values(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            found.extend(_automatic_promotion_values(item))
    return found


def _decision_quality_user_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("user_id must be a positive integer")
    if isinstance(value, int):
        normalized = value
    elif isinstance(value, str) and value.strip().isdigit():
        normalized = int(value.strip())
    else:
        raise ValueError("user_id must be a positive integer")
    if normalized <= 0:
        raise ValueError("user_id must be a positive integer")
    return normalized


def _decision_quality_limit(value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("limit must be a positive integer")
    return min(value, maximum)


def _decision_quality_content_id(value: Any, *, name: str, prefix: str) -> str:
    text = _quality_required_text(value, name)
    if not text.startswith(prefix):
        raise ValueError(f"{name} must start with {prefix}")
    _required_sha256(text[len(prefix) :], f"{name} digest")
    return text


def _decision_quality_store_authority(connection: Any) -> str:
    from app.config import get_settings

    if get_settings().uses_mysql and _dialect(connection) != "mysql":
        return "fallback_non_audited"
    return "primary"


def _require_primary_decision_quality_store(connection: Any) -> None:
    if _decision_quality_store_authority(connection) != "primary":
        raise DecisionQualityPrimaryStoreUnavailable(
            "formal decision-quality snapshots require the configured primary store"
        )


def normalize_decision_quality_input_artifact(
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize one immutable, content-addressed evaluation input artifact."""

    if not isinstance(artifact, Mapping):
        raise ValueError("artifact must be an object")
    if any(not isinstance(key, str) for key in artifact):
        raise ValueError("decision-quality input artifact field names must be strings")
    unknown_fields = set(artifact) - _DECISION_QUALITY_ARTIFACT_FIELDS
    if unknown_fields:
        raise ValueError(
            "decision-quality input artifact contains unsupported fields: "
            + ", ".join(sorted(unknown_fields))
        )
    schema_version = _quality_optional_text(
        artifact.get("schema_version"), "schema_version"
    ) or DECISION_QUALITY_INPUT_ARTIFACT_SCHEMA_VERSION
    if schema_version != DECISION_QUALITY_INPUT_ARTIFACT_SCHEMA_VERSION:
        raise ValueError("decision-quality input artifact schema_version is unsupported")
    artifact_type = _quality_required_text(
        artifact.get("artifact_type"), "artifact_type"
    )
    artifact_payload = artifact.get("artifact")
    if not isinstance(artifact_payload, Mapping):
        raise ValueError("artifact must contain an object-valued artifact payload")
    frozen_artifact = dict(artifact_payload)
    declared_artifact_schema = artifact.get("artifact_schema_version")
    artifact_schema_version = _quality_required_text(
        declared_artifact_schema
        if declared_artifact_schema is not None
        else frozen_artifact.get("schema_version"),
        "artifact_schema_version",
    )
    logical_key = _quality_optional_text(artifact.get("logical_key"), "logical_key")
    if logical_key is not None and len(logical_key) > 255:
        raise ValueError("logical_key must not exceed 255 characters")
    inner_schema_version = _quality_optional_text(
        frozen_artifact.get("schema_version"), "artifact.schema_version"
    )
    if (
        inner_schema_version is not None
        and inner_schema_version != artifact_schema_version
    ):
        raise ValueError("artifact_schema_version conflicts with artifact payload")
    source_type = _quality_required_text(
        artifact.get("source_type"), "source_type"
    )
    source_report_id = _quality_optional_text(
        artifact.get("source_report_id"), "source_report_id"
    )
    decision_event_id = _quality_optional_text(
        artifact.get("decision_event_id"), "decision_event_id"
    )
    decision_at = (
        _canonical_aware_timestamp(artifact.get("decision_at"), "decision_at")
        if artifact.get("decision_at") is not None
        else None
    )
    available_at = _canonical_aware_timestamp(
        artifact.get("available_at"), "available_at"
    )
    recorded_at = _canonical_aware_timestamp(
        artifact.get("recorded_at"), "recorded_at"
    )
    if datetime.fromisoformat(available_at) > datetime.fromisoformat(recorded_at):
        raise ValueError("available_at must not be after recorded_at")
    if (
        decision_at is not None
        and datetime.fromisoformat(decision_at)
        > datetime.fromisoformat(available_at)
    ):
        raise ValueError("decision_at must not be after available_at")
    store_authority = _quality_required_text(
        artifact.get("store_authority"), "store_authority"
    )
    if store_authority not in {"primary", "fallback_non_audited"}:
        raise ValueError("store_authority is unsupported")
    audit_eligible = _required_boolean(
        artifact.get("audit_eligible"), "audit_eligible"
    )
    if audit_eligible and store_authority != "primary":
        raise ValueError("only primary-store artifacts may be audit eligible")
    promotion_values = _automatic_promotion_values(frozen_artifact)
    if any(value is not False for value in promotion_values):
        raise ValueError("automatic_promotion_allowed must never be true in artifacts")

    normalized: dict[str, Any] = {
        "schema_version": schema_version,
        "artifact_type": artifact_type,
        "artifact_schema_version": artifact_schema_version,
        "source_type": source_type,
        "source_report_id": source_report_id,
        "decision_event_id": decision_event_id,
        "decision_at": decision_at,
        "available_at": available_at,
        "recorded_at": recorded_at,
        "store_authority": store_authority,
        "audit_eligible": audit_eligible,
        "artifact": frozen_artifact,
    }
    # Keep the pre-D3 canonical representation byte-for-byte stable.  Existing
    # content-addressed artifacts did not carry this optional field, so adding
    # a JSON null here would invalidate every historical artifact id/hash.
    if logical_key is not None:
        normalized["logical_key"] = logical_key
    content_hash = canonical_hash(normalized)
    supplied_hash = artifact.get("content_hash")
    if supplied_hash is not None and _required_sha256(
        supplied_hash, "content_hash"
    ) != content_hash:
        raise ValueError("decision-quality input artifact content_hash mismatch")
    artifact_id = f"dqa_{content_hash}"
    supplied_id = _quality_optional_text(artifact.get("artifact_id"), "artifact_id")
    if supplied_id is not None and supplied_id != artifact_id:
        raise ValueError("decision-quality input artifact_id mismatch")
    normalized["artifact_id"] = artifact_id
    normalized["content_hash"] = content_hash
    return normalized


def normalize_decision_quality_artifact_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize one immutable proof that a source artifact was commit-visible."""

    if not isinstance(receipt, Mapping):
        raise ValueError("artifact receipt must be an object")
    if any(not isinstance(key, str) for key in receipt):
        raise ValueError("artifact receipt field names must be strings")
    unknown = set(receipt) - _DECISION_QUALITY_ARTIFACT_RECEIPT_FIELDS
    if unknown:
        raise ValueError(
            "artifact receipt contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    schema_version = _quality_optional_text(
        receipt.get("schema_version"), "schema_version"
    ) or DECISION_QUALITY_ARTIFACT_RECEIPT_SCHEMA_VERSION
    if schema_version != DECISION_QUALITY_ARTIFACT_RECEIPT_SCHEMA_VERSION:
        raise ValueError("artifact receipt schema_version is unsupported")
    receipt_policy = _quality_optional_text(
        receipt.get("receipt_policy"), "receipt_policy"
    ) or DECISION_QUALITY_ARTIFACT_RECEIPT_POLICY
    if receipt_policy != DECISION_QUALITY_ARTIFACT_RECEIPT_POLICY:
        raise ValueError("artifact receipt policy is unsupported")
    user_id = _decision_quality_user_id(receipt.get("user_id"))
    artifact_id = _decision_quality_content_id(
        receipt.get("artifact_id"),
        name="artifact_id",
        prefix="dqa_",
    )
    artifact_type = _quality_required_text(
        receipt.get("artifact_type"), "artifact_type"
    )
    artifact_content_hash = _required_sha256(
        receipt.get("artifact_content_hash"), "artifact_content_hash"
    )
    source_row_created_at = _canonical_aware_timestamp(
        receipt.get("source_row_created_at"), "source_row_created_at"
    )
    source_visible_at = _canonical_aware_timestamp(
        receipt.get("source_visible_at"), "source_visible_at"
    )
    if datetime.fromisoformat(source_visible_at) < datetime.fromisoformat(
        source_row_created_at
    ):
        raise ValueError("source_visible_at must not predate the source row")
    store_authority = _quality_required_text(
        receipt.get("store_authority"), "store_authority"
    )
    if store_authority != "primary":
        raise ValueError("artifact receipts require the primary evidence store")
    normalized: dict[str, Any] = {
        "schema_version": schema_version,
        "receipt_policy": receipt_policy,
        "user_id": user_id,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "artifact_content_hash": artifact_content_hash,
        "source_row_created_at": source_row_created_at,
        "source_visible_at": source_visible_at,
        "store_authority": store_authority,
    }
    content_hash = canonical_hash(normalized)
    supplied_hash = receipt.get("content_hash")
    if supplied_hash is not None and _required_sha256(
        supplied_hash, "content_hash"
    ) != content_hash:
        raise ValueError("artifact receipt content_hash mismatch")
    receipt_id = f"dqr_{content_hash}"
    supplied_id = _quality_optional_text(receipt.get("receipt_id"), "receipt_id")
    if supplied_id is not None and supplied_id != receipt_id:
        raise ValueError("artifact receipt_id mismatch")
    normalized["receipt_id"] = receipt_id
    normalized["content_hash"] = content_hash
    return normalized


def normalize_decision_quality_provider_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize one bounded, inline raw adapter response receipt."""

    if not isinstance(receipt, Mapping):
        raise ValueError("provider receipt must be an object")
    if any(not isinstance(key, str) for key in receipt):
        raise ValueError("provider receipt field names must be strings")
    unknown = set(receipt) - _DECISION_QUALITY_PROVIDER_RECEIPT_FIELDS
    if unknown:
        raise ValueError(
            "provider receipt contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    schema_version = _quality_optional_text(
        receipt.get("schema_version"), "schema_version"
    ) or DECISION_QUALITY_PROVIDER_RECEIPT_SCHEMA_VERSION
    if schema_version != DECISION_QUALITY_PROVIDER_RECEIPT_SCHEMA_VERSION:
        raise ValueError("provider receipt schema_version is unsupported")
    provider = _quality_required_text(receipt.get("provider"), "provider")
    operation = _quality_required_text(receipt.get("operation"), "operation")
    capture_mode = _quality_required_text(
        receipt.get("capture_mode"), "capture_mode"
    )
    request_hash = _required_sha256(receipt.get("request_hash"), "request_hash")
    adapter_output = receipt.get("adapter_output")
    if not isinstance(adapter_output, Mapping):
        raise ValueError("adapter_output must be a validated object")
    try:
        adapter_output_json = canonical_json(adapter_output)
        frozen_adapter_output = json.loads(adapter_output_json)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("adapter_output is not canonical JSON") from exc
    adapter_output_bytes = len(adapter_output_json.encode("utf-8"))
    if adapter_output_bytes > DECISION_QUALITY_PROVIDER_ADAPTER_OUTPUT_MAX_BYTES:
        raise ValueError("adapter_output exceeds the inline receipt byte limit")
    adapter_output_sha256 = hashlib.sha256(
        adapter_output_json.encode("utf-8")
    ).hexdigest()
    supplied_output_hash = receipt.get("adapter_output_sha256")
    if supplied_output_hash is not None and _required_sha256(
        supplied_output_hash, "adapter_output_sha256"
    ) != adapter_output_sha256:
        raise ValueError("adapter_output_sha256 mismatch")
    supplied_output_bytes = receipt.get("adapter_output_bytes")
    if supplied_output_bytes is not None and (
        isinstance(supplied_output_bytes, bool)
        or not isinstance(supplied_output_bytes, int)
        or supplied_output_bytes != adapter_output_bytes
    ):
        raise ValueError("adapter_output_bytes mismatch")
    normalized_payload_hash = _required_sha256(
        receipt.get("normalized_payload_hash"), "normalized_payload_hash"
    )
    origin_fetched_at = _canonical_aware_timestamp(
        receipt.get("origin_fetched_at"), "origin_fetched_at"
    )
    completed_at = _canonical_aware_timestamp(
        receipt.get("completed_at"), "completed_at"
    )
    if datetime.fromisoformat(origin_fetched_at) > datetime.fromisoformat(completed_at):
        raise ValueError("origin_fetched_at must not be after completed_at")
    normalized: dict[str, Any] = {
        "schema_version": schema_version,
        "provider": provider,
        "operation": operation,
        "capture_mode": capture_mode,
        "request_hash": request_hash,
        "adapter_output": frozen_adapter_output,
        "adapter_output_sha256": adapter_output_sha256,
        "adapter_output_bytes": adapter_output_bytes,
        "normalized_payload_hash": normalized_payload_hash,
        "origin_fetched_at": origin_fetched_at,
        "completed_at": completed_at,
    }
    content_hash = canonical_hash(normalized)
    supplied_hash = receipt.get("content_hash")
    if supplied_hash is not None and _required_sha256(
        supplied_hash, "content_hash"
    ) != content_hash:
        raise ValueError("provider receipt content_hash mismatch")
    receipt_id = f"dqpr_{content_hash}"
    supplied_id = _quality_optional_text(receipt.get("receipt_id"), "receipt_id")
    if supplied_id is not None and supplied_id != receipt_id:
        raise ValueError("provider receipt_id mismatch")
    normalized["receipt_id"] = receipt_id
    normalized["content_hash"] = content_hash
    return normalized


def normalize_decision_quality_evaluation_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize and verify a persisted output of the pure D1 evaluator."""

    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot must be an object")
    if any(not isinstance(key, str) for key in snapshot):
        raise ValueError(
            "decision-quality evaluation snapshot field names must be strings"
        )
    unknown_fields = set(snapshot) - _DECISION_QUALITY_SNAPSHOT_FIELDS
    if unknown_fields:
        raise ValueError(
            "decision-quality evaluation snapshot contains unsupported fields: "
            + ", ".join(sorted(unknown_fields))
        )
    schema_version = _quality_optional_text(
        snapshot.get("schema_version"), "schema_version"
    ) or DECISION_QUALITY_EVALUATION_SNAPSHOT_SCHEMA_VERSION
    if schema_version != DECISION_QUALITY_EVALUATION_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            "decision-quality evaluation snapshot schema_version is unsupported"
        )
    evaluation = snapshot.get("evaluation")
    if not isinstance(evaluation, Mapping):
        raise ValueError("snapshot evaluation must be an object")
    frozen_evaluation = dict(evaluation)
    evaluator_schema_version = _quality_required_text(
        frozen_evaluation.get("schema_version"), "evaluator_schema_version"
    )
    if evaluator_schema_version != DECISION_QUALITY_EVALUATION_SCHEMA_VERSION:
        raise ValueError("decision-quality evaluator schema_version is unsupported")
    supplied_evaluator_schema = _quality_optional_text(
        snapshot.get("evaluator_schema_version"), "evaluator_schema_version"
    )
    if (
        supplied_evaluator_schema is not None
        and supplied_evaluator_schema != evaluator_schema_version
    ):
        raise ValueError("evaluator_schema_version conflicts with evaluation payload")
    evaluator_version = _quality_required_text(
        snapshot.get("evaluator_version"), "evaluator_version"
    )
    status = _quality_required_text(frozen_evaluation.get("status"), "status")
    if status not in DECISION_QUALITY_EVALUATION_STATUSES:
        raise ValueError("decision-quality evaluation status is unsupported")
    supplied_status = _quality_optional_text(snapshot.get("status"), "status")
    if supplied_status is not None and supplied_status != status:
        raise ValueError("snapshot status conflicts with evaluation payload")

    promotion_values = _automatic_promotion_values(frozen_evaluation)
    if not promotion_values or any(value is not False for value in promotion_values):
        raise ValueError("automatic_promotion_allowed must be explicitly false everywhere")
    if any(
        value is not False for value in _automatic_promotion_values(snapshot)
    ):
        raise ValueError("automatic promotion is forbidden for evaluation snapshots")

    evaluation_hash = _required_sha256(
        frozen_evaluation.get("evaluation_hash"), "evaluation_hash"
    )
    expected_evaluation_hash = canonical_hash(
        _quality_material(frozen_evaluation, "evaluation_hash")
    )
    if evaluation_hash != expected_evaluation_hash:
        raise ValueError("decision-quality evaluation_hash mismatch")
    supplied_evaluation_hash = snapshot.get("evaluation_hash")
    if supplied_evaluation_hash is not None and _required_sha256(
        supplied_evaluation_hash, "evaluation_hash"
    ) != evaluation_hash:
        raise ValueError("snapshot evaluation_hash conflicts with evaluation payload")

    input_audit = frozen_evaluation.get("input_audit")
    evaluation_cutoff = (
        input_audit.get("evaluation_as_of")
        if isinstance(input_audit, Mapping)
        else None
    )
    declared_evaluation_as_of = snapshot.get("evaluation_as_of")
    evaluation_as_of = _canonical_aware_timestamp(
        declared_evaluation_as_of
        if declared_evaluation_as_of is not None
        else evaluation_cutoff,
        "evaluation_as_of",
    )
    if evaluation_cutoff is None or _canonical_aware_timestamp(
        evaluation_cutoff, "evaluation.input_audit.evaluation_as_of"
    ) != evaluation_as_of:
        raise ValueError("evaluation_as_of conflicts with evaluation input audit")

    input_manifest = snapshot.get("input_manifest")
    if not isinstance(input_manifest, Mapping):
        raise ValueError("input_manifest must be an object")
    frozen_manifest = dict(input_manifest)
    rollout_marker = frozen_manifest.get("contract_rollout_marker")
    if not isinstance(rollout_marker, Mapping):
        raise ValueError("input_manifest contract_rollout_marker is required")
    try:
        normalized_rollout_marker = normalize_decision_quality_rollout_marker(
            rollout_marker
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "input_manifest contract_rollout_marker is invalid"
        ) from exc
    frozen_manifest["contract_rollout_marker"] = normalized_rollout_marker
    input_manifest_hash = canonical_hash(frozen_manifest)
    supplied_manifest_hash = snapshot.get("input_manifest_hash")
    if supplied_manifest_hash is not None and _required_sha256(
        supplied_manifest_hash, "input_manifest_hash"
    ) != input_manifest_hash:
        raise ValueError("input_manifest_hash mismatch")

    config = snapshot.get("config", {})
    if not isinstance(config, Mapping):
        raise ValueError("config must be an object")
    frozen_config = dict(config)
    config_hash = canonical_hash(frozen_config)
    supplied_config_hash = snapshot.get("config_hash")
    if supplied_config_hash is not None and _required_sha256(
        supplied_config_hash, "config_hash"
    ) != config_hash:
        raise ValueError("config_hash mismatch")

    readiness_status = _quality_required_text(
        snapshot.get("readiness_status"), "readiness_status"
    )
    if readiness_status not in DECISION_QUALITY_READINESS_STATUSES:
        raise ValueError("readiness_status is unsupported")

    paired_gate = frozen_evaluation.get("paired_gate")
    human_review_status = (
        _quality_required_text(paired_gate.get("status"), "paired_gate.status")
        if isinstance(paired_gate, Mapping)
        else "not_evaluated"
    )
    if human_review_status not in DECISION_QUALITY_HUMAN_REVIEW_STATUSES:
        raise ValueError("human_review_status is unsupported")
    supplied_review_status = _quality_optional_text(
        snapshot.get("human_review_status"), "human_review_status"
    )
    if (
        supplied_review_status is not None
        and supplied_review_status != human_review_status
    ):
        raise ValueError("human_review_status conflicts with evaluation payload")
    if snapshot.get("store_authority") != "primary":
        raise ValueError("evaluation snapshots require the primary evidence store")
    if snapshot.get("audit_eligible") is not True:
        raise ValueError("evaluation snapshots must be explicitly audit eligible")

    normalized: dict[str, Any] = {
        "schema_version": schema_version,
        "evaluation_as_of": evaluation_as_of,
        "evaluator_schema_version": evaluator_schema_version,
        "evaluator_version": evaluator_version,
        "status": status,
        "evaluation_hash": evaluation_hash,
        "input_manifest": frozen_manifest,
        "input_manifest_hash": input_manifest_hash,
        "config": frozen_config,
        "config_hash": config_hash,
        "readiness_status": readiness_status,
        "human_review_status": human_review_status,
        "automatic_promotion_allowed": False,
        "store_authority": "primary",
        "audit_eligible": True,
        "evaluation": frozen_evaluation,
    }
    content_hash = canonical_hash(normalized)
    supplied_content_hash = snapshot.get("content_hash")
    if supplied_content_hash is not None and _required_sha256(
        supplied_content_hash, "content_hash"
    ) != content_hash:
        raise ValueError("decision-quality evaluation snapshot content_hash mismatch")
    snapshot_id = f"dqs_{content_hash}"
    supplied_snapshot_id = _quality_optional_text(
        snapshot.get("snapshot_id"), "snapshot_id"
    )
    if supplied_snapshot_id is not None and supplied_snapshot_id != snapshot_id:
        raise ValueError("decision-quality evaluation snapshot_id mismatch")
    normalized["snapshot_id"] = snapshot_id
    normalized["content_hash"] = content_hash
    return normalized


def _insert_immutable(
    connection: Any,
    *,
    table: str,
    identity_where: str,
    identity_params: Sequence[Any],
    columns: Sequence[str],
    values: Sequence[Any],
    content_hash: str,
) -> tuple[dict[str, Any], bool]:
    value_by_column = dict(zip(columns, values, strict=True))
    if "payload" not in value_by_column or "content_hash" not in value_by_column:
        raise ValueError("immutable inserts require payload and content_hash columns")
    # The supplied content hash binds the canonical payload.  Lock and compare
    # only the compact indexed/metadata columns, then rehydrate the identical
    # payload from the caller instead of transporting LONGTEXT under the lock.
    compact_columns = tuple(column for column in columns if column != "payload")
    compact_select = ", ".join(compact_columns)

    def materialize(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = value_by_column["payload"]
        return _decode_row(result) or result

    lock_suffix = " FOR UPDATE" if _dialect(connection) == "mysql" else ""
    existing = _fetchone(
        connection,
        f"SELECT {compact_select} FROM {table} "
        f"WHERE {identity_where}{lock_suffix}",
        identity_params,
    )
    if existing is not None:
        if existing.get("content_hash") != content_hash:
            raise ImmutableRecordConflict(
                f"{table} identity already exists with different immutable content"
            )
        return materialize(existing), False

    placeholders = ", ".join("?" for _ in columns)
    try:
        _execute(
            connection,
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
    except Exception:
        # Resolve a concurrent identical insert deterministically; preserve the
        # original exception when it was not an identity race.
        raced = _fetchone(
            connection,
            f"SELECT {compact_select} FROM {table} "
            f"WHERE {identity_where}{lock_suffix}",
            identity_params,
        )
        if raced is None:
            raise
        if raced.get("content_hash") != content_hash:
            raise ImmutableRecordConflict(
                f"{table} identity was concurrently written with different content"
            )
        return materialize(raced), False

    return materialize(value_by_column), True


def put_decision_portfolio_snapshot(
    *,
    user_id: int,
    snapshot: Mapping[str, Any],
    connection: Any | None = None,
) -> dict[str, Any]:
    """Insert an immutable point-in-time position snapshot, or return its twin."""
    normalized = normalize_decision_portfolio_snapshot(snapshot)
    snapshot_id = str(normalized["snapshot_id"])
    snapshot_at = str(normalized["snapshot_at"])
    snapshot_date = _optional_text(normalized.get("snapshot_date"))
    source_type = str(normalized["source_type"])
    truth_status = str(normalized["truth_status"])
    account_id = str(normalized["account_id"])
    content_hash = canonical_hash(_record_material(normalized))
    payload = canonical_json(normalized)
    created_at = _utc_now()

    columns = (
        "userId",
        "snapshot_id",
        "account_id",
        "snapshot_at",
        "snapshot_date",
        "source_type",
        "truth_status",
        "ledger_version",
        "cash_yuan",
        "total_market_value_yuan",
        "content_hash",
        "payload",
        "created_at",
    )
    values = (
        int(user_id),
        snapshot_id,
        account_id,
        snapshot_at,
        snapshot_date,
        source_type,
        truth_status,
        normalized.get("ledger_version"),
        normalized.get("cash_yuan"),
        normalized.get("total_market_value_yuan"),
        content_hash,
        payload,
        created_at,
    )
    with _connection_scope(connection) as db:
        result, _ = _insert_immutable(
            db,
            table="decision_portfolio_snapshots",
            identity_where="userId = ? AND snapshot_id = ?",
            identity_params=(int(user_id), snapshot_id),
            columns=columns,
            values=values,
            content_hash=content_hash,
        )
        return result


def normalize_decision_portfolio_snapshot(
    snapshot: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize every indexed/defaulted snapshot field before hashing."""

    snapshot_id = _required_text(snapshot.get("snapshot_id"), "snapshot_id")
    snapshot_at = _required_text(
        snapshot.get("snapshot_at")
        or snapshot.get("captured_at")
        or snapshot.get("fetched_at"),
        "snapshot_at",
    )
    snapshot_date = _optional_text(snapshot.get("snapshot_date") or snapshot.get("as_of_date"))
    snapshot_date = snapshot_date or _date_from_timestamp(snapshot_at, "snapshot_at")
    source_type = _required_text(
        snapshot.get("source_type") or snapshot.get("source"), "source_type"
    )
    truth_status = _optional_text(snapshot.get("truth_status"))
    if truth_status is None:
        truth_status = "confirmed" if bool(snapshot.get("authoritative")) else "estimated"
    account_id = _optional_text(snapshot.get("account_id")) or "default"
    normalized = dict(snapshot)
    normalized.update(
        {
            "snapshot_id": snapshot_id,
            "account_id": account_id,
            "snapshot_at": snapshot_at,
            "snapshot_date": snapshot_date,
            "source_type": source_type,
            "truth_status": truth_status,
            "ledger_version": snapshot.get("ledger_version"),
            "cash_yuan": snapshot.get("cash_yuan"),
            "total_market_value_yuan": snapshot.get("total_market_value_yuan"),
        }
    )
    return normalized


def decision_portfolio_snapshot_content_hash(snapshot: Mapping[str, Any]) -> str:
    return canonical_hash(_record_material(normalize_decision_portfolio_snapshot(snapshot)))


save_decision_portfolio_snapshot = put_decision_portfolio_snapshot


def get_decision_portfolio_snapshot(
    *, user_id: int, snapshot_id: str, connection: Any | None = None
) -> dict[str, Any] | None:
    with _connection_scope(connection) as db:
        return _decode_row(
            _fetchone(
                db,
                "SELECT * FROM decision_portfolio_snapshots WHERE userId = ? AND snapshot_id = ?",
                (int(user_id), snapshot_id),
            )
        )


def _decision_event_fee_model_index(event: Mapping[str, Any]) -> str | None:
    fee_model_value = event.get("fee_model")
    if isinstance(fee_model_value, Mapping):
        return _optional_text(
            fee_model_value.get("type")
            or fee_model_value.get("source")
            or fee_model_value.get("fee_source")
            or fee_model_value.get("model")
        ) or "structured"
    if isinstance(fee_model_value, (list, tuple)):
        return "structured"
    return _optional_text(fee_model_value)


def normalize_decision_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Materialize every indexed/defaulted event field before hashing."""

    event_id = _required_text(event.get("event_id"), "event_id")
    raw_event_type = _optional_text(event.get("event_type"))
    source_type = _optional_text(event.get("source_type"))
    if source_type is None:
        source_type = (
            "discovery"
            if event_id.startswith("discovery:")
            or "discovery" in (raw_event_type or "")
            else "daily"
        )
    event_type = raw_event_type or (
        "fund_discovery_decision"
        if source_type == "discovery"
        else "fund_daily_decision"
    )
    decision_date = _optional_text(event.get("decision_date"))
    decision_at = _optional_text(event.get("decision_at"))
    if decision_at is None:
        decision_date = decision_date or _optional_text(event.get("decision_trade_date"))
    if decision_at is None and decision_date is not None:
        decision_at = f"{decision_date}T00:00:00+00:00"
    decision_at = _required_text(decision_at, "decision_at")
    decision_date = decision_date or _date_from_timestamp(decision_at, "decision_at")
    final_action = _required_text(
        event.get("final_action") or event.get("action"), "final_action"
    )
    action_category = _optional_text(
        event.get("action_category") or event.get("evaluation_class")
    ) or "unknown"
    is_backfilled = bool(event.get("is_backfilled") or event.get("backfilled"))
    metric_eligible = bool(event.get("metric_eligible", not is_backfilled))
    eligible = bool(
        event.get("eligible", action_category in {"buy", "bullish", "bearish"})
    )
    normalized = dict(event)
    normalized.pop("payload_hash", None)
    normalized.update(
        {
            "event_id": event_id,
            "schema_version": _optional_text(event.get("schema_version"))
            or "decision_event.v1",
            "event_type": event_type,
            "source_type": source_type,
            "source_report_id": _optional_text(
                event.get("source_report_id") or event.get("report_id")
            ),
            "decision_at": decision_at,
            "decision_date": decision_date,
            "fund_code": _optional_text(event.get("fund_code")),
            "fund_name": _optional_text(event.get("fund_name")),
            "proposed_action": _optional_text(event.get("proposed_action")),
            "final_action": final_action,
            "action_category": action_category,
            "eligible": eligible,
            "amount_yuan": event.get("amount_yuan"),
            "portfolio_snapshot_id": _optional_text(event.get("portfolio_snapshot_id")),
            "benchmark_mapping_id": _optional_text(event.get("benchmark_mapping_id")),
            "fee_model_index": _decision_event_fee_model_index(event),
            "is_backfilled": is_backfilled,
            "metric_eligible": metric_eligible,
        }
    )
    normalized["payload_hash"] = canonical_hash(
        _record_material(normalized, omit={"payload_hash"})
    )
    return normalized


def decision_event_content_hash(event: Mapping[str, Any]) -> str:
    normalized = normalize_decision_event(event)
    return canonical_hash(_record_material(normalized, omit={"payload_hash"}))


def _supplied_decision_event_payload_hash_error(
    event: Mapping[str, Any],
) -> str | None:
    supplied = event.get("payload_hash")
    if not isinstance(supplied, str):
        return "decision_event_payload_hash_missing_or_invalid"
    digest = supplied.strip().lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        return "decision_event_payload_hash_missing_or_invalid"
    try:
        expected = canonical_hash(
            {key: value for key, value in event.items() if key != "payload_hash"}
        )
    except (TypeError, ValueError, OverflowError, RecursionError):
        return "decision_event_payload_hash_uncomputable"
    if digest != expected:
        return "decision_event_payload_hash_mismatch"
    return None


def get_decision_quality_contract_rollout(
    *,
    connection: Any | None = None,
) -> dict[str, str]:
    """Read and verify the storage-owned D2 activation boundary."""

    with _connection_scope(connection) as db:
        row = _fetchone(
            db,
            "SELECT * FROM decision_quality_contract_rollouts "
            "WHERE contract_name = ?",
            (DECISION_QUALITY_ROLLOUT_CONTRACT_NAME,),
        )
        if row is None:
            raise DecisionQualityIntegrityError(
                "decision-quality rollout marker is missing"
            )
        try:
            return normalize_decision_quality_rollout_marker(row)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DecisionQualityIntegrityError(
                "decision-quality rollout marker failed its immutable contract"
            ) from exc


def put_decision_event(
    *,
    user_id: int,
    event: Mapping[str, Any],
    connection: Any | None = None,
) -> dict[str, Any]:
    """Insert the final, guarded recommendation as immutable evidence."""
    supplied_payload_hash_error = _supplied_decision_event_payload_hash_error(event)
    normalized = normalize_decision_event(event)
    event_id = str(normalized["event_id"])
    source_type = str(normalized["source_type"])
    event_type = str(normalized["event_type"])
    decision_at = str(normalized["decision_at"])
    decision_date = str(normalized["decision_date"])
    final_action = str(normalized["final_action"])
    action_category = str(normalized["action_category"])
    is_backfilled = bool(normalized["is_backfilled"])
    metric_eligible = bool(normalized["metric_eligible"])
    content_hash = decision_event_content_hash(normalized)
    fee_model = _optional_text(normalized.get("fee_model_index"))
    source_report_id = _optional_text(normalized.get("source_report_id"))

    columns = (
        "userId",
        "event_id",
        "schema_version",
        "event_type",
        "source_type",
        "source_report_id",
        "decision_at",
        "decision_date",
        "fund_code",
        "fund_name",
        "proposed_action",
        "final_action",
        "action_category",
        "eligible",
        "amount_yuan",
        "portfolio_snapshot_id",
        "benchmark_mapping_id",
        "fee_model",
        "is_backfilled",
        "metric_eligible",
        "content_hash",
        "payload",
        "created_at",
    )
    with _connection_scope(connection) as db:
        marker = get_decision_quality_contract_rollout(connection=db)
        # On a fresh database the connection bootstrap creates the marker.  The
        # storage receipt must therefore be sampled only after that boundary
        # exists, otherwise the very first valid event can appear pre-rollout.
        created_at = _utc_now()
        existing = _fetchone(
            db,
            "SELECT content_hash FROM decision_events "
            "WHERE userId = ? AND event_id = ?",
            (int(user_id), event_id),
        )
        # Identical retries of a genuinely pre-v14 row remain idempotent. Any
        # new row after activation must be a complete formal D2 event; it can no
        # longer enter storage as an apparent legacy denominator gap.
        if existing is None:
            if supplied_payload_hash_error is not None:
                raise DecisionQualityIntegrityError(
                    "post-rollout decision event failed its supplied payload hash: "
                    f"{supplied_payload_hash_error}"
                )
            receipt = datetime.fromisoformat(
                _canonical_aware_timestamp(created_at, "created_at")
            )
            boundary = datetime.fromisoformat(marker["required_from"])
            if receipt < boundary:
                raise DecisionQualityIntegrityError(
                    "decision event receipt predates the active rollout marker"
                )
            contract_error = post_rollout_decision_event_error(
                normalized,
                expected_store_authority=_decision_quality_store_authority(db),
            )
            if contract_error is not None:
                raise DecisionQualityIntegrityError(
                    "post-rollout decision event failed the formal replay contract: "
                    f"{contract_error}"
                )
            recorded_at = datetime.fromisoformat(
                _canonical_aware_timestamp(
                    normalized.get("recorded_at"), "recorded_at"
                )
            )
            if recorded_at > receipt:
                raise DecisionQualityIntegrityError(
                    "decision event replay receipt is after its storage receipt"
                )
        values = (
            int(user_id),
            event_id,
            normalized["schema_version"],
            event_type,
            source_type,
            source_report_id,
            decision_at,
            decision_date,
            normalized.get("fund_code"),
            normalized.get("fund_name"),
            normalized.get("proposed_action"),
            final_action,
            action_category,
            int(bool(normalized["eligible"])),
            normalized.get("amount_yuan"),
            normalized.get("portfolio_snapshot_id"),
            normalized.get("benchmark_mapping_id"),
            fee_model,
            int(is_backfilled),
            int(metric_eligible),
            content_hash,
            canonical_json(normalized),
            created_at,
        )
        result, _ = _insert_immutable(
            db,
            table="decision_events",
            identity_where="userId = ? AND event_id = ?",
            identity_params=(int(user_id), event_id),
            columns=columns,
            values=values,
            content_hash=content_hash,
        )
        return result


save_decision_event = put_decision_event


def get_decision_event(
    *, user_id: int, event_id: str, connection: Any | None = None
) -> dict[str, Any] | None:
    with _connection_scope(connection) as db:
        return _decode_row(
            _fetchone(
                db,
                "SELECT * FROM decision_events WHERE userId = ? AND event_id = ?",
                (int(user_id), event_id),
            )
        )


def list_decision_events(
    *,
    user_id: int,
    source_type: str | None = None,
    source_report_id: str | None = None,
    metric_eligible_only: bool = False,
    limit: int = 500,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    clauses = ["userId = ?"]
    params: list[Any] = [int(user_id)]
    if source_type:
        clauses.append("source_type = ?")
        params.append(source_type)
    if source_report_id:
        clauses.append("source_report_id = ?")
        params.append(source_report_id)
    if metric_eligible_only:
        clauses.append("metric_eligible = 1")
    safe_limit = max(1, min(int(limit), 10_000))
    with _connection_scope(connection) as db:
        rows = _fetchall(
            db,
            "SELECT * FROM decision_events WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY decision_at DESC, event_id LIMIT {safe_limit}",
            params,
        )
        return [_decode_row(row) or row for row in rows]


def put_fund_benchmark_mapping(
    *,
    user_id: int,
    mapping: Mapping[str, Any],
    connection: Any | None = None,
) -> dict[str, Any]:
    """Insert one immutable, effective-dated benchmark mapping version."""
    mapping_id = _required_text(mapping.get("mapping_id"), "mapping_id")
    fund_code = _required_text(mapping.get("fund_code"), "fund_code")
    benchmark_kind = _required_text(mapping.get("benchmark_kind"), "benchmark_kind")
    completeness = _required_text(mapping.get("completeness"), "completeness")
    benchmark_name = _required_text(mapping.get("benchmark_name"), "benchmark_name")
    valid_from = _required_text(mapping.get("valid_from"), "valid_from")
    source = _required_text(mapping.get("source"), "source")
    content_hash = canonical_hash(_record_material(mapping))
    created_at = _utc_now()

    columns = (
        "userId",
        "mapping_id",
        "fund_code",
        "benchmark_kind",
        "completeness",
        "benchmark_name",
        "benchmark_code",
        "valid_from",
        "valid_to",
        "source",
        "source_ref",
        "content_hash",
        "payload",
        "created_at",
    )
    values = (
        int(user_id),
        mapping_id,
        fund_code,
        benchmark_kind,
        completeness,
        benchmark_name,
        _optional_text(mapping.get("benchmark_code")),
        valid_from,
        _optional_text(mapping.get("valid_to")),
        source,
        _optional_text(mapping.get("source_ref")),
        content_hash,
        canonical_json(mapping),
        created_at,
    )
    with _connection_scope(connection) as db:
        result, _ = _insert_immutable(
            db,
            table="fund_benchmark_mappings",
            identity_where="userId = ? AND mapping_id = ?",
            identity_params=(int(user_id), mapping_id),
            columns=columns,
            values=values,
            content_hash=content_hash,
        )
        return result


save_fund_benchmark_mapping = put_fund_benchmark_mapping


def list_effective_fund_benchmark_mappings(
    *,
    user_id: int,
    fund_code: str,
    as_of_date: str,
    benchmark_kind: str | None = None,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    clauses = [
        "userId = ?",
        "fund_code = ?",
        "valid_from <= ?",
        "(valid_to IS NULL OR valid_to >= ?)",
    ]
    params: list[Any] = [int(user_id), fund_code, as_of_date, as_of_date]
    if benchmark_kind:
        clauses.append("benchmark_kind = ?")
        params.append(benchmark_kind)
    order = (
        "CASE benchmark_kind WHEN 'official_contract' THEN 0 "
        "WHEN 'tracking_index' THEN 1 ELSE 2 END, valid_from DESC, mapping_id"
    )
    with _connection_scope(connection) as db:
        rows = _fetchall(
            db,
            "SELECT * FROM fund_benchmark_mappings WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY {order}",
            params,
        )
        return [_decode_row(row) or row for row in rows]


def get_effective_fund_benchmark_mapping(**kwargs: Any) -> dict[str, Any] | None:
    rows = list_effective_fund_benchmark_mappings(**kwargs)
    return rows[0] if rows else None


def _observation_is_terminal(observation: Mapping[str, Any], status: str) -> bool:
    explicit = observation.get("is_terminal")
    if explicit is not None:
        return bool(explicit)
    return status not in _NON_TERMINAL_OBSERVATION_STATUSES


def _observation_hash(observation: Mapping[str, Any]) -> str:
    # Collection/check timestamps are operational metadata.  Excluding them
    # keeps a repeated read of the same official NAV idempotent while the source
    # valuation dates and values remain part of the immutable evidence.
    return canonical_hash(
        _record_material(
            observation,
            omit={"observation_at", "observed_at", "finalized_at", "revision_no"},
        )
    )


def upsert_outcome_observation(
    *,
    user_id: int,
    observation: Mapping[str, Any],
    connection: Any | None = None,
) -> dict[str, Any]:
    """Create/update pending evidence and permanently lock terminal evidence.

    Each substantive pending-state change is copied to
    ``outcome_observation_revisions``.  Once terminal, an identical retry is a
    no-op and different evidence raises ``ObservationFinalizedConflict``.
    """
    observation_id = _required_text(observation.get("observation_id"), "observation_id")
    event_id = _required_text(
        observation.get("decision_event_id") or observation.get("event_id"),
        "decision_event_id",
    )
    raw_horizon = observation.get("horizon_trading_days")
    if isinstance(raw_horizon, bool):
        raise ValueError("horizon_trading_days must be a positive integer")
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError) as exc:
        raise ValueError("horizon_trading_days must be a positive integer") from exc
    if horizon <= 0:
        raise ValueError("horizon_trading_days must be a positive integer")
    status = _required_text(observation.get("status"), "status").lower()
    terminal = _observation_is_terminal(observation, status)
    observed_at = _optional_text(
        observation.get("observed_at") or observation.get("observation_at")
    ) or _utc_now()
    content_hash = _observation_hash(observation)
    payload = canonical_json(observation)
    now = _utc_now()
    target_date = _optional_text(
        observation.get("target_date") or observation.get("target_trade_date")
    )

    with _connection_scope(connection) as db:
        existing = _fetchone(
            db,
            "SELECT * FROM outcome_observations "
            "WHERE userId = ? AND observation_id = ?",
            (int(user_id), observation_id),
        )
        by_horizon = _fetchone(
            db,
            "SELECT * FROM outcome_observations "
            "WHERE userId = ? AND decision_event_id = ? AND horizon_trading_days = ?",
            (int(user_id), event_id, horizon),
        )
        if existing is None and by_horizon is not None:
            raise ImmutableRecordConflict(
                "decision event/horizon already belongs to a different observation_id"
            )
        if existing is not None and (
            str(existing.get("decision_event_id")) != event_id
            or int(existing.get("horizon_trading_days") or 0) != horizon
        ):
            raise ImmutableRecordConflict(
                "observation_id already belongs to a different event/horizon"
            )

        if existing is not None:
            if bool(existing.get("is_terminal")):
                if existing.get("content_hash") != content_hash:
                    raise ObservationFinalizedConflict(
                        "terminal observation cannot be replaced with different evidence"
                    )
                return _decode_row(existing) or existing
            if existing.get("content_hash") == content_hash:
                return _decode_row(existing) or existing
            revision_no = int(existing.get("revision_no") or 0) + 1
            created_at = str(existing.get("created_at") or now)
            finalized_at = now if terminal else None
            _execute(
                db,
                """
                UPDATE outcome_observations SET
                    target_date = ?, status = ?, is_terminal = ?, revision_no = ?,
                    observed_at = ?, finalized_at = ?, content_hash = ?, payload = ?,
                    updated_at = ?
                WHERE userId = ? AND observation_id = ? AND is_terminal = 0
                """,
                (
                    target_date,
                    status,
                    int(terminal),
                    revision_no,
                    observed_at,
                    finalized_at,
                    content_hash,
                    payload,
                    now,
                    int(user_id),
                    observation_id,
                ),
            )
        else:
            revision_no = 1
            created_at = now
            finalized_at = now if terminal else None
            _execute(
                db,
                """
                INSERT INTO outcome_observations (
                    userId, observation_id, decision_event_id,
                    horizon_trading_days, target_date, status, is_terminal,
                    revision_no, observed_at, finalized_at, content_hash,
                    payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    observation_id,
                    event_id,
                    horizon,
                    target_date,
                    status,
                    int(terminal),
                    revision_no,
                    observed_at,
                    finalized_at,
                    content_hash,
                    payload,
                    created_at,
                    now,
                ),
            )

        _execute(
            db,
            """
            INSERT INTO outcome_observation_revisions (
                userId, observation_id, revision_no, status, is_terminal,
                observed_at, content_hash, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                observation_id,
                revision_no,
                status,
                int(terminal),
                observed_at,
                content_hash,
                payload,
                now,
            ),
        )
        result = _fetchone(
            db,
            "SELECT * FROM outcome_observations WHERE userId = ? AND observation_id = ?",
            (int(user_id), observation_id),
        )
        assert result is not None
        return _decode_row(result) or result


save_outcome_observation = upsert_outcome_observation


def get_outcome_observation(
    *, user_id: int, observation_id: str, connection: Any | None = None
) -> dict[str, Any] | None:
    with _connection_scope(connection) as db:
        return _decode_row(
            _fetchone(
                db,
                "SELECT * FROM outcome_observations WHERE userId = ? AND observation_id = ?",
                (int(user_id), observation_id),
            )
        )


def list_outcome_observations(
    *,
    user_id: int,
    decision_event_id: str | None = None,
    pending_only: bool = False,
    limit: int = 1000,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    clauses = ["userId = ?"]
    params: list[Any] = [int(user_id)]
    if decision_event_id:
        clauses.append("decision_event_id = ?")
        params.append(decision_event_id)
    if pending_only:
        clauses.append("is_terminal = 0")
    safe_limit = max(1, min(int(limit), 10_000))
    with _connection_scope(connection) as db:
        rows = _fetchall(
            db,
            "SELECT * FROM outcome_observations WHERE "
            + " AND ".join(clauses)
            + f" ORDER BY observed_at DESC, observation_id LIMIT {safe_limit}",
            params,
        )
        return [_decode_row(row) or row for row in rows]


def list_outcome_observation_revisions(
    *, user_id: int, observation_id: str, connection: Any | None = None
) -> list[dict[str, Any]]:
    with _connection_scope(connection) as db:
        rows = _fetchall(
            db,
            "SELECT * FROM outcome_observation_revisions "
            "WHERE userId = ? AND observation_id = ? ORDER BY revision_no",
            (int(user_id), observation_id),
        )
        return [_decode_row(row) or row for row in rows]


_DECISION_QUALITY_ARTIFACT_INDEX_FIELDS = (
    "artifact_id",
    "schema_version",
    "artifact_type",
    "artifact_schema_version",
    "logical_key",
    "source_type",
    "source_report_id",
    "decision_event_id",
    "decision_at",
    "available_at",
    "recorded_at",
    "store_authority",
    "audit_eligible",
    "content_hash",
)
_DECISION_QUALITY_SNAPSHOT_INDEX_FIELDS = (
    "snapshot_id",
    "schema_version",
    "evaluation_as_of",
    "evaluator_schema_version",
    "evaluator_version",
    "status",
    "evaluation_hash",
    "input_manifest_hash",
    "config_hash",
    "readiness_status",
    "human_review_status",
    "automatic_promotion_allowed",
    "store_authority",
    "audit_eligible",
    "content_hash",
)
_DECISION_QUALITY_ARTIFACT_RECEIPT_INDEX_FIELDS = (
    "receipt_id",
    "schema_version",
    "receipt_policy",
    "artifact_id",
    "artifact_type",
    "artifact_content_hash",
    "source_row_created_at",
    "source_visible_at",
    "store_authority",
    "content_hash",
)
_DECISION_QUALITY_PROVIDER_RECEIPT_INDEX_FIELDS = (
    "receipt_id",
    "schema_version",
    "provider",
    "operation",
    "capture_mode",
    "request_hash",
    "adapter_output_sha256",
    "adapter_output_bytes",
    "normalized_payload_hash",
    "origin_fetched_at",
    "completed_at",
    "content_hash",
)


def _quality_index_value(row: Mapping[str, Any], field: str) -> Any:
    value = row.get(field)
    if field in {"audit_eligible", "automatic_promotion_allowed"}:
        if type(value) not in {bool, int} or value not in {0, 1}:
            raise DecisionQualityIntegrityError(
                f"stored decision-quality {field} is not a database boolean"
            )
        return bool(value)
    return value


def _decode_quality_row(
    row: Mapping[str, Any],
    *,
    normalizer: Any,
    index_fields: Sequence[str],
) -> dict[str, Any]:
    result = dict(row)
    raw_payload = result.get("payload")
    if isinstance(raw_payload, Mapping):
        payload = dict(raw_payload)
    elif isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise DecisionQualityIntegrityError(
                "stored decision-quality payload is invalid JSON"
            ) from exc
    else:
        raise DecisionQualityIntegrityError(
            "stored decision-quality payload is neither JSON text nor an object"
        )
    if not isinstance(payload, Mapping):
        raise DecisionQualityIntegrityError(
            "stored decision-quality payload is not an object"
        )
    try:
        normalized = normalizer(payload)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise DecisionQualityIntegrityError(
            "stored decision-quality payload failed its immutable contract"
        ) from exc
    if canonical_json(payload) != canonical_json(normalized):
        raise DecisionQualityIntegrityError(
            "stored decision-quality payload is not canonically normalized"
        )
    for field in index_fields:
        if _quality_index_value(result, field) != normalized.get(field):
            raise DecisionQualityIntegrityError(
                f"stored decision-quality index field conflicts with payload: {field}"
            )
    result["payload"] = normalized
    for field in {"audit_eligible", "automatic_promotion_allowed"} & set(result):
        result[field] = _quality_index_value(result, field)
    return result


def _require_quality_row_tenant(
    row: Mapping[str, Any],
    *,
    expected_user_id: int,
    record_name: str,
) -> None:
    try:
        actual_user_id = _decision_quality_user_id(row.get("userId"))
    except ValueError as exc:
        raise DecisionQualityIntegrityError(
            f"stored {record_name} tenant is invalid"
        ) from exc
    if actual_user_id != expected_user_id:
        raise DecisionQualityIntegrityError(
            f"stored {record_name} crossed its tenant boundary"
        )


def _decode_input_artifact_storage_row(
    row: Mapping[str, Any],
    *,
    expected_user_id: int,
) -> dict[str, Any]:
    result = _decode_quality_row(
        row,
        normalizer=normalize_decision_quality_input_artifact,
        index_fields=_DECISION_QUALITY_ARTIFACT_INDEX_FIELDS,
    )
    _require_quality_row_tenant(
        result,
        expected_user_id=expected_user_id,
        record_name="decision-quality input artifact",
    )
    try:
        created_at = _canonical_aware_timestamp(
            result.get("created_at"), "created_at"
        )
    except ValueError as exc:
        raise DecisionQualityIntegrityError(
            "stored decision-quality input artifact created_at is invalid"
        ) from exc
    if datetime.fromisoformat(created_at) < datetime.fromisoformat(
        result["payload"]["recorded_at"]
    ):
        raise DecisionQualityIntegrityError(
            "stored decision-quality input artifact predates recorded_at"
        )
    result["created_at"] = created_at
    return result


def _decode_evaluation_snapshot_storage_row(
    row: Mapping[str, Any],
    *,
    expected_user_id: int,
) -> dict[str, Any]:
    result = _decode_quality_row(
        row,
        normalizer=normalize_decision_quality_evaluation_snapshot,
        index_fields=_DECISION_QUALITY_SNAPSHOT_INDEX_FIELDS,
    )
    _require_quality_row_tenant(
        result,
        expected_user_id=expected_user_id,
        record_name="decision-quality evaluation snapshot",
    )
    try:
        result["created_at"] = _canonical_aware_timestamp(
            result.get("created_at"), "created_at"
        )
    except ValueError as exc:
        raise DecisionQualityIntegrityError(
            "stored decision-quality evaluation snapshot created_at is invalid"
        ) from exc
    return result


def _fetch_decoded_quality_tenant_rows(
    connection: Any,
    *,
    table: str,
    identity_column: str,
    user_id: int,
    decoder: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Losslessly verify one tenant ledger before semantic selection.

    The content-addressed primary key is used only as a transport cursor.  No
    duplicated business index, clock, semantic ordering, or caller limit is
    trusted until every row in the tenant partition has passed normalization
    and payload/index binding.
    """

    count_row = _fetchone(
        connection,
        f"SELECT COUNT(*) AS row_count FROM {table} WHERE userId = ?",
        (user_id,),
    )
    try:
        expected_count = int((count_row or {})["row_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionQualityIntegrityError(
            f"could not establish a complete tenant scan for {table}"
        ) from exc

    result: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    last_identity: str | None = None
    while True:
        if last_identity is None:
            where = "userId = ?"
            params: tuple[Any, ...] = (user_id,)
        else:
            where = f"userId = ? AND {identity_column} > ?"
            params = (user_id, last_identity)
        page = _fetchall(
            connection,
            f"SELECT * FROM {table} WHERE {where} "
            f"ORDER BY {identity_column} LIMIT "
            f"{_DECISION_QUALITY_TENANT_SCAN_PAGE_SIZE}",
            params,
        )
        if not page:
            break
        for row in page:
            raw_identity = row.get(identity_column)
            if not isinstance(raw_identity, str) or not raw_identity:
                raise DecisionQualityIntegrityError(
                    f"stored {table} identity is invalid"
                )
            if raw_identity in seen_identities:
                raise DecisionQualityIntegrityError(
                    f"tenant scan for {table} returned a duplicate identity"
                )
            decoded = decoder(row)
            decoded_identity = decoded.get("payload", {}).get(identity_column)
            if decoded_identity != raw_identity:
                raise DecisionQualityIntegrityError(
                    f"stored {table} identity conflicts with its payload"
                )
            seen_identities.add(raw_identity)
            result.append(decoded)
        next_identity = page[-1].get(identity_column)
        if not isinstance(next_identity, str) or (
            last_identity is not None and next_identity <= last_identity
        ):
            raise DecisionQualityIntegrityError(
                f"tenant scan cursor did not advance for {table}"
            )
        last_identity = next_identity
        if len(page) < _DECISION_QUALITY_TENANT_SCAN_PAGE_SIZE:
            break

    final_count_row = _fetchone(
        connection,
        f"SELECT COUNT(*) AS row_count FROM {table} WHERE userId = ?",
        (user_id,),
    )
    try:
        final_count = int((final_count_row or {})["row_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionQualityIntegrityError(
            f"could not verify the completed tenant scan for {table}"
        ) from exc
    if expected_count != final_count or len(result) != expected_count:
        raise DecisionQualityIntegrityError(
            f"tenant ledger changed or was truncated while reading {table}"
        )
    return result


def _decode_artifact_receipt_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = _decode_quality_row(
        row,
        normalizer=normalize_decision_quality_artifact_receipt,
        index_fields=_DECISION_QUALITY_ARTIFACT_RECEIPT_INDEX_FIELDS,
    )
    payload = result["payload"]
    if int(result.get("userId") or 0) != int(payload["user_id"]):
        raise DecisionQualityIntegrityError(
            "stored artifact receipt tenant conflicts with payload"
        )
    try:
        created_at = _canonical_aware_timestamp(
            result.get("created_at"), "created_at"
        )
    except ValueError as exc:
        raise DecisionQualityIntegrityError(
            "stored artifact receipt created_at is invalid"
        ) from exc
    if created_at != payload["source_visible_at"]:
        raise DecisionQualityIntegrityError(
            "stored artifact receipt clock conflicts with payload"
        )
    result["created_at"] = created_at
    return result


def _decode_provider_receipt_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = _decode_quality_row(
        row,
        normalizer=normalize_decision_quality_provider_receipt,
        index_fields=_DECISION_QUALITY_PROVIDER_RECEIPT_INDEX_FIELDS,
    )
    try:
        created_at = _canonical_aware_timestamp(
            result.get("created_at"), "created_at"
        )
    except ValueError as exc:
        raise DecisionQualityIntegrityError(
            "stored provider receipt created_at is invalid"
        ) from exc
    if datetime.fromisoformat(created_at) < datetime.fromisoformat(
        result["payload"]["completed_at"]
    ):
        raise DecisionQualityIntegrityError(
            "stored provider receipt predates adapter completion"
        )
    result["created_at"] = created_at
    return result


def put_decision_quality_provider_receipt(
    *,
    receipt: Mapping[str, Any],
    connection: Any | None = None,
) -> dict[str, Any]:
    """Append one bounded primary-store provider response receipt."""

    normalized = normalize_decision_quality_provider_receipt(receipt)
    receipt_id = str(normalized["receipt_id"])
    content_hash = str(normalized["content_hash"])
    created_at = _decision_quality_storage_receipt(str(normalized["completed_at"]))
    columns = (
        *_DECISION_QUALITY_PROVIDER_RECEIPT_INDEX_FIELDS,
        "payload",
        "created_at",
    )
    values = (
        normalized["receipt_id"],
        normalized["schema_version"],
        normalized["provider"],
        normalized["operation"],
        normalized["capture_mode"],
        normalized["request_hash"],
        normalized["adapter_output_sha256"],
        normalized["adapter_output_bytes"],
        normalized["normalized_payload_hash"],
        normalized["origin_fetched_at"],
        normalized["completed_at"],
        normalized["content_hash"],
        canonical_json(normalized),
        created_at,
    )
    with _connection_scope(connection) as db:
        _require_primary_decision_quality_store(db)
        row, _ = _insert_immutable(
            db,
            table="decision_quality_provider_receipts",
            identity_where="receipt_id = ?",
            identity_params=(receipt_id,),
            columns=columns,
            values=values,
            content_hash=content_hash,
        )
        return _decode_provider_receipt_row(row)


def get_decision_quality_provider_receipt(
    *,
    receipt_id: str,
    connection: Any | None = None,
) -> dict[str, Any] | None:
    normalized_id = _decision_quality_content_id(
        receipt_id,
        name="receipt_id",
        prefix="dqpr_",
    )
    with _connection_scope(connection) as db:
        _require_primary_decision_quality_store(db)
        row = _fetchone(
            db,
            "SELECT * FROM decision_quality_provider_receipts WHERE receipt_id = ?",
            (normalized_id,),
        )
        return _decode_provider_receipt_row(row) if row is not None else None


def list_decision_quality_provider_receipts(
    *,
    provider: str | None = None,
    operation: str | None = None,
    completed_at_lte: str | datetime | None = None,
    limit: int = 500,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    clauses = ["1 = 1"]
    params: list[Any] = []
    for column, value in (("provider", provider), ("operation", operation)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(_quality_required_text(value, column))
    if completed_at_lte is not None:
        clauses.append("completed_at <= ?")
        params.append(
            _canonical_aware_timestamp(completed_at_lte, "completed_at_lte")
        )
    safe_limit = _decision_quality_limit(limit, maximum=10_000)
    with _connection_scope(connection) as db:
        _require_primary_decision_quality_store(db)
        rows = _fetchall(
            db,
            "SELECT * FROM decision_quality_provider_receipts WHERE "
            + " AND ".join(clauses)
            + " ORDER BY completed_at DESC, receipt_id"
            + f" LIMIT {safe_limit}",
            params,
        )
        return [_decode_provider_receipt_row(row) for row in rows]


def get_decision_quality_artifact_receipt(
    *,
    user_id: int,
    artifact_id: str,
    connection: Any | None = None,
) -> dict[str, Any] | None:
    normalized_user_id = _decision_quality_user_id(user_id)
    normalized_artifact_id = _decision_quality_content_id(
        artifact_id,
        name="artifact_id",
        prefix="dqa_",
    )
    with _connection_scope(connection) as db:
        _require_primary_decision_quality_store(db)
        row = _fetchone(
            db,
            "SELECT * FROM decision_quality_artifact_receipts "
            "WHERE userId = ? AND artifact_id = ?",
            (normalized_user_id, normalized_artifact_id),
        )
        return _decode_artifact_receipt_row(row) if row is not None else None


def list_decision_quality_artifact_receipts(
    *,
    user_id: int,
    artifact_type: str | None = None,
    source_visible_at_lte: str | datetime | None = None,
    limit: int = 500,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    normalized_user_id = _decision_quality_user_id(user_id)
    clauses = ["userId = ?"]
    params: list[Any] = [normalized_user_id]
    if artifact_type is not None:
        clauses.append("artifact_type = ?")
        params.append(_quality_required_text(artifact_type, "artifact_type"))
    if source_visible_at_lte is not None:
        clauses.append("source_visible_at <= ?")
        params.append(
            _canonical_aware_timestamp(
                source_visible_at_lte,
                "source_visible_at_lte",
            )
        )
    safe_limit = _decision_quality_limit(limit, maximum=10_000)
    with _connection_scope(connection) as db:
        _require_primary_decision_quality_store(db)
        rows = _fetchall(
            db,
            "SELECT * FROM decision_quality_artifact_receipts WHERE "
            + " AND ".join(clauses)
            + " ORDER BY source_visible_at DESC, artifact_id"
            + f" LIMIT {safe_limit}",
            params,
        )
        return [_decode_artifact_receipt_row(row) for row in rows]


def _default_decision_quality_connection_factory() -> Any:
    from app.config import get_settings

    settings = get_settings()
    if settings.uses_mysql:
        import pymysql

        from app.db_connect import DbConnection, _parse_mysql_url

        assert settings.database_url
        raw = pymysql.connect(
            **(
                _parse_mysql_url(settings.database_url)
                | {
                    "connect_timeout": 10,
                    "read_timeout": 30,
                    "write_timeout": 30,
                    "autocommit": False,
                }
            )
        )
        return DbConnection(raw, "mysql")

    from app.database import _connect

    return _connect()


@contextmanager
def _fresh_decision_quality_connection(
    factory: Callable[[], Any],
) -> Iterator[Any]:
    connection = factory()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _decision_quality_database_utc_now(connection: Any) -> datetime:
    if _dialect(connection) == "mysql":
        row = _fetchone(connection, "SELECT UTC_TIMESTAMP(6) AS utc_now")
    else:
        row = _fetchone(
            connection,
            "SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now') AS utc_now",
        )
    if row is None or row.get("utc_now") is None:
        raise DecisionQualityIntegrityError(
            "primary store did not return a receipt clock"
        )
    raw = row["utc_now"]
    if isinstance(raw, datetime) and (raw.tzinfo is None or raw.utcoffset() is None):
        parsed = raw.replace(tzinfo=timezone.utc)
    else:
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError as exc:
            raise DecisionQualityIntegrityError(
                "primary store returned an invalid receipt clock"
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DecisionQualityIntegrityError(
            "primary store receipt clock has no timezone"
        )
    return parsed.astimezone(timezone.utc)


def _verify_artifact_receipt_binding(
    receipt_row: Mapping[str, Any],
    *,
    source_row: Mapping[str, Any],
    user_id: int,
) -> dict[str, Any]:
    receipt = _decode_artifact_receipt_row(receipt_row)
    source = _decode_quality_row(
        source_row,
        normalizer=normalize_decision_quality_input_artifact,
        index_fields=_DECISION_QUALITY_ARTIFACT_INDEX_FIELDS,
    )
    source_payload = source["payload"]
    source_created_at = _canonical_aware_timestamp(
        source.get("created_at"), "source.created_at"
    )
    expected = (
        int(receipt.get("userId") or 0) == user_id
        and receipt["payload"]["user_id"] == user_id
        and receipt["payload"]["artifact_id"] == source_payload["artifact_id"]
        and receipt["payload"]["artifact_type"] == source_payload["artifact_type"]
        and receipt["payload"]["artifact_content_hash"]
        == source_payload["content_hash"]
        and receipt["payload"]["source_row_created_at"] == source_created_at
        and source_payload["store_authority"] == "primary"
    )
    if not expected:
        raise DecisionQualityIntegrityError(
            "artifact receipt conflicts with its immutable source"
        )
    visibility_floor = max(
        datetime.fromisoformat(source_created_at),
        datetime.fromisoformat(str(source_payload["recorded_at"])),
        datetime.fromisoformat(str(source_payload["available_at"])),
    )
    if datetime.fromisoformat(receipt["payload"]["source_visible_at"]) < visibility_floor:
        raise DecisionQualityIntegrityError(
            "artifact receipt visibility predates source evidence"
        )
    return receipt


def _finalize_decision_quality_artifact_receipt_once(
    *,
    user_id: int,
    artifact_id: str,
    connection_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Append a receipt only after a fresh transaction can see the source row."""

    normalized_user_id = _decision_quality_user_id(user_id)
    normalized_artifact_id = _decision_quality_content_id(
        artifact_id,
        name="artifact_id",
        prefix="dqa_",
    )
    factory = connection_factory or _default_decision_quality_connection_factory
    saved: dict[str, Any]
    with _fresh_decision_quality_connection(factory) as db:
        _require_primary_decision_quality_store(db)
        source = _fetchone(
            db,
            "SELECT * FROM decision_quality_input_artifacts "
            "WHERE userId = ? AND artifact_id = ?",
            (normalized_user_id, normalized_artifact_id),
        )
        if source is None:
            raise DecisionQualityIntegrityError(
                "source artifact is not committed or does not exist"
            )
        normalized_source = _decode_quality_row(
            source,
            normalizer=normalize_decision_quality_input_artifact,
            index_fields=_DECISION_QUALITY_ARTIFACT_INDEX_FIELDS,
        )
        if normalized_source["payload"]["store_authority"] != "primary":
            raise DecisionQualityPrimaryStoreUnavailable(
                "artifact receipts require a primary-store source"
            )
        if _dialect(db) == "sqlite":
            # The committed-source SELECT above does not open a Python sqlite3
            # write transaction.  Reserve the single writer before checking the
            # unique receipt identity so concurrent finalizers serialize.
            _execute(db, "BEGIN IMMEDIATE")
        existing = _fetchone(
            db,
            "SELECT * FROM decision_quality_artifact_receipts "
            "WHERE userId = ? AND artifact_id = ?",
            (normalized_user_id, normalized_artifact_id),
        )
        if existing is not None:
            saved = _verify_artifact_receipt_binding(
                existing,
                source_row=normalized_source,
                user_id=normalized_user_id,
            )
        else:
            source_created_at = _canonical_aware_timestamp(
                normalized_source.get("created_at"), "source.created_at"
            )
            source_payload = normalized_source["payload"]
            source_visible_at = max(
                _decision_quality_database_utc_now(db),
                datetime.fromisoformat(source_created_at),
                datetime.fromisoformat(str(source_payload["recorded_at"])),
                datetime.fromisoformat(str(source_payload["available_at"])),
            ).isoformat()
            normalized_receipt = normalize_decision_quality_artifact_receipt(
                {
                    "user_id": normalized_user_id,
                    "artifact_id": normalized_artifact_id,
                    "artifact_type": source_payload["artifact_type"],
                    "artifact_content_hash": source_payload["content_hash"],
                    "source_row_created_at": source_created_at,
                    "source_visible_at": source_visible_at,
                    "store_authority": "primary",
                }
            )
            columns = (
                "userId",
                *_DECISION_QUALITY_ARTIFACT_RECEIPT_INDEX_FIELDS,
                "payload",
                "created_at",
            )
            values = (
                normalized_user_id,
                normalized_receipt["receipt_id"],
                normalized_receipt["schema_version"],
                normalized_receipt["receipt_policy"],
                normalized_receipt["artifact_id"],
                normalized_receipt["artifact_type"],
                normalized_receipt["artifact_content_hash"],
                normalized_receipt["source_row_created_at"],
                normalized_receipt["source_visible_at"],
                normalized_receipt["store_authority"],
                normalized_receipt["content_hash"],
                canonical_json(normalized_receipt),
                source_visible_at,
            )
            try:
                _execute(
                    db,
                    "INSERT INTO decision_quality_artifact_receipts "
                    f"({', '.join(columns)}) VALUES "
                    f"({', '.join('?' for _ in columns)})",
                    values,
                )
                row = _fetchone(
                    db,
                    "SELECT * FROM decision_quality_artifact_receipts "
                    "WHERE userId = ? AND artifact_id = ?",
                    (normalized_user_id, normalized_artifact_id),
                )
            except Exception:
                lock_suffix = " FOR UPDATE" if _dialect(db) == "mysql" else ""
                row = _fetchone(
                    db,
                    "SELECT * FROM decision_quality_artifact_receipts "
                    "WHERE userId = ? AND artifact_id = ?" + lock_suffix,
                    (normalized_user_id, normalized_artifact_id),
                )
                if row is None:
                    raise
            assert row is not None
            saved = _verify_artifact_receipt_binding(
                row,
                source_row=normalized_source,
                user_id=normalized_user_id,
            )
    return saved


def finalize_decision_quality_artifact_receipt(
    *,
    user_id: int,
    artifact_id: str,
    connection_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Finalize once, then resolve a rolled-back unique/lock race afresh."""

    factory = connection_factory or _default_decision_quality_connection_factory
    try:
        return _finalize_decision_quality_artifact_receipt_once(
            user_id=user_id,
            artifact_id=artifact_id,
            connection_factory=factory,
        )
    except Exception as original:
        normalized_user_id = _decision_quality_user_id(user_id)
        normalized_artifact_id = _decision_quality_content_id(
            artifact_id,
            name="artifact_id",
            prefix="dqa_",
        )
        # SQLite read snapshots cannot necessarily observe the concurrent winner
        # after an INSERT lock/unique error.  Re-open after rollback; MySQL also
        # benefits from resolving a lost response on a clean transaction.
        with _fresh_decision_quality_connection(factory) as db:
            _require_primary_decision_quality_store(db)
            source = _fetchone(
                db,
                "SELECT * FROM decision_quality_input_artifacts "
                "WHERE userId = ? AND artifact_id = ?",
                (normalized_user_id, normalized_artifact_id),
            )
            receipt = _fetchone(
                db,
                "SELECT * FROM decision_quality_artifact_receipts "
                "WHERE userId = ? AND artifact_id = ?",
                (normalized_user_id, normalized_artifact_id),
            )
            if source is None or receipt is None:
                raise original
            return _verify_artifact_receipt_binding(
                receipt,
                source_row=source,
                user_id=normalized_user_id,
            )


def reconcile_decision_quality_artifact_receipts(
    *,
    user_id: int | None = None,
    limit: int = 500,
    connection_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Finalize primary artifacts missing receipts, isolating per-row failures."""

    normalized_user_id = (
        _decision_quality_user_id(user_id) if user_id is not None else None
    )
    safe_limit = _decision_quality_limit(limit, maximum=10_000)
    factory = connection_factory or _default_decision_quality_connection_factory
    clauses = ["a.store_authority = 'primary'", "r.artifact_id IS NULL"]
    params: list[Any] = []
    if normalized_user_id is not None:
        clauses.append("a.userId = ?")
        params.append(normalized_user_id)
    with _fresh_decision_quality_connection(factory) as db:
        _require_primary_decision_quality_store(db)
        targets = _fetchall(
            db,
            "SELECT a.userId, a.artifact_id "
            "FROM decision_quality_input_artifacts a "
            "LEFT JOIN decision_quality_artifact_receipts r "
            "ON r.userId = a.userId AND r.artifact_id = a.artifact_id "
            "WHERE "
            + " AND ".join(clauses)
            + " ORDER BY a.recorded_at, a.userId, a.artifact_id"
            + f" LIMIT {safe_limit}",
            params,
        )
    finalized: list[str] = []
    failed: list[dict[str, Any]] = []
    for target in targets:
        target_user_id = int(target["userId"])
        target_artifact_id = str(target["artifact_id"])
        try:
            finalize_decision_quality_artifact_receipt(
                user_id=target_user_id,
                artifact_id=target_artifact_id,
                connection_factory=factory,
            )
            finalized.append(target_artifact_id)
        except Exception as exc:  # noqa: BLE001 - isolate corrupt tenants/rows
            failed.append(
                {
                    "user_id": target_user_id,
                    "artifact_id": target_artifact_id,
                    "error_type": type(exc).__name__,
                }
            )
    return {
        "status": "completed" if not failed else "completed_with_failures",
        "scanned_count": len(targets),
        "finalized_count": len(finalized),
        "failed_count": len(failed),
        "finalized_artifact_ids": finalized,
        "failures": failed,
    }


def put_decision_quality_input_artifact(
    *,
    user_id: int,
    artifact: Mapping[str, Any],
    connection: Any | None = None,
) -> dict[str, Any]:
    """Append one exact evaluation input, or return its content-identical twin."""

    normalized_user_id = _decision_quality_user_id(user_id)
    normalized = normalize_decision_quality_input_artifact(artifact)
    artifact_id = str(normalized["artifact_id"])
    content_hash = str(normalized["content_hash"])
    created_at = _decision_quality_storage_receipt(str(normalized["recorded_at"]))
    columns = (
        "userId",
        *_DECISION_QUALITY_ARTIFACT_INDEX_FIELDS,
        "payload",
        "created_at",
    )
    values = (
        normalized_user_id,
        normalized["artifact_id"],
        normalized["schema_version"],
        normalized["artifact_type"],
        normalized["artifact_schema_version"],
        normalized.get("logical_key"),
        normalized["source_type"],
        normalized["source_report_id"],
        normalized["decision_event_id"],
        normalized["decision_at"],
        normalized["available_at"],
        normalized["recorded_at"],
        normalized["store_authority"],
        int(bool(normalized["audit_eligible"])),
        content_hash,
        canonical_json(normalized),
        created_at,
    )
    with _connection_scope(connection) as db:
        actual_authority = _decision_quality_store_authority(db)
        if normalized["store_authority"] != actual_authority:
            raise ValueError(
                "artifact store_authority conflicts with the active evidence store"
            )
        logical_key = normalized.get("logical_key")
        identity_where = "userId = ? AND artifact_id = ?"
        identity_params: tuple[object, ...] = (normalized_user_id, artifact_id)
        if logical_key is not None:
            identity_where = (
                "userId = ? AND artifact_type = ? AND logical_key = ?"
            )
            identity_params = (
                normalized_user_id,
                normalized["artifact_type"],
                logical_key,
            )
        row, _ = _insert_immutable(
            db,
            table="decision_quality_input_artifacts",
            identity_where=identity_where,
            identity_params=identity_params,
            columns=columns,
            values=values,
            content_hash=content_hash,
        )
        return _decode_input_artifact_storage_row(
            row,
            expected_user_id=normalized_user_id,
        )


def get_decision_quality_input_artifact(
    *, user_id: int, artifact_id: str, connection: Any | None = None
) -> dict[str, Any] | None:
    normalized_user_id = _decision_quality_user_id(user_id)
    normalized_artifact_id = _decision_quality_content_id(
        artifact_id,
        name="artifact_id",
        prefix="dqa_",
    )
    with _connection_scope(connection) as db:
        row = _fetchone(
            db,
            "SELECT * FROM decision_quality_input_artifacts "
            "WHERE userId = ? AND artifact_id = ?",
            (normalized_user_id, normalized_artifact_id),
        )
        return (
            _decode_input_artifact_storage_row(
                row,
                expected_user_id=normalized_user_id,
            )
            if row is not None
            else None
        )


def list_decision_quality_input_artifacts(
    *,
    user_id: int,
    artifact_type: str | None = None,
    source_type: str | None = None,
    source_report_id: str | None = None,
    decision_event_id: str | None = None,
    audit_eligible_only: bool = False,
    recorded_at_lte: str | datetime | None = None,
    limit: int | None = 500,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    normalized_user_id = _decision_quality_user_id(user_id)
    normalized_eligible_only = _required_boolean(
        audit_eligible_only, "audit_eligible_only"
    )
    normalized_filters: dict[str, str] = {}
    for column, value in (
        ("artifact_type", artifact_type),
        ("source_type", source_type),
        ("source_report_id", source_report_id),
        ("decision_event_id", decision_event_id),
    ):
        if value is not None:
            normalized_filters[column] = _quality_required_text(value, column)
    normalized_cutoff = (
        _canonical_aware_timestamp(recorded_at_lte, "recorded_at_lte")
        if recorded_at_lte is not None
        else None
    )
    safe_limit = (
        None
        if limit is None
        else _decision_quality_limit(limit, maximum=10_000)
    )
    with _connection_scope(connection) as db:
        if normalized_eligible_only:
            _require_primary_decision_quality_store(db)
        rows = _fetch_decoded_quality_tenant_rows(
            db,
            table="decision_quality_input_artifacts",
            identity_column="artifact_id",
            user_id=normalized_user_id,
            decoder=lambda row: _decode_input_artifact_storage_row(
                row,
                expected_user_id=normalized_user_id,
            ),
        )
        selected = [
            row
            for row in rows
            if all(
                row["payload"].get(column) == value
                for column, value in normalized_filters.items()
            )
            and (
                not normalized_eligible_only
                or row["payload"].get("audit_eligible") is True
            )
            and (
                normalized_cutoff is None
                or datetime.fromisoformat(row["payload"]["recorded_at"])
                <= datetime.fromisoformat(normalized_cutoff)
            )
        ]
        selected.sort(key=lambda row: str(row["payload"]["artifact_id"]))
        selected.sort(
            key=lambda row: datetime.fromisoformat(
                row["payload"]["recorded_at"]
            ),
            reverse=True,
        )
        return selected if safe_limit is None else selected[:safe_limit]


def put_decision_quality_evaluation_snapshot(
    *,
    user_id: int,
    snapshot: Mapping[str, Any],
    connection: Any | None = None,
) -> dict[str, Any]:
    """Append a hash-complete D1 result; automatic promotion is never accepted."""

    normalized_user_id = _decision_quality_user_id(user_id)
    normalized = normalize_decision_quality_evaluation_snapshot(snapshot)
    snapshot_id = str(normalized["snapshot_id"])
    content_hash = str(normalized["content_hash"])
    created_at = _utc_now()
    columns = (
        "userId",
        *_DECISION_QUALITY_SNAPSHOT_INDEX_FIELDS,
        "payload",
        "created_at",
    )
    values = (
        normalized_user_id,
        normalized["snapshot_id"],
        normalized["schema_version"],
        normalized["evaluation_as_of"],
        normalized["evaluator_schema_version"],
        normalized["evaluator_version"],
        normalized["status"],
        normalized["evaluation_hash"],
        normalized["input_manifest_hash"],
        normalized["config_hash"],
        normalized["readiness_status"],
        normalized["human_review_status"],
        0,
        normalized["store_authority"],
        1,
        content_hash,
        canonical_json(normalized),
        created_at,
    )
    with _connection_scope(connection) as db:
        _require_primary_decision_quality_store(db)
        current_rollout_marker = get_decision_quality_contract_rollout(
            connection=db
        )
        snapshot_rollout_marker = normalized["input_manifest"].get(
            "contract_rollout_marker"
        )
        if snapshot_rollout_marker != current_rollout_marker:
            raise DecisionQualityIntegrityError(
                "evaluation snapshot rollout marker does not match the primary store"
            )
        row, _ = _insert_immutable(
            db,
            table="decision_quality_evaluation_snapshots",
            identity_where="userId = ? AND snapshot_id = ?",
            identity_params=(normalized_user_id, snapshot_id),
            columns=columns,
            values=values,
            content_hash=content_hash,
        )
        return _decode_evaluation_snapshot_storage_row(
            row,
            expected_user_id=normalized_user_id,
        )


def get_decision_quality_evaluation_snapshot(
    *, user_id: int, snapshot_id: str, connection: Any | None = None
) -> dict[str, Any] | None:
    normalized_user_id = _decision_quality_user_id(user_id)
    normalized_snapshot_id = _decision_quality_content_id(
        snapshot_id,
        name="snapshot_id",
        prefix="dqs_",
    )
    with _connection_scope(connection) as db:
        _require_primary_decision_quality_store(db)
        row = _fetchone(
            db,
            "SELECT * FROM decision_quality_evaluation_snapshots "
            "WHERE userId = ? AND snapshot_id = ?",
            (normalized_user_id, normalized_snapshot_id),
        )
        return (
            _decode_evaluation_snapshot_storage_row(
                row,
                expected_user_id=normalized_user_id,
            )
            if row is not None
            else None
        )


def list_decision_quality_evaluation_snapshots(
    *,
    user_id: int,
    status: str | None = None,
    readiness_status: str | None = None,
    human_review_status: str | None = None,
    evaluation_as_of_lte: str | datetime | None = None,
    limit: int = 100,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    normalized_user_id = _decision_quality_user_id(user_id)
    normalized_status: str | None = None
    normalized_review: str | None = None
    normalized_readiness: str | None = None
    if status is not None:
        normalized_status = _quality_required_text(status, "status")
        if normalized_status not in DECISION_QUALITY_EVALUATION_STATUSES:
            raise ValueError("status is unsupported")
    if human_review_status is not None:
        normalized_review = _quality_required_text(
            human_review_status, "human_review_status"
        )
        if normalized_review not in DECISION_QUALITY_HUMAN_REVIEW_STATUSES:
            raise ValueError("human_review_status is unsupported")
    if readiness_status is not None:
        normalized_readiness = _quality_required_text(
            readiness_status, "readiness_status"
        )
        if normalized_readiness not in DECISION_QUALITY_READINESS_STATUSES:
            raise ValueError("readiness_status is unsupported")
    normalized_cutoff = (
        _canonical_aware_timestamp(
            evaluation_as_of_lte, "evaluation_as_of_lte"
        )
        if evaluation_as_of_lte is not None
        else None
    )
    safe_limit = _decision_quality_limit(limit, maximum=10_000)
    with _connection_scope(connection) as db:
        _require_primary_decision_quality_store(db)
        rows = _fetch_decoded_quality_tenant_rows(
            db,
            table="decision_quality_evaluation_snapshots",
            identity_column="snapshot_id",
            user_id=normalized_user_id,
            decoder=lambda row: _decode_evaluation_snapshot_storage_row(
                row,
                expected_user_id=normalized_user_id,
            ),
        )
        selected = [
            row
            for row in rows
            if (normalized_status is None or row["payload"]["status"] == normalized_status)
            and (
                normalized_review is None
                or row["payload"]["human_review_status"] == normalized_review
            )
            and (
                normalized_readiness is None
                or row["payload"]["readiness_status"] == normalized_readiness
            )
            and (
                normalized_cutoff is None
                or datetime.fromisoformat(row["payload"]["evaluation_as_of"])
                <= datetime.fromisoformat(normalized_cutoff)
            )
        ]
        selected.sort(key=lambda row: str(row["payload"]["snapshot_id"]))
        selected.sort(
            key=lambda row: datetime.fromisoformat(row["created_at"]),
            reverse=True,
        )
        selected.sort(
            key=lambda row: datetime.fromisoformat(
                row["payload"]["evaluation_as_of"]
            ),
            reverse=True,
        )
        return selected[:safe_limit]


def get_portfolio_ledger_head(
    *, user_id: int, account_id: str = "default", connection: Any | None = None
) -> dict[str, Any]:
    with _connection_scope(connection) as db:
        row = _fetchone(
            db,
            "SELECT * FROM portfolio_ledger_heads WHERE userId = ? AND account_id = ?",
            (int(user_id), account_id),
        )
        return row or {
            "userId": int(user_id),
            "account_id": account_id,
            "revision": 0,
            "chain_hash": "",
            "updated_at": None,
        }


def _cas_ledger_head(
    connection: Any,
    *,
    user_id: int,
    account_id: str,
    expected_revision: int,
    expected_chain_hash: str,
    new_revision: int,
    new_chain_hash: str,
    updated_at: str,
) -> bool:
    if new_revision != expected_revision + 1:
        raise ValueError("new ledger revision must be expected_revision + 1")
    if expected_revision == 0:
        if expected_chain_hash:
            return False
        existing = _fetchone(
            connection,
            "SELECT revision, chain_hash FROM portfolio_ledger_heads "
            "WHERE userId = ? AND account_id = ?",
            (int(user_id), account_id),
        )
        if existing is not None:
            return False
        try:
            _execute(
                connection,
                "INSERT INTO portfolio_ledger_heads "
                "(userId, account_id, revision, chain_hash, updated_at) VALUES (?, ?, ?, ?, ?)",
                (int(user_id), account_id, new_revision, new_chain_hash, updated_at),
            )
            return True
        except Exception:
            raced = _fetchone(
                connection,
                "SELECT revision, chain_hash FROM portfolio_ledger_heads "
                "WHERE userId = ? AND account_id = ?",
                (int(user_id), account_id),
            )
            if raced is not None:
                return False
            raise

    cursor = _execute(
        connection,
        """
        UPDATE portfolio_ledger_heads
        SET revision = ?, chain_hash = ?, updated_at = ?
        WHERE userId = ? AND account_id = ? AND revision = ? AND chain_hash = ?
        """,
        (
            new_revision,
            new_chain_hash,
            updated_at,
            int(user_id),
            account_id,
            expected_revision,
            expected_chain_hash,
        ),
    )
    return int(getattr(cursor, "rowcount", 0) or 0) == 1


def compare_and_set_portfolio_ledger_head(
    *,
    user_id: int,
    account_id: str = "default",
    expected_revision: int,
    expected_chain_hash: str,
    new_revision: int,
    new_chain_hash: str,
    connection: Any | None = None,
) -> bool:
    with _connection_scope(connection) as db:
        return _cas_ledger_head(
            db,
            user_id=int(user_id),
            account_id=account_id,
            expected_revision=int(expected_revision),
            expected_chain_hash=expected_chain_hash,
            new_revision=int(new_revision),
            new_chain_hash=new_chain_hash,
            updated_at=_utc_now(),
        )


def _ledger_event_material(
    *, user_id: int, account_id: str, event: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "user_id": int(user_id),
        "account_id": account_id,
        **_record_material(
            event,
            omit={"event_revision_id", "event_hash", "payload_hash", "previous_hash"},
        ),
    }


def append_portfolio_ledger_event(
    *,
    user_id: int,
    event: Mapping[str, Any],
    account_id: str = "default",
    expected_head_revision: int | None = None,
    expected_head_hash: str | None = None,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Atomically append one immutable event and advance the account hash chain."""
    logical_event_id = _required_text(event.get("logical_event_id"), "logical_event_id")
    event_type = _required_text(event.get("event_type"), "event_type")
    effective_at = _required_text(event.get("effective_at"), "effective_at")
    # Append-only identities must be reproducible. Operational timestamps and
    # state therefore cannot be silently defaulted after hashing.
    recorded_at = _required_text(event.get("recorded_at"), "recorded_at")
    status = _required_text(event.get("status"), "status")
    source = _required_text(event.get("source"), "source")
    try:
        logical_revision = int(event.get("revision_no", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("revision_no must be a positive integer") from exc
    if logical_revision <= 0:
        raise ValueError("revision_no must be a positive integer")

    normalized_event = dict(event)
    normalized_event.update(
        {
            "logical_event_id": logical_event_id,
            "revision_no": logical_revision,
            "event_type": event_type,
            "effective_at": effective_at,
            "recorded_at": recorded_at,
            "status": status,
            "source": source,
        }
    )
    payload_value = normalized_event.get("payload", normalized_event)
    payload = canonical_json(payload_value)
    payload_hash = canonical_hash(_ledger_event_material(
        user_id=int(user_id), account_id=account_id, event=normalized_event
    ))
    event_revision_id = _optional_text(normalized_event.get("event_revision_id")) or (
        "ler_"
        + hashlib.sha256(
            f"{user_id}|{account_id}|{logical_event_id}|{logical_revision}|{payload_hash}".encode(
                "utf-8"
            )
        ).hexdigest()[:32]
    )
    now = _utc_now()

    with _connection_scope(connection) as db:
        _execute(db, "SAVEPOINT decision_repository_ledger_append")
        try:
            existing_id = _fetchone(
                db,
                "SELECT * FROM portfolio_ledger_events WHERE event_revision_id = ?",
                (event_revision_id,),
            )
            if existing_id is not None:
                if (
                    int(existing_id.get("userId") or 0) == int(user_id)
                    and existing_id.get("payload_hash") == payload_hash
                ):
                    _execute(db, "RELEASE SAVEPOINT decision_repository_ledger_append")
                    return _decode_row(existing_id) or existing_id
                raise ImmutableRecordConflict(
                    "event_revision_id already exists with different ledger content"
                )

            source_ref = _optional_text(normalized_event.get("source_ref"))
            if source_ref:
                existing_source = _fetchone(
                    db,
                    "SELECT * FROM portfolio_ledger_events WHERE "
                    "userId = ? AND account_id = ? AND source = ? AND source_ref = ?",
                    (int(user_id), account_id, source, source_ref),
                )
                if existing_source is not None:
                    if existing_source.get("payload_hash") == payload_hash:
                        _execute(db, "RELEASE SAVEPOINT decision_repository_ledger_append")
                        return _decode_row(existing_source) or existing_source
                    raise ImmutableRecordConflict(
                        "source_ref already exists with different ledger content"
                    )

            lock_suffix = " FOR UPDATE" if _dialect(db) == "mysql" else ""
            head = _fetchone(
                db,
                "SELECT * FROM portfolio_ledger_heads WHERE userId = ? AND account_id = ?"
                + lock_suffix,
                (int(user_id), account_id),
            ) or {"revision": 0, "chain_hash": ""}
            head_revision = int(head.get("revision") or 0)
            head_hash = str(head.get("chain_hash") or "")
            if expected_head_revision is not None and head_revision != int(expected_head_revision):
                raise LedgerHeadConflict(
                    f"ledger revision changed: expected {expected_head_revision}, got {head_revision}"
                )
            if expected_head_hash is not None and head_hash != expected_head_hash:
                raise LedgerHeadConflict("ledger chain hash changed")
            supplied_previous = _optional_text(normalized_event.get("previous_hash"))
            if supplied_previous is not None and supplied_previous != head_hash:
                raise LedgerHeadConflict("event previous_hash does not match ledger head")

            event_hash = hashlib.sha256(
                f"{head_hash}|{payload_hash}|{event_revision_id}".encode("utf-8")
            ).hexdigest()
            _execute(
                db,
                """
                INSERT INTO portfolio_ledger_events (
                    event_revision_id, logical_event_id, userId, account_id,
                    revision_no, event_type, fund_code, effective_at, recorded_at,
                    status, source, source_ref, event_hash, previous_hash,
                    payload_hash, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_revision_id,
                    logical_event_id,
                    int(user_id),
                    account_id,
                    logical_revision,
                    event_type,
                    _optional_text(normalized_event.get("fund_code")),
                    effective_at,
                    recorded_at,
                    status,
                    source,
                    source_ref,
                    event_hash,
                    head_hash or None,
                    payload_hash,
                    payload,
                    now,
                ),
            )
            if not _cas_ledger_head(
                db,
                user_id=int(user_id),
                account_id=account_id,
                expected_revision=head_revision,
                expected_chain_hash=head_hash,
                new_revision=head_revision + 1,
                new_chain_hash=event_hash,
                updated_at=now,
            ):
                raise LedgerHeadConflict("ledger head compare-and-set failed")
            _execute(db, "RELEASE SAVEPOINT decision_repository_ledger_append")
        except Exception:
            _execute(db, "ROLLBACK TO SAVEPOINT decision_repository_ledger_append")
            _execute(db, "RELEASE SAVEPOINT decision_repository_ledger_append")
            raise

        inserted = _fetchone(
            db,
            "SELECT * FROM portfolio_ledger_events WHERE event_revision_id = ?",
            (event_revision_id,),
        )
        assert inserted is not None
        return _decode_row(inserted) or inserted


def list_portfolio_ledger_events(
    *,
    user_id: int,
    account_id: str = "default",
    fund_code: str | None = None,
    effective_at_lte: str | None = None,
    recorded_at_lte: str | None = None,
    limit: int = 10_000,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    clauses = ["userId = ?", "account_id = ?"]
    params: list[Any] = [int(user_id), account_id]
    if fund_code:
        clauses.append("fund_code = ?")
        params.append(fund_code)
    if effective_at_lte:
        clauses.append("effective_at <= ?")
        params.append(effective_at_lte)
    if recorded_at_lte:
        clauses.append("recorded_at <= ?")
        params.append(recorded_at_lte)
    safe_limit = max(1, min(int(limit), 100_000))
    with _connection_scope(connection) as db:
        rows = _fetchall(
            db,
            "SELECT * FROM portfolio_ledger_events WHERE "
            + " AND ".join(clauses)
            + " ORDER BY effective_at, recorded_at, event_revision_id"
            + f" LIMIT {safe_limit}",
            params,
        )
        return [_decode_row(row) or row for row in rows]


__all__ = [
    "DECISION_QUALITY_ARTIFACT_RECEIPT_POLICY",
    "DECISION_QUALITY_ARTIFACT_RECEIPT_SCHEMA_VERSION",
    "DECISION_QUALITY_EVALUATION_SCHEMA_VERSION",
    "DECISION_QUALITY_EVALUATION_SNAPSHOT_SCHEMA_VERSION",
    "DECISION_QUALITY_EVALUATION_STATUSES",
    "DECISION_QUALITY_HUMAN_REVIEW_STATUSES",
    "DECISION_QUALITY_INPUT_ARTIFACT_SCHEMA_VERSION",
    "DECISION_QUALITY_PROVIDER_ADAPTER_OUTPUT_MAX_BYTES",
    "DECISION_QUALITY_PROVIDER_RECEIPT_SCHEMA_VERSION",
    "DECISION_QUALITY_READINESS_STATUSES",
    "DecisionQualityIntegrityError",
    "DecisionQualityPrimaryStoreUnavailable",
    "DecisionRepositoryError",
    "ImmutableRecordConflict",
    "LedgerHeadConflict",
    "ObservationFinalizedConflict",
    "append_portfolio_ledger_event",
    "canonical_hash",
    "canonical_json",
    "compare_and_set_portfolio_ledger_head",
    "decision_event_content_hash",
    "decision_portfolio_snapshot_content_hash",
    "get_decision_event",
    "get_decision_portfolio_snapshot",
    "get_decision_quality_contract_rollout",
    "get_decision_quality_artifact_receipt",
    "get_decision_quality_evaluation_snapshot",
    "get_decision_quality_input_artifact",
    "get_decision_quality_provider_receipt",
    "get_effective_fund_benchmark_mapping",
    "get_outcome_observation",
    "get_portfolio_ledger_head",
    "list_decision_events",
    "list_decision_quality_evaluation_snapshots",
    "list_decision_quality_artifact_receipts",
    "list_decision_quality_input_artifacts",
    "list_decision_quality_provider_receipts",
    "list_effective_fund_benchmark_mappings",
    "list_outcome_observation_revisions",
    "list_outcome_observations",
    "list_portfolio_ledger_events",
    "normalize_decision_event",
    "normalize_decision_portfolio_snapshot",
    "normalize_decision_quality_evaluation_snapshot",
    "normalize_decision_quality_artifact_receipt",
    "normalize_decision_quality_input_artifact",
    "normalize_decision_quality_provider_receipt",
    "put_decision_event",
    "put_decision_portfolio_snapshot",
    "put_decision_quality_evaluation_snapshot",
    "put_decision_quality_input_artifact",
    "put_decision_quality_provider_receipt",
    "put_fund_benchmark_mapping",
    "save_decision_event",
    "save_decision_portfolio_snapshot",
    "save_fund_benchmark_mapping",
    "save_outcome_observation",
    "finalize_decision_quality_artifact_receipt",
    "reconcile_decision_quality_artifact_receipts",
    "upsert_outcome_observation",
]
