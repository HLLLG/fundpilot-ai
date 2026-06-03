from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from app.database import _connect
from app.models import NewsItem


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


def get_cached_news(topic: str, cache_date: str | None = None) -> list[NewsItem] | None:
    key = _cache_key(topic, cache_date)
    with _connect() as connection:
        _ensure_cache_table(connection)
        row = connection.execute(
            "SELECT payload FROM news_cache WHERE cache_key = ?",
            (key,),
        ).fetchone()
    if row is None:
        return None
    raw = json.loads(row["payload"])
    return [NewsItem.model_validate(item) for item in raw]


def save_cached_news(
    topic: str,
    items: list[NewsItem],
    cache_date: str | None = None,
) -> None:
    key = _cache_key(topic, cache_date)
    payload = json.dumps([item.model_dump(mode="json") for item in items], ensure_ascii=False)
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as connection:
        _ensure_cache_table(connection)
        connection.execute(
            """
            INSERT OR REPLACE INTO news_cache (cache_key, payload, updated_at)
            VALUES (?, ?, ?)
            """,
            (key, payload, now),
        )
        connection.commit()
