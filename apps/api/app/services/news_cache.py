from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from app.database import _connect
from app.models import NewsItem
NEWS_CACHE_STALE_SECONDS = 900


def _ensure_cache_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS news_cache (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _cache_key(topic: str, cache_date: str | None = None) -> str:
    day = cache_date or date.today().isoformat()
    return f"{topic.strip().lower()}:{day}"


def get_cached_news(
    topic: str,
    cache_date: str | None = None,
    *,
    max_age_seconds: int | None = None,
    now: datetime | None = None,
) -> list[NewsItem] | None:
    key = _cache_key(topic, cache_date)
    with _connect() as connection:
        _ensure_cache_table(connection)
        row = connection.execute(
            "SELECT payload, updated_at FROM news_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return None
    if max_age_seconds is not None:
        updated_at = str(row["updated_at"] or "")
        try:
            parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            age = (_as_utc(now) - _as_utc(parsed)).total_seconds()
            if age < 0 or age > max_age_seconds:
                return None
        except ValueError:
            return None
    raw = json.loads(row["payload"])
    return [NewsItem.model_validate(item) for item in raw]


def save_cached_news(
    topic: str,
    items: list[NewsItem],
    cache_date: str | None = None,
    *,
    now: datetime | None = None,
) -> None:
    key = _cache_key(topic, cache_date)
    payload = json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False)
    updated_at = _as_utc(now).isoformat()
    with _connect() as connection:
        _ensure_cache_table(connection)
        connection.execute(
            """
            INSERT OR REPLACE INTO news_cache (cache_key, payload, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, payload, updated_at),
        )
        connection.commit()


def _as_utc(value: datetime | None) -> datetime:
    resolved = value or datetime.now(timezone.utc)
    if resolved.tzinfo is None:
        return resolved.replace(tzinfo=timezone.utc)
    return resolved.astimezone(timezone.utc)
