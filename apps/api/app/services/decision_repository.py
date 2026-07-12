from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterator, Mapping, Sequence


class DecisionRepositoryError(RuntimeError):
    """Base error for durable decision evidence."""


class ImmutableRecordConflict(DecisionRepositoryError):
    """An immutable identifier was reused for different content."""


class ObservationFinalizedConflict(ImmutableRecordConflict):
    """A terminal observation was recomputed with different evidence."""


class LedgerHeadConflict(DecisionRepositoryError):
    """The append-only ledger head changed before an event could be appended."""


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
    return _row_dict(cursor, cursor.fetchone())


def _fetchall(connection: Any, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    cursor = _execute(connection, sql, params)
    return [row for raw in cursor.fetchall() if (row := _row_dict(cursor, raw)) is not None]


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
    lock_suffix = " FOR UPDATE" if _dialect(connection) == "mysql" else ""
    existing = _fetchone(
        connection,
        f"SELECT * FROM {table} WHERE {identity_where}{lock_suffix}",
        identity_params,
    )
    if existing is not None:
        if existing.get("content_hash") != content_hash:
            raise ImmutableRecordConflict(
                f"{table} identity already exists with different immutable content"
            )
        return _decode_row(existing) or existing, False

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
            f"SELECT * FROM {table} WHERE {identity_where}{lock_suffix}",
            identity_params,
        )
        if raced is None:
            raise
        if raced.get("content_hash") != content_hash:
            raise ImmutableRecordConflict(
                f"{table} identity was concurrently written with different content"
            )
        return _decode_row(raced) or raced, False

    inserted = _fetchone(
        connection,
        f"SELECT * FROM {table} WHERE {identity_where}",
        identity_params,
    )
    assert inserted is not None
    return _decode_row(inserted) or inserted, True


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


def put_decision_event(
    *,
    user_id: int,
    event: Mapping[str, Any],
    connection: Any | None = None,
) -> dict[str, Any]:
    """Insert the final, guarded recommendation as immutable evidence."""
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
    created_at = _utc_now()
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
    with _connection_scope(connection) as db:
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
    "get_effective_fund_benchmark_mapping",
    "get_outcome_observation",
    "get_portfolio_ledger_head",
    "list_decision_events",
    "list_effective_fund_benchmark_mappings",
    "list_outcome_observation_revisions",
    "list_outcome_observations",
    "list_portfolio_ledger_events",
    "normalize_decision_event",
    "normalize_decision_portfolio_snapshot",
    "put_decision_event",
    "put_decision_portfolio_snapshot",
    "put_fund_benchmark_mapping",
    "save_decision_event",
    "save_decision_portfolio_snapshot",
    "save_fund_benchmark_mapping",
    "save_outcome_observation",
    "upsert_outcome_observation",
]
