from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.config import get_settings
from datetime import datetime, timezone

from app.models import (
    ChatMessage,
    FundProfile,
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


def _connect() -> sqlite3.Connection:
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
    connection.commit()
    return connection


def save_report(report: Report) -> Report:
    payload = report.model_dump(mode="json")
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO reports (id, created_at, payload)
            VALUES (?, ?, ?)
            """,
            (report.id, report.created_at.isoformat(), json.dumps(payload, ensure_ascii=False)),
        )
        connection.commit()
    return report


def list_reports() -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM reports ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def get_report(report_id: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM reports WHERE id = ?",
            (report_id,),
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
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM reports WHERE id = ?",
            (report_id,),
        )
        connection.commit()
    return cursor.rowcount > 0


def save_fund_profile(profile: FundProfile) -> FundProfile:
    from app.services.fund_profile import _sanitize_profile_sector_fields

    profile = _sanitize_profile_sector_fields(profile)
    payload = profile.model_dump(mode="json")
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO fund_profiles (fund_code, fund_name, payload, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                profile.fund_code,
                profile.fund_name,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        connection.commit()
    return profile


def list_fund_profiles() -> list[FundProfile]:
    from app.services.fund_profile import _sanitize_profile_sector_fields

    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM fund_profiles ORDER BY updated_at DESC"
        ).fetchall()
    return [
        _sanitize_profile_sector_fields(FundProfile.model_validate(json.loads(row["payload"])))
        for row in rows
    ]


def delete_fund_profile(fund_code: str) -> bool:
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM fund_profiles WHERE fund_code = ?",
            (fund_code,),
        )
        connection.commit()
    return cursor.rowcount > 0


def get_fund_profile_by_code(fund_code: str) -> FundProfile | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM fund_profiles WHERE fund_code = ?",
            (fund_code,),
        ).fetchone()
    if row is None:
        return None
    from app.services.fund_profile import _sanitize_profile_sector_fields

    return _sanitize_profile_sector_fields(
        FundProfile.model_validate(json.loads(row["payload"]))
    )


def save_portfolio_summary(summary: PortfolioSummary) -> PortfolioSummary:
    payload = summary.model_dump(mode="json")
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO portfolio_state (id, payload, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            """,
            (json.dumps(payload, ensure_ascii=False),),
        )
        connection.commit()
    return summary


def save_portfolio_daily_snapshot(snapshot: PortfolioDailySnapshot) -> PortfolioDailySnapshot:
    payload = snapshot.model_dump(mode="json")
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO portfolio_daily_snapshots (snapshot_date, payload, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (
                snapshot.snapshot_date,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        connection.commit()
    return snapshot


def list_portfolio_daily_snapshots(*, limit: int = 30) -> list[dict[str, Any]]:
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT payload FROM portfolio_daily_snapshots
            ORDER BY snapshot_date DESC
            LIMIT ?
            """,
            (limit,),
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


def get_investor_profile() -> InvestorProfile | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM investor_profile_state WHERE id = 1"
        ).fetchone()
    if row is None:
        return None
    return InvestorProfile.model_validate(json.loads(row["payload"]))


def save_investor_profile(profile: InvestorProfile) -> InvestorProfile:
    payload = profile.model_dump(mode="json")
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO investor_profile_state (id, payload, updated_at)
            VALUES (1, ?, CURRENT_TIMESTAMP)
            """,
            (json.dumps(payload, ensure_ascii=False),),
        )
        connection.commit()
    return profile


def get_portfolio_summary() -> PortfolioSummary | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM portfolio_state WHERE id = 1"
        ).fetchone()
    if row is None:
        return None
    data = json.loads(row["payload"])
    if data.get("updated_at") is None:
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
    return PortfolioSummary.model_validate(data)


def get_ocr_text_cache(cache_key: str) -> str | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT raw_text FROM ocr_text_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if row is None:
        return None
    return str(row["raw_text"])


def list_report_chat_messages(report_id: str) -> list[dict[str, Any]]:
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
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO ocr_text_cache (cache_key, raw_text, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (cache_key, raw_text),
        )
        connection.commit()


def get_sector_mapping(sector_label: str) -> dict[str, Any] | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT * FROM sector_mappings WHERE sector_label = ?",
            (sector_label,),
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
    with _connect() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO sector_mappings
            (sector_label, source_type, source_code, source_name, confidence, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
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
