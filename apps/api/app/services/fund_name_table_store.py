from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

_CACHE_VERSION = 1


def _cache_path() -> Path:
    override = os.getenv("FUND_AI_FUND_NAME_CACHE_PATH")
    if override:
        return Path(override)
    return get_settings().db_path.parent / "fund_name_table_cache.json"


def clear_persisted_fund_name_table_cache() -> None:
    path = _cache_path()
    if path.is_file():
        path.unlink(missing_ok=True)


def _cache_ttl_seconds() -> int:
    raw = os.getenv("FUND_AI_FUND_NAME_TABLE_TTL_SECONDS", "86400")
    try:
        return max(300, int(raw))
    except ValueError:
        return 86400


def load_cached_fund_name_table(
    *, allow_stale: bool = False
) -> list[tuple[str, str]] | None:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != _CACHE_VERSION:
            return None
        fetched_at = payload.get("fetched_at")
        rows = payload.get("rows")
        if not fetched_at or not isinstance(rows, list):
            return None
        fetched = datetime.fromisoformat(str(fetched_at))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        if age > _cache_ttl_seconds() and not allow_stale:
            return None
        table = [(str(code), str(name)) for code, name in rows if code and name]
        return table or None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def save_fund_name_table_cache(table: list[tuple[str, str]]) -> None:
    if not table:
        return
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _CACHE_VERSION,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "rows": [[code, name] for code, name in table],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
