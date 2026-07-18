from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Mapping

from app.config import get_settings
from app.db_migrations import SCHEMA_VERSION, run_migrations
from app.request_context import get_request_user_id
from app.models import (
    ChatMessage,
    DiscoveryChatMessage,
    FundDiscoveryReport,
    FundProfile,
    FundTransaction,
    InvestorProfile,
    PortfolioDailySnapshot,
    PortfolioSummary,
    Report,
)


_SQLITE_SCHEMA_CACHE_MAX_PATHS = 32
_SqliteSchemaIdentity = tuple[str, int, int, int, int]
_SQLITE_SCHEMA_INIT_LOCK = RLock()
_SQLITE_SCHEMA_INIT_CACHE: OrderedDict[str, _SqliteSchemaIdentity] = OrderedDict()

_FUND_HOLDINGS_SNAPSHOT_SCHEMA = "fund_holdings_snapshot.v1"
_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
logger = logging.getLogger(__name__)


def _db_path() -> Path:
    override = os.getenv("FUND_AI_DB_PATH")
    if override:
        return Path(override)
    return get_settings().db_path


def _uid() -> int:
    return get_request_user_id()


def _row_to_dict(row: object) -> dict[str, object]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row)


def _sqlite_path_cache_key(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve(strict=False)))


def _sqlite_schema_identity(
    path: Path,
    connection: sqlite3.Connection,
) -> _SqliteSchemaIdentity:
    resolved = path.expanduser().resolve(strict=False)
    cache_key = os.path.normcase(str(resolved))
    stat = resolved.stat()
    row = connection.execute("PRAGMA schema_version").fetchone()
    schema_version = int(row[0]) if row is not None else 0
    return (
        cache_key,
        int(stat.st_dev),
        int(stat.st_ino),
        schema_version,
        int(SCHEMA_VERSION),
    )


def _clear_sqlite_schema_init_cache(path: Path | None = None) -> None:
    """Invalidate the process-local SQLite bootstrap memo.

    Tests that monkeypatch the bootstrap implementation for an already-opened
    path can clear a single entry. Runtime database import uses the same hook
    because its in-place copy deliberately preserves the target inode.
    """

    with _SQLITE_SCHEMA_INIT_LOCK:
        if path is None:
            _SQLITE_SCHEMA_INIT_CACHE.clear()
            return
        _SQLITE_SCHEMA_INIT_CACHE.pop(_sqlite_path_cache_key(path), None)


def _bootstrap_sqlite_schema(connection: sqlite3.Connection) -> None:
    """Run the historical SQLite bootstrap and migrations unchanged."""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fund_profiles (
            fund_code TEXT PRIMARY KEY,
            fund_name TEXT NOT NULL,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS ocr_text_cache (
            cache_key TEXT PRIMARY KEY,
            raw_text TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_daily_snapshots (
            snapshot_date TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_intraday_curves (
            trade_date TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS report_chat_messages (
            id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_report_chat_report_id
        ON report_chat_messages (report_id, created_at)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sector_mappings (
            sector_label TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_code TEXT,
            source_name TEXT NOT NULL,
            confidence TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS investor_profile_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS fund_transactions (
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
            confirmed_shares REAL,
            fee_yuan REAL,
            shares_source TEXT,
            in_progress INTEGER NOT NULL DEFAULT 0,
            confirmed_at TEXT,
            dedup_key TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_fund_tx_dedup
        ON fund_transactions (userId, dedup_key)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_fund_tx_fund
        ON fund_transactions (userId, fund_code)
        """
    )
    run_migrations(connection)


def _ensure_sqlite_schema_initialized(
    path: Path,
    connection: sqlite3.Connection,
) -> None:
    with _SQLITE_SCHEMA_INIT_LOCK:
        # Identity reads share the import/bootstrap lock, so a connection never
        # inspects a file while an in-process import is replacing its contents.
        current_identity = _sqlite_schema_identity(path, connection)
        cache_key = current_identity[0]
        cached_identity = _SQLITE_SCHEMA_INIT_CACHE.get(cache_key)
        if cached_identity == current_identity:
            _SQLITE_SCHEMA_INIT_CACHE.move_to_end(cache_key)
            return

        _SQLITE_SCHEMA_INIT_CACHE.pop(cache_key, None)
        try:
            _bootstrap_sqlite_schema(connection)
            connection.commit()
            initialized_identity = _sqlite_schema_identity(path, connection)
        except Exception:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise

        _SQLITE_SCHEMA_INIT_CACHE[cache_key] = initialized_identity
        _SQLITE_SCHEMA_INIT_CACHE.move_to_end(cache_key)
        while len(_SQLITE_SCHEMA_INIT_CACHE) > _SQLITE_SCHEMA_CACHE_MAX_PATHS:
            _SQLITE_SCHEMA_INIT_CACHE.popitem(last=False)


def _connect():
    from app.db_connect import DbConnection, connect, uses_mysql

    if uses_mysql():
        candidate = connect()
        if str(getattr(candidate, "dialect", "")) == "mysql":
            return candidate
        # MySQL may be configured while ``connect`` returns the explicitly
        # enabled local SQLite fallback.  That fallback still needs the same
        # bootstrap and migrations as a normal SQLite deployment; otherwise a
        # fresh outage database has no reports/decision tables at all.
        candidate.close()
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        _ensure_sqlite_schema_initialized(path, connection)
    except Exception:
        connection.close()
        raise
    return DbConnection(connection, "sqlite")


def create_user(
    *,
    user_account: str,
    password_hash: str,
    username: str,
    user_role: str = "user",
) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (
                userRole, username, userAccount, passwordHash,
                bio, avatarUrl, cloudbaseUid, createdAt, updatedAt, isDeleted, deletedAt,
                authVersion, lastLoginAt, lastActiveAt, passwordUpdatedAt
            ) VALUES (?, ?, ?, ?, '', '', NULL, ?, ?, 0, NULL, 1, NULL, NULL, ?)
            """,
            (user_role, username, user_account, password_hash, now, now, now),
        )
        connection.commit()
        user_id = int(cursor.lastrowid)
    user = get_user_by_id(user_id)
    if user is None:
        raise RuntimeError("创建用户失败")
    return user


def get_user_by_id(user_id: int) -> dict[str, object] | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT id, userRole, username, userAccount, passwordHash,
                   bio, avatarUrl, cloudbaseUid, createdAt, updatedAt, isDeleted, deletedAt,
                   authVersion, lastLoginAt, lastActiveAt, passwordUpdatedAt
            FROM users WHERE id = ? AND isDeleted = 0
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def get_user_by_account(user_account: str) -> dict[str, object] | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT id, userRole, username, userAccount, passwordHash,
                   bio, avatarUrl, cloudbaseUid, createdAt, updatedAt, isDeleted, deletedAt,
                   authVersion, lastLoginAt, lastActiveAt, passwordUpdatedAt
            FROM users WHERE userAccount = ?
            """,
            (user_account,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def record_successful_login(user_id: int) -> dict[str, object] | None:
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE users
            SET lastLoginAt = ?, lastActiveAt = ?, updatedAt = updatedAt
            WHERE id = ? AND isDeleted = 0
            """,
            (now, now, user_id),
        )
    return get_user_by_id(user_id)


def get_auth_principal(
    user_id: int,
    *,
    touch_activity: bool = True,
) -> dict[str, object] | None:
    """Load account authority from the database, never from JWT claims."""

    now = datetime.now(timezone.utc)
    stale_before = (now - timedelta(minutes=15)).isoformat()
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT id, userRole, username, userAccount, isDeleted,
                   authVersion, lastLoginAt, lastActiveAt, updatedAt
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        principal = _row_to_dict(row)
        if touch_activity and int(principal.get("isDeleted") or 0) == 0:
            last_active = str(principal.get("lastActiveAt") or "")
            if not last_active or last_active < stale_before:
                touched_at = now.isoformat()
                connection.execute(
                    """
                    UPDATE users
                    SET lastActiveAt = ?
                    WHERE id = ? AND isDeleted = 0
                      AND (lastActiveAt IS NULL OR lastActiveAt < ?)
                    """,
                    (touched_at, user_id, stale_before),
                )
                principal["lastActiveAt"] = touched_at
    return principal


def save_report(report: Report) -> Report:
    user_id = _uid()
    quality_artifacts: list[dict[str, Any]] = []
    with _connect() as connection:
        from app.services.benchmark_mapping_service import (
            freeze_report_benchmark_specs,
        )
        from app.services.decision_contract import (
            attach_decision_bundle,
            build_report_decision_bundle,
        )

        store_authority = _decision_store_authority(connection)
        frozen_payload, benchmark_mappings = freeze_report_benchmark_specs(
            report.model_dump(mode="json"),
            decision_kind="daily",
            user_id=user_id,
            connection=connection,
        )
        bundle = build_report_decision_bundle(
            frozen_payload,
            decision_kind="daily",
            store_authority=store_authority,
        )
        bundle["benchmark_mappings"] = benchmark_mappings
        payload = attach_decision_bundle(frozen_payload, bundle)
        saved_report = Report.model_validate(payload)
        connection.execute(
            """
            INSERT OR REPLACE INTO reports (id, created_at, payload, userId)
            VALUES (?, ?, ?, ?)
            """,
            (
                saved_report.id,
                saved_report.created_at.isoformat(),
                json.dumps(payload, ensure_ascii=False),
                user_id,
            ),
        )
        report_recorded_at = datetime.now(timezone.utc).isoformat()
        quality_artifacts = _persist_decision_bundle(
            connection,
            user_id=user_id,
            bundle=bundle,
            report_payload=payload,
            report_recorded_at=report_recorded_at,
        )
    _finalize_committed_decision_quality_artifacts(
        user_id=user_id,
        artifacts=quality_artifacts,
    )
    return saved_report


def _decision_store_authority(connection: Any) -> str:
    configured_mysql = get_settings().uses_mysql
    dialect = str(getattr(connection, "dialect", "sqlite"))
    if configured_mysql and dialect != "mysql":
        return "fallback_non_audited"
    return "primary"


def _persist_decision_bundle(
    connection: Any,
    *,
    user_id: int,
    bundle: dict[str, Any],
    report_payload: dict[str, Any],
    report_recorded_at: str,
) -> list[dict[str, Any]]:
    from app.services.decision_repository import (
        put_fund_benchmark_mapping,
        put_decision_event,
        put_decision_portfolio_snapshot,
        upsert_outcome_observation,
    )

    for mapping in bundle.get("benchmark_mappings") or []:
        if isinstance(mapping, dict):
            put_fund_benchmark_mapping(
                user_id=user_id,
                mapping=mapping,
                connection=connection,
            )
    snapshot = bundle.get("position_snapshot")
    if isinstance(snapshot, dict) and snapshot.get("snapshot_id"):
        put_decision_portfolio_snapshot(
            user_id=user_id,
            snapshot=snapshot,
            connection=connection,
        )
    saved_events: list[dict[str, Any]] = []
    for event in bundle.get("events") or []:
        if isinstance(event, dict):
            saved_events.append(
                put_decision_event(
                    user_id=user_id,
                    event=event,
                    connection=connection,
                )
            )
    for observation in bundle.get("observations") or []:
        if isinstance(observation, dict):
            upsert_outcome_observation(
                user_id=user_id,
                observation=observation,
                connection=connection,
            )
    from app.services.decision_quality_artifacts import (
        persist_report_decision_quality_artifacts,
    )

    contract = bundle.get("contract") or {}
    artifacts = persist_report_decision_quality_artifacts(
        user_id=user_id,
        report=report_payload,
        saved_events=saved_events,
        source_type=str(contract.get("decision_kind") or ""),
        store_authority=str(contract.get("store_authority") or ""),
        report_recorded_at=report_recorded_at,
        connection=connection,
    )
    if str(contract.get("decision_kind") or "") == "discovery":
        from app.services.mainline_snapshot_repository import (
            persist_discovery_mainline_snapshot,
        )

        mainline_artifact = persist_discovery_mainline_snapshot(
            user_id=user_id,
            report=report_payload,
            store_authority=str(contract.get("store_authority") or ""),
            report_recorded_at=report_recorded_at,
            connection=connection,
        )
        if mainline_artifact is not None:
            artifacts.append(mainline_artifact)
    return artifacts


def _finalize_committed_decision_quality_artifacts(
    *,
    user_id: int,
    artifacts: list[dict[str, Any]],
) -> None:
    """Best-effort Phase 2 receipts after the report transaction committed.

    A receipt failure must not imply that the already committed report and
    decision bundle rolled back.  The append-only reconciliation job can retry
    any missing receipt; until then, D4 evaluation treats the artifact as
    pending instead of formal evidence.
    """

    from app.services.decision_repository import (
        finalize_decision_quality_artifact_receipt,
    )

    for row in artifacts:
        payload = row.get("payload")
        if not isinstance(payload, Mapping) or payload.get("store_authority") != "primary":
            continue
        artifact_id = str(payload.get("artifact_id") or "").strip()
        if not artifact_id:
            logger.error(
                "committed decision-quality artifact has no artifact_id",
                extra={"user_id": user_id},
            )
            continue
        try:
            finalize_decision_quality_artifact_receipt(
                user_id=user_id,
                artifact_id=artifact_id,
            )
        except Exception:  # noqa: BLE001 - reconciliation owns durable retries
            logger.exception(
                "decision-quality post-commit receipt remains pending",
                extra={"user_id": user_id, "artifact_id": artifact_id},
            )


def list_reports() -> list[dict[str, Any]]:
    user_id = _uid()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT payload FROM reports
            WHERE userId = ?
            ORDER BY created_at DESC LIMIT 50
            """,
            (user_id,),
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def get_report(report_id: str) -> dict[str, Any] | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM reports WHERE id = ? AND userId = ?",
            (report_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload"])


def get_previous_report(report_id: str) -> dict[str, Any] | None:
    reports = list_reports()
    for index, report in enumerate(reports):
        if report.get("id") == report_id and index + 1 < len(reports):
            return reports[index + 1]
    return None


def get_baseline_report_by_days(report_id: str, days: int = 7) -> dict[str, Any] | None:
    """返回不晚于当前报告、且间隔至少 days 天的最近一份日报。"""
    reports = list_reports()
    current_index = next(
        (index for index, report in enumerate(reports) if report.get("id") == report_id),
        None,
    )
    if current_index is None:
        return None

    current = reports[current_index]
    current_created = _parse_report_datetime(current.get("created_at"))
    if current_created is None:
        return None

    for report in reports[current_index + 1 :]:
        created = _parse_report_datetime(report.get("created_at"))
        if created is None:
            continue
        delta_days = (current_created - created).days
        if delta_days >= days:
            return report
    return None


def _parse_report_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def database_file_path() -> Path:
    return _db_path()


def import_database_file(source: Path, *, backup_current: bool = True) -> dict[str, str]:
    target = _db_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        raise FileNotFoundError(f"数据库文件不存在：{source}")

    with _SQLITE_SCHEMA_INIT_LOCK:
        # ``write_bytes`` replaces the contents in place, so st_dev/st_ino may
        # remain unchanged. Explicit invalidation guarantees that the imported
        # schema receives the complete bootstrap on its next connection.
        _clear_sqlite_schema_init_cache(target)
        backup_path: Path | None = None
        if backup_current and target.exists():
            backup_path = target.with_suffix(".db.bak")
            backup_path.write_bytes(target.read_bytes())

        try:
            target.write_bytes(source.read_bytes())
        finally:
            _clear_sqlite_schema_init_cache(target)
    return {
        "imported_from": str(source),
        "target": str(target),
        "backup_path": str(backup_path) if backup_path else "",
    }


def delete_report(report_id: str) -> bool:
    user_id = _uid()
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM reports WHERE id = ? AND userId = ?",
            (report_id, user_id),
        )
        connection.commit()
    return cursor.rowcount > 0


def save_fund_profile(profile: FundProfile) -> FundProfile:
    from app.services.fund_profile import _sanitize_profile_sector_fields

    profile = _sanitize_profile_sector_fields(profile)
    payload = profile.model_dump(mode="json")
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO fund_profiles (userId, fund_code, fund_name, payload, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                user_id,
                profile.fund_code,
                profile.fund_name,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        connection.commit()
    return profile


def list_distinct_portfolio_user_ids() -> list[int]:
    """后台板块刷新：列出有持仓档案或日快照的用户（跨用户查询）。"""
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT userId FROM fund_profiles
            UNION
            SELECT DISTINCT userId FROM portfolio_daily_snapshots
            """
        ).fetchall()
    return sorted({int(row["userId"]) for row in rows})


def list_fund_profiles() -> list[FundProfile]:
    from app.services.fund_profile import _sanitize_profile_sector_fields

    user_id = _uid()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT payload FROM fund_profiles
            WHERE userId = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [
        _sanitize_profile_sector_fields(FundProfile.model_validate(json.loads(row["payload"])))
        for row in rows
    ]


def delete_fund_profile(fund_code: str) -> bool:
    user_id = _uid()
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM fund_profiles WHERE userId = ? AND fund_code = ?",
            (user_id, fund_code),
        )
        connection.commit()
    return cursor.rowcount > 0


def get_fund_profile_by_code(fund_code: str) -> FundProfile | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM fund_profiles WHERE userId = ? AND fund_code = ?",
            (user_id, fund_code),
        ).fetchone()
    if row is None:
        return None
    from app.services.fund_profile import _sanitize_profile_sector_fields

    return _sanitize_profile_sector_fields(
        FundProfile.model_validate(json.loads(row["payload"]))
    )


def _fund_transaction_from_row(row: object) -> FundTransaction:
    data = _row_to_dict(row)
    return FundTransaction(
        id=str(data["id"]),
        fund_code=data.get("fund_code"),
        fund_name=str(data["fund_name"]),
        direction=str(data["direction"]),
        amount_yuan=float(data["amount_yuan"]),
        trade_time=str(data["trade_time"]),
        confirm_date=str(data["confirm_date"]),
        status=str(data["status"]),
        shares_delta=(
            float(data["shares_delta"]) if data.get("shares_delta") is not None else None
        ),
        nav_on_confirm=(
            float(data["nav_on_confirm"]) if data.get("nav_on_confirm") is not None else None
        ),
        confirmed_shares=(
            float(data["confirmed_shares"])
            if data.get("confirmed_shares") is not None
            else None
        ),
        fee_yuan=(float(data["fee_yuan"]) if data.get("fee_yuan") is not None else None),
        shares_source=(str(data["shares_source"]) if data.get("shares_source") else None),
        in_progress=bool(data.get("in_progress")),
        confirmed_at=(str(data["confirmed_at"]) if data.get("confirmed_at") else None),
        dedup_key=str(data["dedup_key"]),
        created_at=str(data["created_at"]),
    )


def insert_fund_transaction(tx: FundTransaction) -> bool:
    """写入交易记录；命中唯一 (userId, dedup_key) 时忽略并返回 False。"""
    user_id = _uid()
    with _connect() as connection:
        cursor = _insert_fund_transaction_on_connection(connection, tx, user_id=user_id)
    return cursor.rowcount > 0


def _insert_fund_transaction_on_connection(
    connection: Any,
    tx: FundTransaction,
    *,
    user_id: int,
) -> Any:
    return connection.execute(
        """
        INSERT OR IGNORE INTO fund_transactions (
            id, userId, fund_code, fund_name, direction, amount_yuan,
            trade_time, confirm_date, status, shares_delta, nav_on_confirm,
            confirmed_shares, fee_yuan, shares_source, in_progress, confirmed_at,
            dedup_key, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tx.id,
            user_id,
            tx.fund_code,
            tx.fund_name,
            tx.direction,
            tx.amount_yuan,
            tx.trade_time,
            tx.confirm_date,
            tx.status,
            tx.shares_delta,
            tx.nav_on_confirm,
            tx.confirmed_shares,
            tx.fee_yuan,
            tx.shares_source,
            int(tx.in_progress),
            tx.confirmed_at,
            tx.dedup_key,
            tx.created_at,
        ),
    )


def _get_fund_transaction_by_dedup_on_connection(
    connection: Any,
    *,
    user_id: int,
    dedup_key: str,
) -> FundTransaction | None:
    row = connection.execute(
        "SELECT * FROM fund_transactions WHERE userId = ? AND dedup_key = ?",
        (user_id, dedup_key),
    ).fetchone()
    return _fund_transaction_from_row(row) if row is not None else None


def _list_fund_transactions_on_connection(
    connection: Any,
    *,
    user_id: int,
) -> list[FundTransaction]:
    rows = connection.execute(
        "SELECT * FROM fund_transactions WHERE userId = ? "
        "ORDER BY confirm_date ASC, trade_time ASC",
        (user_id,),
    ).fetchall()
    return [_fund_transaction_from_row(row) for row in rows]


def _get_pending_fund_transaction_on_connection(
    connection: Any,
    *,
    user_id: int,
    id: str,
) -> FundTransaction | None:
    lock = " FOR UPDATE" if str(getattr(connection, "dialect", "sqlite")) == "mysql" else ""
    row = connection.execute(
        "SELECT * FROM fund_transactions "
        "WHERE userId = ? AND id = ? AND status = 'pending'" + lock,
        (user_id, id),
    ).fetchone()
    return _fund_transaction_from_row(row) if row is not None else None


def list_fund_transactions(fund_code: str | None = None) -> list[FundTransaction]:
    user_id = _uid()
    with _connect() as connection:
        if fund_code is None:
            return _list_fund_transactions_on_connection(
                connection,
                user_id=user_id,
            )
        else:
            rows = connection.execute(
                """
                SELECT * FROM fund_transactions
                WHERE userId = ? AND fund_code = ?
                ORDER BY confirm_date ASC, trade_time ASC
                """,
                (user_id, fund_code),
            ).fetchall()
    return [_fund_transaction_from_row(row) for row in rows]


def list_pending_fund_transactions() -> list[FundTransaction]:
    user_id = _uid()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fund_transactions
            WHERE userId = ? AND status = 'pending'
            ORDER BY confirm_date ASC, trade_time ASC
            """,
            (user_id,),
        ).fetchall()
    return [_fund_transaction_from_row(row) for row in rows]


def update_fund_transaction(
    id: str,
    *,
    status: str,
    shares_delta: float | None = None,
    nav_on_confirm: float | None = None,
    confirmed_shares: float | None = None,
    fee_yuan: float | None = None,
    shares_source: str | None = None,
    in_progress: bool | None = None,
    confirmed_at: str | None = None,
) -> None:
    user_id = _uid()
    with _connect() as connection:
        _update_fund_transaction_on_connection(
            connection,
            user_id=user_id,
            id=id,
            status=status,
            shares_delta=shares_delta,
            nav_on_confirm=nav_on_confirm,
            confirmed_shares=confirmed_shares,
            fee_yuan=fee_yuan,
            shares_source=shares_source,
            in_progress=in_progress,
            confirmed_at=confirmed_at,
        )


def _update_fund_transaction_on_connection(
    connection: Any,
    *,
    user_id: int,
    id: str,
    status: str,
    shares_delta: float | None = None,
    nav_on_confirm: float | None = None,
    confirmed_shares: float | None = None,
    fee_yuan: float | None = None,
    shares_source: str | None = None,
    in_progress: bool | None = None,
    confirmed_at: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE fund_transactions
        SET status = ?,
            shares_delta = COALESCE(?, shares_delta),
            nav_on_confirm = COALESCE(?, nav_on_confirm),
            confirmed_shares = COALESCE(?, confirmed_shares),
            fee_yuan = COALESCE(?, fee_yuan),
            shares_source = COALESCE(?, shares_source),
            in_progress = CASE WHEN ? IS NULL THEN in_progress ELSE ? END,
            confirmed_at = COALESCE(?, confirmed_at)
        WHERE userId = ? AND id = ?
        """,
        (
            status,
            shares_delta,
            nav_on_confirm,
            confirmed_shares,
            fee_yuan,
            shares_source,
            None if in_progress is None else int(in_progress),
            None if in_progress is None else int(in_progress),
            confirmed_at,
            user_id,
            id,
        ),
    )


def delete_fund_transaction(id: str) -> None:
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            "DELETE FROM fund_transactions WHERE userId = ? AND id = ?",
            (user_id, id),
        )
        connection.commit()


def save_portfolio_summary(summary: PortfolioSummary) -> PortfolioSummary:
    payload = summary.model_dump(mode="json")
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO portfolio_state (userId, payload, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (user_id, json.dumps(payload, ensure_ascii=False)),
        )
        connection.commit()
    return summary


def save_portfolio_daily_snapshot(snapshot: PortfolioDailySnapshot) -> PortfolioDailySnapshot:
    payload = snapshot.model_dump(mode="json")
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO portfolio_daily_snapshots (userId, snapshot_date, payload, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                user_id,
                snapshot.snapshot_date,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        connection.commit()
    return snapshot


def _snapshot_meta_from_row(row: object) -> dict[str, Any]:
    data = _row_to_dict(row)
    snapshot_date = data.get("snapshot_date")
    if snapshot_date is not None:
        snapshot_date = str(snapshot_date).strip('"')[:10]

    def _coerce_float(value: object) -> float | None:
        if value is None:
            return None
        text = str(value).strip().strip('"')
        if not text or text.lower() == "null":
            return None
        try:
            return float(text)
        except (TypeError, ValueError):
            return None

    return {
        "snapshot_date": snapshot_date,
        "total_assets": _coerce_float(data.get("total_assets")),
        "daily_profit": _coerce_float(data.get("daily_profit")),
        "daily_return_percent": _coerce_float(data.get("daily_return_percent")),
    }


def list_portfolio_daily_snapshots(
    *,
    limit: int = 30,
    include_holdings: bool = True,
) -> list[dict[str, Any]]:
    user_id = _uid()
    with _connect() as connection:
        if include_holdings:
            rows = connection.execute(
                """
                SELECT payload FROM portfolio_daily_snapshots
                WHERE userId = ?
                ORDER BY snapshot_date DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        elif connection.dialect == "mysql":
            rows = connection.execute(
                """
                SELECT
                  JSON_UNQUOTE(JSON_EXTRACT(payload, '$.snapshot_date')) AS snapshot_date,
                  JSON_EXTRACT(payload, '$.total_assets') AS total_assets,
                  JSON_EXTRACT(payload, '$.daily_profit') AS daily_profit,
                  JSON_EXTRACT(payload, '$.daily_return_percent') AS daily_return_percent
                FROM portfolio_daily_snapshots
                WHERE userId = ?
                ORDER BY snapshot_date DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT
                  json_extract(payload, '$.snapshot_date') AS snapshot_date,
                  json_extract(payload, '$.total_assets') AS total_assets,
                  json_extract(payload, '$.daily_profit') AS daily_profit,
                  json_extract(payload, '$.daily_return_percent') AS daily_return_percent
                FROM portfolio_daily_snapshots
                WHERE userId = ?
                ORDER BY snapshot_date DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

    results: list[dict[str, Any]] = []
    if include_holdings:
        for row in rows:
            data = json.loads(row["payload"])
            results.append(
                {
                    "snapshot_date": data.get("snapshot_date"),
                    "total_assets": data.get("total_assets"),
                    "daily_profit": data.get("daily_profit"),
                    "daily_return_percent": data.get("daily_return_percent"),
                    "holdings": data.get("holdings") or [],
                    "captured_at": data.get("captured_at"),
                }
            )
        return results

    for row in rows:
        results.append(_snapshot_meta_from_row(row))
    return results


def get_most_recent_portfolio_snapshot() -> dict[str, Any] | None:
    rows = list_portfolio_daily_snapshots(limit=1)
    return rows[0] if rows else None


def save_portfolio_intraday_curve(
    trade_date: str,
    points: list[dict[str, Any]],
    *,
    holdings_fingerprint: str | None = None,
) -> None:
    user_id = _uid()
    payload: dict[str, Any] = {"points": points}
    if holdings_fingerprint:
        payload["holdings_fingerprint"] = holdings_fingerprint
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO portfolio_intraday_curves (userId, trade_date, payload, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (user_id, trade_date, json.dumps(payload, ensure_ascii=False)),
        )
        connection.commit()


def delete_portfolio_snapshots_on_or_before(cutoff_date: str) -> dict[str, int]:
    """删除 cutoff_date 当日及更早的日快照与分时曲线（用于纠正历史脏数据）。"""
    user_id = _uid()
    with _connect() as connection:
        daily = connection.execute(
            """
            DELETE FROM portfolio_daily_snapshots
            WHERE userId = ? AND snapshot_date <= ?
            """,
            (user_id, cutoff_date),
        )
        intraday = connection.execute(
            """
            DELETE FROM portfolio_intraday_curves
            WHERE userId = ? AND trade_date <= ?
            """,
            (user_id, cutoff_date),
        )
        connection.commit()
    return {
        "daily_snapshots_deleted": daily.rowcount,
        "intraday_curves_deleted": intraday.rowcount,
        "cutoff_date": cutoff_date,
    }


def get_portfolio_intraday_curve_entry(trade_date: str) -> dict[str, Any] | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT payload FROM portfolio_intraday_curves
            WHERE userId = ? AND trade_date = ?
            """,
            (user_id, trade_date),
        ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload"])
    points = payload.get("points")
    if not isinstance(points, list):
        return None
    fingerprint = payload.get("holdings_fingerprint")
    return {
        "points": points,
        "holdings_fingerprint": str(fingerprint) if fingerprint else None,
    }


def get_portfolio_intraday_curve(trade_date: str) -> list[dict[str, Any]] | None:
    entry = get_portfolio_intraday_curve_entry(trade_date)
    return entry["points"] if entry else None


def get_investor_profile() -> InvestorProfile | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM investor_profile_state WHERE userId = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return InvestorProfile.model_validate(json.loads(row["payload"]))


def save_investor_profile(profile: InvestorProfile) -> InvestorProfile:
    payload = profile.model_dump(mode="json")
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO investor_profile_state (userId, payload, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (user_id, json.dumps(payload, ensure_ascii=False)),
        )
        connection.commit()
    return profile


def get_analysis_role_prompt() -> str | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            "SELECT role_prompt FROM analysis_prompt_state WHERE userId = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    value = row["role_prompt"]
    return value if isinstance(value, str) and value.strip() else None


def save_analysis_role_prompt(role_prompt: str | None) -> str | None:
    from app.services.analysis_prompt import normalize_role_prompt

    normalized = normalize_role_prompt(role_prompt)
    user_id = _uid()
    with _connect() as connection:
        if normalized is None:
            connection.execute(
                "DELETE FROM analysis_prompt_state WHERE userId = ?",
                (user_id,),
            )
        else:
            connection.execute(
                """
                INSERT OR REPLACE INTO analysis_prompt_state (userId, role_prompt, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (user_id, normalized),
            )
        connection.commit()
    return normalized


def get_discovery_role_prompt() -> str | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            "SELECT role_prompt FROM discovery_prompt_state WHERE userId = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    value = row["role_prompt"]
    return value if isinstance(value, str) and value.strip() else None


def save_discovery_role_prompt(role_prompt: str | None) -> str | None:
    from app.services.analysis_prompt import normalize_role_prompt

    normalized = normalize_role_prompt(role_prompt)
    user_id = _uid()
    with _connect() as connection:
        if normalized is None:
            connection.execute(
                "DELETE FROM discovery_prompt_state WHERE userId = ?",
                (user_id,),
            )
        else:
            connection.execute(
                """
                INSERT OR REPLACE INTO discovery_prompt_state (userId, role_prompt, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (user_id, normalized),
            )
        connection.commit()
    return normalized


def get_previous_discovery_report(report_id: str) -> dict[str, Any] | None:
    reports = list_discovery_reports()
    for index, report in enumerate(reports):
        if report.get("id") == report_id and index + 1 < len(reports):
            return reports[index + 1]
    return None


def get_portfolio_summary() -> PortfolioSummary | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM portfolio_state WHERE userId = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    data = json.loads(row["payload"])
    if data.get("updated_at") is None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
    return PortfolioSummary.model_validate(data)


def get_ocr_text_cache(cache_key: str) -> str | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            "SELECT raw_text FROM ocr_text_cache WHERE userId = ? AND cache_key = ?",
            (user_id, cache_key),
        ).fetchone()
    if row is None:
        return None
    return str(row["raw_text"])


def list_report_chat_messages(report_id: str) -> list[dict[str, Any]]:
    if get_report(report_id) is None:
        return []
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, report_id, role, content, created_at
            FROM report_chat_messages
            WHERE report_id = ?
            ORDER BY created_at ASC
            """,
            (report_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "report_id": row["report_id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def save_chat_message(message: ChatMessage) -> ChatMessage:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO report_chat_messages (id, report_id, role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                message.id,
                message.report_id,
                message.role,
                message.content,
                message.created_at.isoformat(),
            ),
        )
        connection.commit()
    return message


def save_ocr_text_cache(cache_key: str, raw_text: str) -> None:
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO ocr_text_cache (userId, cache_key, raw_text, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (user_id, cache_key, raw_text),
        )
        connection.commit()


def get_sector_mapping(sector_label: str) -> dict[str, Any] | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM sector_mappings
            WHERE userId = ? AND sector_label = ?
            """,
            (user_id, sector_label),
        ).fetchone()
    if row is None:
        return None
    return {
        "sector_label": row["sector_label"],
        "source_type": row["source_type"],
        "source_code": row["source_code"],
        "source_name": row["source_name"],
        "confidence": row["confidence"],
        "updated_at": row["updated_at"],
    }


def save_sector_mapping(record: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO sector_mappings
            (userId, sector_label, source_type, source_code, source_name, confidence, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                record["sector_label"],
                record["source_type"],
                record.get("source_code"),
                record["source_name"],
                record.get("confidence", "high"),
                record.get("updated_at", now),
            ),
        )
        connection.commit()
    return get_sector_mapping(record["sector_label"]) or record


def get_fund_primary_sector(fund_code: str) -> dict[str, Any] | None:
    user_id = _uid()
    code = fund_code.strip().zfill(6)
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT fund_code, sector_name, intraday_index_name, source, confidence, detail, updated_at
            FROM fund_primary_sectors
            WHERE userId = ? AND fund_code = ?
            """,
            (user_id, code),
        ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def save_fund_primary_sector(
    *,
    fund_code: str,
    sector_name: str,
    intraday_index_name: str | None = None,
    source: str,
    confidence: float | None = None,
    detail: dict | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    user_id = _uid()
    code = fund_code.strip().zfill(6)
    detail_json = json.dumps(detail, ensure_ascii=False) if detail else None
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO fund_primary_sectors (
                userId, fund_code, sector_name, intraday_index_name,
                source, confidence, detail, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                code,
                sector_name,
                intraday_index_name,
                source,
                confidence,
                detail_json,
                now,
            ),
        )
        connection.commit()
    return {
        "fund_code": code,
        "sector_name": sector_name,
        "intraday_index_name": intraday_index_name,
        "source": source,
        "confidence": confidence,
        "detail": detail,
        "updated_at": now,
    }


def delete_fund_primary_sector(fund_code: str) -> bool:
    user_id = _uid()
    code = fund_code.strip().zfill(6)
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM fund_primary_sectors WHERE userId = ? AND fund_code = ?",
            (user_id, code),
        )
        connection.commit()
    return cursor.rowcount > 0


def list_fund_primary_sectors() -> list[dict[str, Any]]:
    user_id = _uid()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT fund_code, sector_name, intraday_index_name, source, confidence, detail, updated_at
            FROM fund_primary_sectors
            WHERE userId = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def list_fund_primary_sectors_by_sector_names(
    sector_names: list[str],
    *,
    limit_per_sector: int = 20,
) -> list[dict[str, Any]]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in sector_names:
        label = str(raw or "").strip()
        if label and label not in seen:
            seen.add(label)
            normalized.append(label)
    if not normalized:
        return []

    placeholders = ",".join("?" * len(normalized))
    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT fund_code, sector_name, intraday_index_name, source, confidence, detail, resolved_at
            FROM fund_primary_sectors_global
            WHERE sector_name IN ({placeholders})
            ORDER BY confidence DESC, resolved_at DESC
            """,
            tuple(normalized),
        ).fetchall()
    counts: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = _row_to_dict(row)
        label = str(payload.get("sector_name") or "")
        if counts.get(label, 0) >= limit_per_sector:
            continue
        payload["updated_at"] = payload.get("resolved_at")
        result.append(payload)
        counts[label] = counts.get(label, 0) + 1
    return result


def get_fund_primary_sector_global(fund_code: str) -> dict[str, Any] | None:
    rows = get_fund_primary_sectors_global_by_codes([fund_code])
    code = fund_code.strip().zfill(6)
    return rows.get(code)


def get_fund_primary_sectors_global_by_codes(
    fund_codes: set[str] | list[str],
) -> dict[str, dict[str, Any]]:
    """批量读取全市场关联板块（未做 TTL 过滤，由调用方判定 freshness）。"""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in fund_codes:
        code = str(raw or "").strip().zfill(6)
        if len(code) == 6 and code.isdigit() and code not in seen:
            seen.add(code)
            normalized.append(code)
    if not normalized:
        return {}

    placeholders = ",".join("?" * len(normalized))
    with _connect() as connection:
        rows = connection.execute(
            f"""
            SELECT fund_code, sector_name, intraday_index_name, source, confidence, detail, resolved_at
            FROM fund_primary_sectors_global
            WHERE fund_code IN ({placeholders})
            """,
            tuple(normalized),
        ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = _row_to_dict(row)
        payload["updated_at"] = payload.get("resolved_at")
        code = str(payload.get("fund_code", "")).zfill(6)
        if code:
            result[code] = payload
    return result


def save_fund_primary_sector_global(
    *,
    fund_code: str,
    sector_name: str,
    intraday_index_name: str | None = None,
    source: str,
    confidence: float | None = None,
    detail: dict | None = None,
    resolved_at: str | None = None,
) -> dict[str, Any]:
    now = resolved_at or datetime.now(timezone.utc).isoformat()
    code = fund_code.strip().zfill(6)
    detail_json = json.dumps(detail, ensure_ascii=False) if detail else None
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO fund_primary_sectors_global (
                fund_code, sector_name, intraday_index_name,
                source, confidence, detail, resolved_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                code,
                sector_name,
                intraday_index_name,
                source,
                confidence,
                detail_json,
                now,
            ),
        )
        connection.commit()
    return {
        "fund_code": code,
        "sector_name": sector_name,
        "intraday_index_name": intraday_index_name,
        "source": source,
        "confidence": confidence,
        "detail": detail,
        "resolved_at": now,
        "updated_at": now,
    }


def count_fund_primary_sectors_global() -> int:
    with _connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS cnt FROM fund_primary_sectors_global",
        ).fetchone()
    if row is None:
        return 0
    return int(_row_to_dict(row).get("cnt") or 0)


def list_fund_primary_sectors_global(*, limit: int = 5000) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT fund_code, sector_name, intraday_index_name, source, confidence, detail, resolved_at
            FROM fund_primary_sectors_global
            ORDER BY resolved_at DESC
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
    result = []
    for row in rows:
        payload = _row_to_dict(row)
        payload["updated_at"] = payload.get("resolved_at")
        result.append(payload)
    return result


def save_discovery_report(report: FundDiscoveryReport) -> FundDiscoveryReport:
    user_id = _uid()
    quality_artifacts: list[dict[str, Any]] = []
    with _connect() as connection:
        from app.services.benchmark_mapping_service import (
            freeze_report_benchmark_specs,
        )
        from app.services.decision_contract import (
            attach_decision_bundle,
            build_report_decision_bundle,
        )

        store_authority = _decision_store_authority(connection)
        frozen_payload, benchmark_mappings = freeze_report_benchmark_specs(
            report.model_dump(mode="json"),
            decision_kind="discovery",
            user_id=user_id,
            connection=connection,
        )
        bundle = build_report_decision_bundle(
            frozen_payload,
            decision_kind="discovery",
            store_authority=store_authority,
        )
        bundle["benchmark_mappings"] = benchmark_mappings
        payload = attach_decision_bundle(frozen_payload, bundle)
        saved_report = FundDiscoveryReport.model_validate(payload)
        connection.execute(
            """
            INSERT OR REPLACE INTO fund_discovery_reports (id, created_at, payload, userId)
            VALUES (?, ?, ?, ?)
            """,
            (
                saved_report.id,
                saved_report.created_at.isoformat(),
                json.dumps(payload, ensure_ascii=False),
                user_id,
            ),
        )
        report_recorded_at = datetime.now(timezone.utc).isoformat()
        quality_artifacts = _persist_decision_bundle(
            connection,
            user_id=user_id,
            bundle=bundle,
            report_payload=payload,
            report_recorded_at=report_recorded_at,
        )
    _finalize_committed_decision_quality_artifacts(
        user_id=user_id,
        artifacts=quality_artifacts,
    )
    return saved_report


def list_discovery_reports(*, limit: int = 30) -> list[dict[str, Any]]:
    user_id = _uid()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT payload FROM fund_discovery_reports
            WHERE userId = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def get_discovery_report(report_id: str) -> dict[str, Any] | None:
    user_id = _uid()
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT payload FROM fund_discovery_reports
            WHERE id = ? AND userId = ?
            """,
            (report_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload"])


def delete_discovery_report(report_id: str) -> bool:
    user_id = _uid()
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM fund_discovery_reports WHERE id = ? AND userId = ?",
            (report_id, user_id),
        )
        connection.execute(
            "DELETE FROM discovery_chat_messages WHERE discovery_report_id = ?",
            (report_id,),
        )
        connection.commit()
    return cursor.rowcount > 0


def list_discovery_chat_messages(discovery_report_id: str) -> list[dict[str, Any]]:
    if get_discovery_report(discovery_report_id) is None:
        return []
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, discovery_report_id, role, content, created_at
            FROM discovery_chat_messages
            WHERE discovery_report_id = ?
            ORDER BY created_at ASC
            """,
            (discovery_report_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "discovery_report_id": row["discovery_report_id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def save_discovery_chat_message(message: DiscoveryChatMessage) -> DiscoveryChatMessage:
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO discovery_chat_messages (
                id, discovery_report_id, role, content, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                message.id,
                message.discovery_report_id,
                message.role,
                message.content,
                message.created_at.isoformat(),
            ),
        )
        connection.commit()
    return message


class FundHoldingsSnapshotConflict(RuntimeError):
    """A snapshot hash was reused for different immutable evidence."""


def _snapshot_text(value: object, field: str, *, required: bool = False) -> str | None:
    text = str(value).strip() if value is not None else ""
    if required and not text:
        raise ValueError(f"{field} is required")
    return text or None


def _normalize_snapshot_timestamp(
    value: object,
    *,
    field: str,
    required: bool = False,
) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"{field} is required")
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _snapshot_verified_master_key(snapshot: Mapping[str, Any], fund_code: str) -> str:
    explicit = _snapshot_text(snapshot.get("fund_master_key"), "fund_master_key")
    family_hint = snapshot.get("family_hint")
    verified_key: str | None = None
    if isinstance(family_hint, Mapping):
        is_verified = (
            family_hint.get("verified") is True
            and str(family_hint.get("status") or "").strip().lower() == "verified"
            and family_hint.get("hard_merge_applied") is True
        )
        if is_verified:
            for key in ("fund_master_key", "verified_master_key", "hinted_master_key"):
                verified_key = _snapshot_text(family_hint.get(key), key)
                if verified_key is not None:
                    break

    if explicit is None:
        return verified_key or fund_code
    if explicit == fund_code or (verified_key is not None and explicit == verified_key):
        return explicit
    raise ValueError(
        "fund_master_key may differ from fund_code only with a verified hard-merge mapping"
    )


def _normalize_fund_holdings_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping")

    fund_code = _snapshot_text(snapshot.get("fund_code"), "fund_code", required=True)
    assert fund_code is not None
    fund_master_key = _snapshot_verified_master_key(snapshot, fund_code)
    schema_version = _snapshot_text(
        snapshot.get("schema_version") or snapshot.get("schema"), "schema_version", required=True
    )
    status = _snapshot_text(snapshot.get("status"), "status", required=True)
    source_hash = _snapshot_text(snapshot.get("source_hash"), "source_hash", required=True)
    snapshot_hash = _snapshot_text(
        snapshot.get("snapshot_hash"), "snapshot_hash", required=True
    )
    assert schema_version is not None and status is not None
    assert source_hash is not None and snapshot_hash is not None
    status = status.lower()
    if _SHA256_HEX_PATTERN.fullmatch(source_hash) is None:
        raise ValueError("source_hash must be a lowercase SHA-256 hex digest")
    if _SHA256_HEX_PATTERN.fullmatch(snapshot_hash) is None:
        raise ValueError("snapshot_hash must be a lowercase SHA-256 hex digest")
    from app.services.fund_holdings_snapshot import (
        validate_fund_holdings_snapshot_hash,
    )

    if not validate_fund_holdings_snapshot_hash(snapshot):
        raise ValueError("snapshot_hash does not match canonical snapshot content")

    report_period = _snapshot_text(snapshot.get("report_period"), "report_period")
    if report_period is not None and re.fullmatch(r"\d{4}-Q[1-4]", report_period) is None:
        raise ValueError("report_period must use YYYY-Qn")
    as_of_date = _snapshot_text(snapshot.get("as_of_date"), "as_of_date")
    if as_of_date is not None:
        try:
            as_of_date = date.fromisoformat(as_of_date).isoformat()
        except ValueError as exc:
            raise ValueError("as_of_date must be an ISO date") from exc
    available_at = _normalize_snapshot_timestamp(
        snapshot.get("available_at"), field="available_at"
    )
    first_observed_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")

    if status == "qualified":
        qualification = snapshot.get("qualification")
        source_validation = snapshot.get("source_validation")
        if schema_version != _FUND_HOLDINGS_SNAPSHOT_SCHEMA:
            raise ValueError("qualified snapshot must use fund_holdings_snapshot.v1")
        if snapshot.get("qualified") is not True:
            raise ValueError("qualified status requires qualified=true")
        if (
            not isinstance(source_validation, Mapping)
            or source_validation.get("schema_version")
            != "fund_holdings_source_validation.v1"
            or source_validation.get("status") != "qualified"
            or source_validation.get("qualified") is not True
            or source_validation.get("valid_snapshot") is not True
        ):
            raise ValueError(
                "qualified snapshot requires immutable source_validation"
            )
        if not isinstance(qualification, Mapping) or not all(
            qualification.get(field) is True
            for field in (
                "qualified",
                "valid_snapshot",
                "pit_eligible",
                "disclosure_scope_identified",
                "weight_validation_passed",
            )
        ):
            raise ValueError("qualified snapshot requires a complete qualification contract")
        if report_period is None or as_of_date is None:
            raise ValueError("qualified snapshot requires report_period and as_of_date")
        if available_at is None:
            raise ValueError("qualified snapshot requires a known available_at")
        if datetime.fromisoformat(available_at).date() < date.fromisoformat(as_of_date):
            raise ValueError("available_at cannot precede as_of_date for a qualified snapshot")
        if datetime.fromisoformat(available_at) > datetime.fromisoformat(first_observed_at):
            raise ValueError("available_at cannot be later than first_observed_at")

    # Keep the immutable payload's original timestamp representation.  The
    # indexed column is normalized to UTC for lexical PIT queries, while the
    # content-addressed snapshot may intentionally use a Shanghai offset.  If
    # we rewrote the payload to the indexed representation, snapshot_hash
    # would no longer be reproducible from the persisted evidence.
    payload = dict(snapshot)
    payload.update(
        {
            "schema_version": schema_version,
            "fund_code": fund_code,
            "fund_master_key": fund_master_key,
            "report_period": report_period,
            "as_of_date": as_of_date,
            # Observation time is storage-owned; callers cannot backdate it.
            "first_observed_at": first_observed_at,
            "source_hash": source_hash,
            "snapshot_hash": snapshot_hash,
            "status": status,
        }
    )
    created_at = first_observed_at
    return {
        "id": f"fhs_{snapshot_hash}",
        "fund_master_key": fund_master_key,
        "fund_code": fund_code,
        "report_period": report_period,
        "as_of_date": as_of_date,
        "available_at": available_at,
        "first_observed_at": first_observed_at,
        "source_hash": source_hash,
        "snapshot_hash": snapshot_hash,
        "schema_version": schema_version,
        "status": status,
        "payload_json": json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        "created_at": created_at,
    }


@contextmanager
def _fund_holdings_snapshot_connection(connection: Any | None) -> Iterator[Any]:
    if connection is not None:
        yield connection
        return
    owned = _connect()
    try:
        yield owned
        owned.commit()
    except Exception:
        owned.rollback()
        raise
    finally:
        owned.close()


def _decode_fund_holdings_snapshot_row(row: dict[str, Any]) -> dict[str, Any]:
    result = _row_to_dict(row)
    payload = result.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    result["payload_json"] = payload
    result["payload"] = payload
    return result


def _load_fund_holdings_snapshot(db: Any, snapshot_hash: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT * FROM fund_holdings_snapshots WHERE snapshot_hash = ?",
        (snapshot_hash,),
    ).fetchone()
    return None if row is None else _row_to_dict(row)


_FUND_HOLDINGS_IMMUTABLE_COLUMNS = (
    "fund_master_key",
    "fund_code",
    "report_period",
    "as_of_date",
    "available_at",
    "source_hash",
    "snapshot_hash",
    "schema_version",
    "status",
)


def _assert_same_fund_holdings_snapshot(
    stored: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> None:
    mismatched = [
        field
        for field in _FUND_HOLDINGS_IMMUTABLE_COLUMNS
        if stored.get(field) != candidate.get(field)
    ]
    if mismatched:
        raise FundHoldingsSnapshotConflict(
            "snapshot_hash already exists with different immutable fields: "
            + ", ".join(mismatched)
        )


def _snapshot_save_result(row: dict[str, Any], *, stored: bool) -> dict[str, Any]:
    record = _decode_fund_holdings_snapshot_row(row)
    return {
        "id": record["id"],
        "stored": stored,
        "duplicate": not stored,
        "record": record,
        "snapshot": record.get("payload"),
    }


def save_fund_holdings_snapshot(
    snapshot: Mapping[str, Any],
    *,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Append one immutable holdings revision, or return its existing twin."""

    candidate = _normalize_fund_holdings_snapshot(snapshot)
    with _fund_holdings_snapshot_connection(connection) as db:
        existing = _load_fund_holdings_snapshot(db, candidate["snapshot_hash"])
        if existing is not None:
            _assert_same_fund_holdings_snapshot(existing, candidate)
            return _snapshot_save_result(existing, stored=False)

        columns = (
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
        )
        placeholders = ", ".join("?" for _ in columns)
        try:
            db.execute(
                f"INSERT INTO fund_holdings_snapshots ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                tuple(candidate[column] for column in columns),
            )
        except Exception:
            raced = _load_fund_holdings_snapshot(db, candidate["snapshot_hash"])
            if raced is None:
                raise
            _assert_same_fund_holdings_snapshot(raced, candidate)
            return _snapshot_save_result(raced, stored=False)

        inserted = _load_fund_holdings_snapshot(db, candidate["snapshot_hash"])
        if inserted is None:
            raise RuntimeError("fund holdings snapshot insert was not observable")
        return _snapshot_save_result(inserted, stored=True)


put_fund_holdings_snapshot = save_fund_holdings_snapshot


def _stored_fund_holdings_snapshot_is_qualified(record: Mapping[str, Any]) -> bool:
    if (
        record.get("status") != "qualified"
        or record.get("schema_version") != _FUND_HOLDINGS_SNAPSHOT_SCHEMA
        or record.get("available_at") is None
    ):
        return False
    try:
        _normalize_snapshot_timestamp(
            record["available_at"], field="available_at", required=True
        )
    except ValueError:
        return False
    payload = record.get("payload")
    if not isinstance(payload, Mapping) or payload.get("qualified") is not True:
        return False
    # Indexed status and duplicated envelope fields are only lookup aids.  The
    # immutable payload remains the trust boundary, so a row whose content no
    # longer matches its address must never be returned as decision evidence.
    from app.services.fund_holdings_snapshot import (
        validate_fund_holdings_snapshot_hash,
    )

    if not validate_fund_holdings_snapshot_hash(payload):
        return False
    source_validation = payload.get("source_validation")
    if (
        not isinstance(source_validation, Mapping)
        or source_validation.get("schema_version")
        != "fund_holdings_source_validation.v1"
        or source_validation.get("status") != "qualified"
        or source_validation.get("qualified") is not True
        or source_validation.get("valid_snapshot") is not True
    ):
        return False
    qualification = payload.get("qualification")
    if not isinstance(qualification, Mapping) or not all(
        qualification.get(field) is True
        for field in (
            "qualified",
            "valid_snapshot",
            "pit_eligible",
            "disclosure_scope_identified",
            "weight_validation_passed",
        )
    ):
        return False
    for field in _FUND_HOLDINGS_IMMUTABLE_COLUMNS:
        if field == "available_at":
            try:
                payload_time = _normalize_snapshot_timestamp(
                    payload.get(field), field=field, required=True
                )
            except ValueError:
                return False
            if payload_time != record.get(field):
                return False
            continue
        if payload.get(field) != record.get(field):
            return False
    return True


def list_fund_holdings_snapshots(
    *,
    fund_code: str | None = None,
    fund_master_key: str | None = None,
    decision_at: datetime | str | None = None,
    qualified_only: bool = False,
    limit: int = 100,
    connection: Any | None = None,
) -> list[dict[str, Any]]:
    """List immutable revisions, optionally constrained to a PIT decision clock."""

    normalized_code = _snapshot_text(fund_code, "fund_code")
    normalized_master = _snapshot_text(fund_master_key, "fund_master_key")
    if normalized_code is None and normalized_master is None:
        raise ValueError("fund_code or fund_master_key is required")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0 or limit > 1000:
        raise ValueError("limit must be an integer between 1 and 1000")

    where: list[str] = []
    params: list[Any] = []
    if normalized_code is not None:
        where.append("fund_code = ?")
        params.append(normalized_code)
    if normalized_master is not None:
        where.append("fund_master_key = ?")
        params.append(normalized_master)
    if decision_at is not None:
        normalized_decision_at = _normalize_snapshot_timestamp(
            decision_at, field="decision_at", required=True
        )
        # A report can have an old official publication time but only be first
        # captured (or silently revised by the upstream current endpoint) much
        # later.  Historical replay must not backfill that newer observation
        # into a decision that predates our immutable capture.
        where.extend(
            (
                "available_at IS NOT NULL",
                "available_at <= ?",
                "first_observed_at <= ?",
            )
        )
        params.extend((normalized_decision_at, normalized_decision_at))
    if qualified_only:
        where.extend(
            (
                "status = ?",
                "schema_version = ?",
                "available_at IS NOT NULL",
            )
        )
        params.extend(("qualified", _FUND_HOLDINGS_SNAPSHOT_SCHEMA))

    sql = (
        "SELECT * FROM fund_holdings_snapshots WHERE "
        + " AND ".join(where)
        + " ORDER BY available_at DESC, first_observed_at DESC, created_at DESC, id DESC"
    )
    with _fund_holdings_snapshot_connection(connection) as db:
        rows = db.execute(sql, tuple(params)).fetchall()
        records = [
            _decode_fund_holdings_snapshot_row(_row_to_dict(row))
            for row in rows
        ]
    if qualified_only:
        records = [
            record
            for record in records
            if _stored_fund_holdings_snapshot_is_qualified(record)
        ]
    return records[:limit]


def get_latest_fund_holdings_snapshot(
    *,
    decision_at: datetime | str,
    fund_code: str | None = None,
    fund_master_key: str | None = None,
    qualified_only: bool = True,
    connection: Any | None = None,
) -> dict[str, Any] | None:
    """Return the newest eligible revision known at an aware decision time."""

    records = list_fund_holdings_snapshots(
        fund_code=fund_code,
        fund_master_key=fund_master_key,
        decision_at=decision_at,
        qualified_only=qualified_only,
        limit=1,
        connection=connection,
    )
    return records[0] if records else None
