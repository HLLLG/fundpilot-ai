from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from app.config import get_settings
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


def prune_expired_news_cache(
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> int:
    """删除超过保留期的新闻缓存行（key 按日切分，不清理会一直堆积）。"""
    days = (
        int(retention_days)
        if retention_days is not None
        else int(get_settings().spot_cache_retention_days)
    )
    days = max(1, days)
    cutoff = (_as_utc(now) - timedelta(days=days)).isoformat()
    with _connect() as connection:
        _ensure_cache_table(connection)
        cursor = connection.execute(
            "DELETE FROM news_cache WHERE updated_at < ?",
            (cutoff,),
        )
        connection.commit()
        try:
            return max(0, int(cursor.rowcount or 0))
        except (TypeError, ValueError):
            return 0

