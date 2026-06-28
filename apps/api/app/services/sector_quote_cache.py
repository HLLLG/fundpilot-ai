from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.database import _connect

_MEMORY: dict[str, tuple[float, dict]] = {}
_PROCESS_BOOT_AT: datetime | None = None


def mark_process_boot() -> datetime:
    """记录进程启动时刻；早于该时刻写入的快照视为跨进程遗留缓存。"""
    global _PROCESS_BOOT_AT
    _PROCESS_BOOT_AT = datetime.now(timezone.utc)
    return _PROCESS_BOOT_AT


def snapshot_refreshed_before_process_boot(refreshed_at: str | None) -> bool:
    """快照是否在本进程启动前写入（含 SQLite 遗留、缺失 refreshed_at）。"""
    if _PROCESS_BOOT_AT is None:
        return False
    if not refreshed_at:
        return True
    try:
        ts = datetime.fromisoformat(str(refreshed_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc) < _PROCESS_BOOT_AT
    except ValueError:
        return True


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


def get_spot_snapshot_any_age(cache_key: str) -> dict | None:
    """读取缓存（忽略 TTL），用于 stale-while-revalidate 回退。"""
    now = datetime.now(timezone.utc).timestamp()
    cached = _MEMORY.get(cache_key)
    if cached is not None:
        return cached[1]

    with _connect() as connection:
        _ensure_cache_table(connection)
        row = connection.execute(
            "SELECT payload, updated_at FROM sector_spot_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if row is None:
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
