from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.config import get_settings
from datetime import datetime, timezone

from app.models import ChatMessage, FundProfile, PortfolioSummary, Report


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


def delete_report(report_id: str) -> bool:
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM reports WHERE id = ?",
            (report_id,),
        )
        connection.commit()
    return cursor.rowcount > 0


def save_fund_profile(profile: FundProfile) -> FundProfile:
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
    with _connect() as connection:
        rows = connection.execute(
            "SELECT payload FROM fund_profiles ORDER BY updated_at DESC"
        ).fetchall()
    return [FundProfile.model_validate(json.loads(row["payload"])) for row in rows]


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
    return FundProfile.model_validate(json.loads(row["payload"]))


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
