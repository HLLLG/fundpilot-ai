from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db_migrations import run_migrations
from app.request_context import get_request_user_id
from datetime import datetime, timezone

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


def _connect():
    from app.db_connect import connect, uses_mysql

    if uses_mysql():
        return connect()
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
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
    connection.commit()
    from app.db_connect import DbConnection

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
                bio, avatarUrl, cloudbaseUid, createdAt, updatedAt, isDeleted, deletedAt
            ) VALUES (?, ?, ?, ?, '', '', NULL, ?, ?, 0, NULL)
            """,
            (user_role, username, user_account, password_hash, now, now),
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
                   bio, avatarUrl, cloudbaseUid, createdAt, updatedAt, isDeleted, deletedAt
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
                   bio, avatarUrl, cloudbaseUid, createdAt, updatedAt, isDeleted, deletedAt
            FROM users WHERE userAccount = ?
            """,
            (user_account,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def get_user_by_cloudbase_uid(cloudbase_uid: str) -> dict[str, object] | None:
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT id, userRole, username, userAccount, passwordHash,
                   bio, avatarUrl, cloudbaseUid, createdAt, updatedAt, isDeleted, deletedAt
            FROM users WHERE cloudbaseUid = ? AND isDeleted = 0
            """,
            (cloudbase_uid,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def _wechat_placeholder_account(cloudbase_uid: str) -> str:
    digest = hashlib.sha256(cloudbase_uid.encode("utf-8")).hexdigest()[:16]
    return f"wx_{digest}@wechat.fundpilot"


def create_wechat_user(*, cloudbase_uid: str, username: str) -> dict[str, object]:
    from app.auth.passwords import hash_password

    account = _wechat_placeholder_account(cloudbase_uid)
    now = datetime.now(timezone.utc).isoformat()
    display_name = username.strip() or "微信用户"
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (
                userRole, username, userAccount, passwordHash,
                bio, avatarUrl, cloudbaseUid, createdAt, updatedAt, isDeleted, deletedAt
            ) VALUES (?, ?, ?, ?, '', '', ?, ?, ?, 0, NULL)
            """,
            (
                "user",
                display_name,
                account,
                hash_password(secrets.token_urlsafe(32)),
                cloudbase_uid,
                now,
                now,
            ),
        )
        connection.commit()
        user_id = int(cursor.lastrowid)
    user = get_user_by_id(user_id)
    if user is None:
        raise RuntimeError("创建微信用户失败")
    return user


def bind_user_cloudbase_uid(user_id: int, cloudbase_uid: str) -> dict[str, object]:
    existing = get_user_by_cloudbase_uid(cloudbase_uid)
    if existing is not None and int(existing["id"]) != user_id:
        raise ValueError("该微信账号已绑定其他用户")
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE users SET cloudbaseUid = ?, updatedAt = ?
            WHERE id = ? AND isDeleted = 0
            """,
            (cloudbase_uid, now, user_id),
        )
        connection.commit()
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError("用户不存在")
    return user


def merge_wechat_account_into_email_user(
    wechat_user_id: int,
    email_user_id: int,
) -> dict[str, object]:
    """把微信占位账号上的 cloudbaseUid 迁移到邮箱账号，并软删占位账号。

    用于小程序「关联已有邮箱账号」：微信登录会先建一个空的占位账号（携带本次
    openid），关联时把该 openid 搬到真正的邮箱账号上，之后每次微信登录都命中
    邮箱账号，从而看到 Web 端录入的持仓。占位账号无业务数据，仅软删不迁移。
    """
    wechat_user = get_user_by_id(wechat_user_id)
    if wechat_user is None:
        raise ValueError("微信账号不存在")
    email_user = get_user_by_id(email_user_id)
    if email_user is None:
        raise ValueError("邮箱账号不存在")
    if wechat_user_id == email_user_id:
        raise ValueError("无法关联到自身账号")

    cloudbase_uid = wechat_user.get("cloudbaseUid")
    if not cloudbase_uid:
        raise ValueError("当前微信账号缺少标识，无法关联")

    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        # 先解绑并软删占位账号，避免两条记录同时持有同一 cloudbaseUid
        connection.execute(
            """
            UPDATE users
            SET cloudbaseUid = NULL, isDeleted = 1, deletedAt = ?, updatedAt = ?
            WHERE id = ?
            """,
            (now, now, wechat_user_id),
        )
        connection.execute(
            """
            UPDATE users SET cloudbaseUid = ?, updatedAt = ?
            WHERE id = ? AND isDeleted = 0
            """,
            (cloudbase_uid, now, email_user_id),
        )
        connection.commit()

    merged = get_user_by_id(email_user_id)
    if merged is None:
        raise ValueError("邮箱账号不存在")
    return merged


def save_report(report: Report) -> Report:
    payload = report.model_dump(mode="json")
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO reports (id, created_at, payload, userId)
            VALUES (?, ?, ?, ?)
            """,
            (
                report.id,
                report.created_at.isoformat(),
                json.dumps(payload, ensure_ascii=False),
                user_id,
            ),
        )
        connection.commit()
    return report


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

    backup_path: Path | None = None
    if backup_current and target.exists():
        backup_path = target.with_suffix(".db.bak")
        backup_path.write_bytes(target.read_bytes())

    target.write_bytes(source.read_bytes())
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
        dedup_key=str(data["dedup_key"]),
        created_at=str(data["created_at"]),
    )


def insert_fund_transaction(tx: FundTransaction) -> bool:
    """写入交易记录；命中唯一 (userId, dedup_key) 时忽略并返回 False。"""
    user_id = _uid()
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO fund_transactions (
                id, userId, fund_code, fund_name, direction, amount_yuan,
                trade_time, confirm_date, status, shares_delta, nav_on_confirm,
                dedup_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                tx.dedup_key,
                tx.created_at,
            ),
        )
        connection.commit()
    return cursor.rowcount > 0


def list_fund_transactions(fund_code: str | None = None) -> list[FundTransaction]:
    user_id = _uid()
    with _connect() as connection:
        if fund_code is None:
            rows = connection.execute(
                """
                SELECT * FROM fund_transactions
                WHERE userId = ?
                ORDER BY confirm_date ASC, trade_time ASC
                """,
                (user_id,),
            ).fetchall()
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
) -> None:
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            """
            UPDATE fund_transactions
            SET status = ?, shares_delta = ?, nav_on_confirm = ?
            WHERE userId = ? AND id = ?
            """,
            (status, shares_delta, nav_on_confirm, user_id, id),
        )
        connection.commit()


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


def list_portfolio_daily_snapshots(*, limit: int = 30) -> list[dict[str, Any]]:
    user_id = _uid()
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT payload FROM portfolio_daily_snapshots
            WHERE userId = ?
            ORDER BY snapshot_date DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    results: list[dict[str, Any]] = []
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
    return get_fund_primary_sector(code) or {
        "fund_code": code,
        "sector_name": sector_name,
        "intraday_index_name": intraday_index_name,
        "source": source,
        "confidence": confidence,
        "detail": detail,
        "updated_at": now,
    }


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


def save_discovery_report(report: FundDiscoveryReport) -> FundDiscoveryReport:
    payload = report.model_dump(mode="json")
    user_id = _uid()
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO fund_discovery_reports (id, created_at, payload, userId)
            VALUES (?, ?, ?, ?)
            """,
            (
                report.id,
                report.created_at.isoformat(),
                json.dumps(payload, ensure_ascii=False),
                user_id,
            ),
        )
        connection.commit()
    return report


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
