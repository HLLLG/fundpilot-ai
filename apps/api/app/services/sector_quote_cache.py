from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.database import _connect

_MEMORY: dict[str, tuple[float, dict]] = {}


def _ensure_cache_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sector_spot_cache (
            cache_key TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def get_spot_snapshot(cache_key: str, *, ttl_seconds: float) -> dict | None:
    now = datetime.now(timezone.utc).timestamp()
    cached = _MEMORY.get(cache_key)
    if cached is not None:
        ts, payload = cached
        if now - ts <= ttl_seconds:
            return payload

    with _connect() as connection:
        _ensure_cache_table(connection)
        row = connection.execute(
            "SELECT payload, updated_at FROM sector_spot_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if row is None:
        return None

    updated_at = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
    age = now - updated_at.timestamp()
    if age > ttl_seconds:
        return None

    payload = json.loads(row["payload"])
    _MEMORY[cache_key] = (now, payload)
    return payload


def save_spot_snapshot(cache_key: str, payload: dict) -> None:
    now = datetime.now(timezone.utc)
    encoded = json.dumps(payload, ensure_ascii=False)
    _MEMORY[cache_key] = (now.timestamp(), payload)
    with _connect() as connection:
        _ensure_cache_table(connection)
        connection.execute(
            """
            INSERT OR REPLACE INTO sector_spot_cache (cache_key, payload, updated_at)
            VALUES (?, ?, ?)
            """,
            (cache_key, encoded, now.isoformat()),
        )
        connection.commit()
