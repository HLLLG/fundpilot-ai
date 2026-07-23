from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from datetime import datetime, timezone
from threading import RLock

from app.config import get_settings
from app.database import _connect
from app.services.performance_metrics import record_cache_event

_MEMORY: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_MEMORY_MAX_ENTRIES = 512
_MEMORY_LOCK = RLock()
_PROCESS_BOOT_AT: datetime | None = None
_SCHEMA_LOCK = RLock()
_SCHEMA_READY_KEY: tuple[str, str] | None = None


def _get_memory_snapshot(
    cache_key: str,
    now: float,
    *,
    ttl_seconds: float | None,
) -> tuple[bool, dict | None]:
    with _MEMORY_LOCK:
        cached = _MEMORY.get(cache_key)
        if cached is None:
            return False, None
        cached_at, payload = cached
        if ttl_seconds is not None and now - cached_at > ttl_seconds:
            _MEMORY.pop(cache_key, None)
            return False, None
        _MEMORY.move_to_end(cache_key)
        return True, payload


def _save_memory_snapshot(cache_key: str, cached_at: float, payload: dict) -> None:
    with _MEMORY_LOCK:
        _MEMORY[cache_key] = (cached_at, payload)
        _MEMORY.move_to_end(cache_key)
        while len(_MEMORY) > _MEMORY_MAX_ENTRIES:
            _MEMORY.popitem(last=False)


def _updated_at_timestamp(value: object) -> float:
    updated_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at.astimezone(timezone.utc).timestamp()


def mark_process_boot() -> datetime:
    """记录进程启动时刻；早于该时刻写入的快照视为跨进程遗留缓存。"""
    global _PROCESS_BOOT_AT
    with _MEMORY_LOCK:
        _PROCESS_BOOT_AT = datetime.now(timezone.utc)
        return _PROCESS_BOOT_AT


def snapshot_refreshed_before_process_boot(refreshed_at: str | None) -> bool:
    """快照是否在本进程启动前写入（含 SQLite 遗留、缺失 refreshed_at）。"""
    with _MEMORY_LOCK:
        process_boot_at = _PROCESS_BOOT_AT
    if process_boot_at is None:
        return False
    if not refreshed_at:
        return True
    try:
        ts = datetime.fromisoformat(str(refreshed_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc) < process_boot_at
    except ValueError:
        return True


def _ensure_cache_table(connection: sqlite3.Connection) -> None:
    global _SCHEMA_READY_KEY
    dialect = str(getattr(connection, "dialect", "sqlite"))
    settings = get_settings()
    key = (
        dialect,
        settings.database_url or str(settings.db_path.resolve()),
    )
    if _SCHEMA_READY_KEY == key:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY_KEY == key:
            return
        # Production MySQL schema ownership belongs to the one-shot deployment
        # bootstrap. Request paths must never take metadata locks.
        if dialect != "mysql":
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sector_spot_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        _SCHEMA_READY_KEY = key


def get_spot_snapshot(cache_key: str, *, ttl_seconds: float) -> dict | None:
    now = datetime.now(timezone.utc).timestamp()
    found, payload = _get_memory_snapshot(
        cache_key,
        now,
        ttl_seconds=ttl_seconds,
    )
    if found:
        record_cache_event(cache_key, "hit_memory")
        return payload
    record_cache_event(cache_key, "miss_memory")

    with _connect() as connection:
        _ensure_cache_table(connection)
        row = connection.execute(
            "SELECT payload, updated_at FROM sector_spot_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if row is None:
        record_cache_event(cache_key, "miss")
        return None

    updated_at = _updated_at_timestamp(row["updated_at"])
    age = now - updated_at
    if age > ttl_seconds:
        record_cache_event(cache_key, "stale")
        return None

    payload = json.loads(row["payload"])
    _save_memory_snapshot(cache_key, updated_at, payload)
    record_cache_event(cache_key, "hit_durable")
    return payload


def get_spot_snapshot_any_age(cache_key: str) -> dict | None:
    """读取缓存（忽略 TTL），用于 stale-while-revalidate 回退。"""
    now = datetime.now(timezone.utc).timestamp()
    found, payload = _get_memory_snapshot(cache_key, now, ttl_seconds=None)
    if found:
        record_cache_event(cache_key, "stale_hit_memory")
        return payload

    with _connect() as connection:
        _ensure_cache_table(connection)
        row = connection.execute(
            "SELECT payload, updated_at FROM sector_spot_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if row is None:
        record_cache_event(cache_key, "miss")
        return None

    payload = json.loads(row["payload"])
    # ``any_age`` is a delivery policy, not a refresh. Preserve the durable
    # capture time when promoting a stale row into process memory so a later
    # TTL-aware read cannot mistake the promotion for a fresh provider fetch.
    _save_memory_snapshot(
        cache_key,
        _updated_at_timestamp(row["updated_at"]),
        payload,
    )
    record_cache_event(cache_key, "stale_hit_durable")
    return payload


def save_spot_snapshot(cache_key: str, payload: dict) -> None:
    now = datetime.now(timezone.utc)
    encoded = json.dumps(payload, ensure_ascii=False)
    _save_memory_snapshot(cache_key, now.timestamp(), payload)
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
    record_cache_event(cache_key, "refresh")
