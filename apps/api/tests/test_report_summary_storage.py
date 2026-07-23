from __future__ import annotations

import json

from app.database import _connect, list_discovery_reports, list_reports


def test_daily_report_summary_is_lazily_backfilled_then_read_without_payload() -> None:
    payload = {
        "id": "daily-large",
        "created_at": "2026-07-24T00:00:00+00:00",
        "title": "日报",
        "summary": "摘要",
        "risk": {"level": "medium"},
        "large": "x" * 100_000,
    }
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO reports (
                id, created_at, payload, summary_payload, userId
            )
            VALUES (?, ?, ?, NULL, ?)
            """,
            (
                payload["id"],
                payload["created_at"],
                json.dumps(payload, ensure_ascii=False),
                1,
            ),
        )

    assert list_reports() == [
        {
            "id": "daily-large",
            "created_at": payload["created_at"],
            "title": "日报",
            "summary": "摘要",
            "risk": {"level": "medium"},
        }
    ]
    with _connect() as connection:
        stored = connection.execute(
            "SELECT summary_payload FROM reports WHERE id = ?",
            ("daily-large",),
        ).fetchone()["summary_payload"]
        narrow = connection.execute(
            """
            SELECT summary_payload FROM report_summaries
            WHERE userId = 1 AND report_id = ?
            """,
            ("daily-large",),
        ).fetchone()["summary_payload"]
        connection.execute(
            "UPDATE reports SET payload = ? WHERE id = ?",
            ("not-json", "daily-large"),
        )
    assert stored
    assert narrow
    assert list_reports()[0]["id"] == "daily-large"


def test_discovery_summary_is_lazily_backfilled_then_read_without_payload() -> None:
    payload = {
        "id": "discovery-large",
        "created_at": "2026-07-24T00:00:00+00:00",
        "title": "荐基",
        "summary": "摘要",
        "target_sectors": ["半导体"],
        "large": "x" * 500_000,
    }
    with _connect() as connection:
        connection.execute(
            """
            INSERT INTO fund_discovery_reports (
                id, created_at, payload, summary_payload, userId
            )
            VALUES (?, ?, ?, NULL, ?)
            """,
            (
                payload["id"],
                payload["created_at"],
                json.dumps(payload, ensure_ascii=False),
                1,
            ),
        )

    assert list_discovery_reports() == [
        {
            "id": "discovery-large",
            "created_at": payload["created_at"],
            "title": "荐基",
            "summary": "摘要",
            "target_sectors": ["半导体"],
        }
    ]
    with _connect() as connection:
        stored = connection.execute(
            """
            SELECT summary_payload FROM fund_discovery_reports
            WHERE id = ?
            """,
            ("discovery-large",),
        ).fetchone()["summary_payload"]
        narrow = connection.execute(
            """
            SELECT summary_payload
            FROM fund_discovery_report_summaries
            WHERE userId = 1 AND report_id = ?
            """,
            ("discovery-large",),
        ).fetchone()["summary_payload"]
        connection.execute(
            """
            UPDATE fund_discovery_reports SET payload = ?
            WHERE id = ?
            """,
            ("not-json", "discovery-large"),
        )
    assert stored
    assert narrow
    assert list_discovery_reports()[0]["id"] == "discovery-large"
