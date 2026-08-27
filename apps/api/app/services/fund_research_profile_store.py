"""开放式基金规模/经理档案：新浪四张大类表整包落库，规模由定时任务写入。"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from app.database import (
    get_fund_research_profile_meta,
    list_fund_research_profiles,
    list_fund_research_profiles_by_codes,
    replace_fund_research_profiles,
    update_fund_research_profile_scales,
)
from app.services.cross_process_lock import CrossProcessLockError, cross_process_lock
from app.services.sector_quote_cache import get_spot_snapshot_any_age

_PROFILE_SNAPSHOT_SOURCE = "sina.fund_scale_open_sina"
_PROFILE_TTL_SECONDS = 24 * 60 * 60
_PROFILE_MEMORY_REVALIDATE_SECONDS = 60.0
_LEGACY_PROFILE_CACHE_KEY = "fund:discovery_profiles:v5:tracking-reference"

_PROFILE_FETCH_LOCK = RLock()
_PROFILE_MEMORY_LOCK = RLock()
_PROFILE_MEMORY_ROWS: dict[str, dict] | None = None
_PROFILE_MEMORY_STAMP = ""
_PROFILE_MEMORY_LOADED_AT = 0.0

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "none", "null", "nan", "--", "—"}
    return True


def _profile_is_fresh(meta: dict | None, *, now: datetime | None = None) -> bool:
    if not meta:
        return False
    try:
        captured_at = datetime.fromisoformat(
            str(meta.get("snapshot_available_at") or "").replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return False
    if captured_at.tzinfo is None:
        return False
    moment = now or _now_utc()
    return (moment - captured_at.astimezone(timezone.utc)).total_seconds() < _PROFILE_TTL_SECONDS


def _pin_profile_rows(rows: list[dict], *, stamp: str) -> dict[str, dict]:
    global _PROFILE_MEMORY_ROWS, _PROFILE_MEMORY_STAMP, _PROFILE_MEMORY_LOADED_AT
    by_code = {
        str(row.get("fund_code") or "").zfill(6): dict(row)
        for row in rows
        if isinstance(row, dict)
        and str(row.get("fund_code") or "").zfill(6) not in {"", "000000"}
    }
    with _PROFILE_MEMORY_LOCK:
        _PROFILE_MEMORY_ROWS = by_code
        _PROFILE_MEMORY_STAMP = stamp
        _PROFILE_MEMORY_LOADED_AT = time.monotonic()
    return by_code


def _reset_profile_memory_for_tests() -> None:
    global _PROFILE_MEMORY_ROWS, _PROFILE_MEMORY_STAMP, _PROFILE_MEMORY_LOADED_AT
    with _PROFILE_MEMORY_LOCK:
        _PROFILE_MEMORY_ROWS = None
        _PROFILE_MEMORY_STAMP = ""
        _PROFILE_MEMORY_LOADED_AT = 0.0


def _read_profile_map(*, revalidate: bool) -> dict[str, dict]:
    global _PROFILE_MEMORY_ROWS, _PROFILE_MEMORY_STAMP, _PROFILE_MEMORY_LOADED_AT
    now = time.monotonic()
    with _PROFILE_MEMORY_LOCK:
        cached = _PROFILE_MEMORY_ROWS
        if cached is not None and (
            not revalidate
            or now - _PROFILE_MEMORY_LOADED_AT < _PROFILE_MEMORY_REVALIDATE_SECONDS
        ):
            return cached

    meta = get_fund_research_profile_meta()
    with _PROFILE_MEMORY_LOCK:
        cached = _PROFILE_MEMORY_ROWS
        cached_stamp = _PROFILE_MEMORY_STAMP
        meta_stamp = str((meta or {}).get("snapshot_available_at") or "")
        if cached is not None and meta is not None and cached_stamp == meta_stamp:
            _PROFILE_MEMORY_LOADED_AT = time.monotonic()
            return cached
        if meta is None:
            _PROFILE_MEMORY_ROWS = None
            _PROFILE_MEMORY_STAMP = ""
            _PROFILE_MEMORY_LOADED_AT = 0.0
            return {}

    rows = list_fund_research_profiles()
    return _pin_profile_rows(rows, stamp=str(meta.get("snapshot_available_at") or ""))


def _import_legacy_profile_blob_if_needed() -> dict[str, dict]:
    if get_fund_research_profile_meta() is not None:
        return _read_profile_map(revalidate=False)
    payload = get_spot_snapshot_any_age(_LEGACY_PROFILE_CACHE_KEY)
    rows = (payload or {}).get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return {}
    available_at = str(
        payload.get("snapshot_available_at")
        or payload.get("checked_at")
        or _now_utc().isoformat()
    )
    replace_fund_research_profiles(
        [row for row in rows if isinstance(row, dict)],
        snapshot_available_at=available_at,
        source=str(payload.get("source") or "legacy_profile_blob"),
    )
    imported = list_fund_research_profiles()
    return _pin_profile_rows(imported, stamp=available_at)


def refresh_fund_research_profiles(*, force: bool = False) -> int:
    """整包替换规模/经理表。失败时保留上一份快照。"""

    meta = get_fund_research_profile_meta()
    if not force and _profile_is_fresh(meta):
        return int((meta or {}).get("row_count") or 0)

    from app.services.akshare_subprocess import fetch_open_fund_scale_universe

    try:
        rows = fetch_open_fund_scale_universe(timeout_seconds=90) or []
    except Exception:  # noqa: BLE001 - stale snapshot stays usable
        logger.exception("fund research profile refresh failed")
        rows = []
    if not rows:
        return int((meta or {}).get("row_count") or 0)
    available_at = _now_utc().isoformat()
    written = replace_fund_research_profiles(
        rows,
        snapshot_available_at=available_at,
        source=_PROFILE_SNAPSHOT_SOURCE,
    )
    _pin_profile_rows(
        list_fund_research_profiles(),
        stamp=available_at,
    )
    return written


def _refresh_profiles_under_lock(*, force: bool) -> int:
    try:
        with cross_process_lock(
            "discovery-research-profile-refresh",
            timeout_seconds=3.0,
        ):
            return refresh_fund_research_profiles(force=force)
    except CrossProcessLockError:
        meta = get_fund_research_profile_meta()
        return int((meta or {}).get("row_count") or 0)


def run_fund_research_profile_refresh(*, force: bool = True) -> dict[str, Any]:
    """后台/定时任务入口：整包拉新浪四表。荐基请求路径不得调用。"""

    with _PROFILE_FETCH_LOCK:
        written = _refresh_profiles_under_lock(force=force)
    meta = get_fund_research_profile_meta() or {}
    row_count = int(meta.get("row_count") or 0)
    return {
        "ok": row_count > 0,
        "written": written,
        "row_count": row_count,
        "snapshot_available_at": meta.get("snapshot_available_at"),
        "source": meta.get("source"),
        "forced": force,
    }


def ensure_fund_research_profiles(*, blocking_if_empty: bool = False) -> dict[str, dict]:
    """只读当前档案。新浪整包由后台循环/定时任务刷新，请求路径不拉源。"""

    del blocking_if_empty
    rows = _read_profile_map(revalidate=True)
    if not rows:
        rows = _import_legacy_profile_blob_if_needed()
    return rows


def list_research_profiles_for_codes(fund_codes: list[str] | set[str]) -> dict[str, dict]:
    mapped = _read_profile_map(revalidate=True)
    if not mapped:
        mapped = ensure_fund_research_profiles()
    requested = {
        str(code).strip().zfill(6)
        for code in fund_codes
        if str(code).strip().isdigit()
    }
    if not requested:
        return {}
    if mapped:
        return {
            code: dict(row)
            for code, row in mapped.items()
            if code in requested
        }
    return list_fund_research_profiles_by_codes(requested)


def persist_computed_fund_scales(rows: list[dict] | None) -> int:
    """把刚算出的规模立刻写回 SQL 和进程内档案，不改整包快照时点。"""

    payload = [row for row in (rows or []) if isinstance(row, dict)]
    if not payload:
        return 0
    written = update_fund_research_profile_scales(payload)
    if not written:
        return 0
    by_code = {
        str(row.get("fund_code") or "").zfill(6): row
        for row in payload
        if str(row.get("fund_code") or "").zfill(6) not in {"", "000000"}
    }
    with _PROFILE_MEMORY_LOCK:
        cached = _PROFILE_MEMORY_ROWS
        if cached is None:
            return written
        for code, row in by_code.items():
            existing = cached.get(code)
            if existing is None:
                continue
            if row.get("fund_scale_yi") is not None:
                existing["fund_scale_yi"] = row["fund_scale_yi"]
            if row.get("fund_scale_basis") is not None:
                existing["fund_scale_basis"] = row["fund_scale_basis"]
    return written


def overlay_research_on_universe_rows(rows: list[dict]) -> list[dict]:
    """给目录行补规模/经理姓名/从业天数/自算风险。有补丁才 copy，避免污染目录内存快照。"""

    if not rows:
        return rows
    profiles = _read_profile_map(revalidate=True)
    from app.services.fund_manager_roster import (
        apply_manager_roster_to_row,
        list_manager_roster_by_codes,
    )
    from app.services.fund_risk_metrics import apply_risk_metrics_to_row, list_all_risk_metrics

    risks = {
        str(row.get("fund_code") or "").zfill(6): row
        for row in list_all_risk_metrics()
        if isinstance(row, dict)
    }
    codes = {
        str(row.get("fund_code") or "").zfill(6)
        for row in rows
        if isinstance(row, dict)
    }
    roster = list_manager_roster_by_codes(codes)
    if not profiles and not risks and not roster:
        return rows
    overlay_fields = (
        "fund_scale_yi",
        "fund_shares_yi",
        "fund_manager",
        "established_date",
        "fund_scale_basis",
    )
    out: list[dict] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        code = str(raw.get("fund_code") or "").zfill(6)
        extra_p = profiles.get(code)
        extra_r = risks.get(code)
        extra_m = roster.get(code)
        if extra_p is None and extra_r is None and not extra_m:
            out.append(raw)
            continue
        item = dict(raw)
        if extra_p is not None:
            for key in overlay_fields:
                if item.get(key) is None and extra_p.get(key) is not None:
                    item[key] = extra_p[key]
            if item.get("fund_scale_yi") is not None:
                item.setdefault(
                    "fund_scale_yi_available_at",
                    extra_p.get("snapshot_available_at"),
                )
                item.setdefault(
                    "fund_scale_yi_source",
                    extra_p.get("source") or _PROFILE_SNAPSHOT_SOURCE,
                )
        apply_risk_metrics_to_row(item, extra_r)
        apply_manager_roster_to_row(item, extra_m)
        out.append(item)
    return out


def profile_row_for_candidate(
    row: dict,
    *,
    managers: list[dict] | None = None,
) -> dict:
    """把表行收成荐基档案字段，并标 complete/partial。"""

    item = dict(row)
    code = str(item.get("fund_code") or "").zfill(6)
    item["fund_code"] = code
    from app.services.fund_manager_roster import (
        apply_manager_roster_to_row,
        list_manager_roster_by_codes,
    )

    roster_rows = managers
    if roster_rows is None:
        roster_rows = list_manager_roster_by_codes([code]).get(code)
    apply_manager_roster_to_row(item, roster_rows)
    item["profile_source"] = item.get("source") or _PROFILE_SNAPSHOT_SOURCE
    item["profile_sources"] = [item["profile_source"]]
    item["profile_checked_at"] = item.get("snapshot_available_at")
    from app.services.fund_scale import profile_has_scale_input

    missing = []
    if not profile_has_scale_input(item):
        missing.append("fund_scale_yi")
    missing.extend(
        field
        for field in ("established_date", "fund_manager")
        if not _has_value(item.get(field))
    )
    item["profile_missing_fields"] = missing
    item["profile_status"] = "partial" if missing else "complete"
    return item
