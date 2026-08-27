"""天天基金经理大全：累计从业天数整包落库，荐基只 JOIN。

不存、不算「从业以来年化」。东财全表没有这个数，也不要用
「在管最佳任期回报 ÷ 从业天数」冒充年化。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping

from app.database import (
    get_fund_manager_roster_meta,
    list_fund_manager_roster,
    list_fund_manager_roster_by_codes,
    replace_fund_manager_roster,
)
from app.services.cross_process_lock import CrossProcessLockError, cross_process_lock

_ROSTER_SNAPSHOT_SOURCE = "eastmoney.fund_manager_em"
_ROSTER_TTL_SECONDS = 24 * 60 * 60
_ROSTER_MEMORY_REVALIDATE_SECONDS = 60.0
_MANAGER_NAME_SPLIT = re.compile(r"[/／、,，;；|｜]+")

_ROSTER_FETCH_LOCK = RLock()
_ROSTER_MEMORY_LOCK = RLock()
_ROSTER_MEMORY_ROWS: dict[str, list[dict]] | None = None
_ROSTER_MEMORY_STAMP = ""
_ROSTER_MEMORY_LOADED_AT = 0.0

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _has_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "none", "null", "nan", "--", "—"}
    return True


def format_career_tenure(days: object) -> str | None:
    """对齐天天基金「累计任职时间」：N年又M天。"""

    try:
        whole = int(days)
    except (TypeError, ValueError):
        return None
    if whole < 0:
        return None
    years, rest = divmod(whole, 365)
    if years <= 0:
        return f"{rest}天"
    if rest <= 0:
        return f"{years}年"
    return f"{years}年又{rest}天"


def split_manager_names(value: object) -> list[str]:
    parts = [
        part.strip()
        for part in _MANAGER_NAME_SPLIT.split(str(value or ""))
        if part.strip()
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        ordered.append(part)
    return ordered


def _parse_percent(value: object) -> float | None:
    text = str(value or "").strip().replace("%", "").replace(",", "")
    if not text or text.lower() in {"nan", "--", "none", "null"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_yi(value: object) -> float | None:
    text = (
        str(value or "")
        .strip()
        .replace("亿元", "")
        .replace("亿", "")
        .replace(",", "")
    )
    if not text or text.lower() in {"nan", "--", "none", "null"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def explode_eastmoney_manager_rows(
    raw_rows: list[object] | None,
) -> list[dict[str, Any]]:
    """把东财经理大全一行（一人多基金）拆成 (基金代码, 经理) 行。"""

    exploded: list[dict[str, Any]] = []
    for raw in raw_rows or []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 8:
            continue
        manager_id = str(raw[0] or "").strip()
        manager_name = str(raw[1] or "").strip()
        if not manager_id or not manager_name:
            continue
        company = str(raw[3] or "").strip() or None
        codes = [
            str(code).strip().zfill(6)
            for code in str(raw[4] or "").split(",")
            if str(code).strip().isdigit()
        ]
        try:
            career_days = int(float(str(raw[6]).strip()))
        except (TypeError, ValueError):
            career_days = None
        if career_days is not None and career_days < 0:
            career_days = None
        best_return = _parse_percent(raw[7])
        best_fund_code = str(raw[8] or "").strip() if len(raw) > 8 else ""
        if best_fund_code.isdigit():
            best_fund_code = best_fund_code.zfill(6)
        else:
            best_fund_code = None
        aum_yi = _parse_yi(raw[10]) if len(raw) > 10 else None
        seen: set[str] = set()
        for code in codes:
            if len(code) != 6 or code == "000000" or code in seen:
                continue
            seen.add(code)
            exploded.append(
                {
                    "fund_code": code,
                    "manager_id": manager_id,
                    "manager_name": manager_name,
                    "company": company,
                    "career_days": career_days,
                    "current_best_tenure_return_percent": best_return,
                    "current_best_fund_code": best_fund_code,
                    "current_aum_yi": aum_yi,
                    "source": _ROSTER_SNAPSHOT_SOURCE,
                }
            )
    return exploded


def _roster_is_fresh(meta: dict | None, *, now: datetime | None = None) -> bool:
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
    return (moment - captured_at.astimezone(timezone.utc)).total_seconds() < (
        _ROSTER_TTL_SECONDS
    )


def _pin_roster_rows(rows: list[dict], *, stamp: str) -> dict[str, list[dict]]:
    global _ROSTER_MEMORY_ROWS, _ROSTER_MEMORY_STAMP, _ROSTER_MEMORY_LOADED_AT
    by_code: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("fund_code") or "").zfill(6)
        if code in {"", "000000"}:
            continue
        by_code.setdefault(code, []).append(dict(row))
    with _ROSTER_MEMORY_LOCK:
        _ROSTER_MEMORY_ROWS = by_code
        _ROSTER_MEMORY_STAMP = stamp
        _ROSTER_MEMORY_LOADED_AT = time.monotonic()
    return by_code


def _reset_roster_memory_for_tests() -> None:
    global _ROSTER_MEMORY_ROWS, _ROSTER_MEMORY_STAMP, _ROSTER_MEMORY_LOADED_AT
    with _ROSTER_MEMORY_LOCK:
        _ROSTER_MEMORY_ROWS = None
        _ROSTER_MEMORY_STAMP = ""
        _ROSTER_MEMORY_LOADED_AT = 0.0


def _read_roster_map(*, revalidate: bool) -> dict[str, list[dict]]:
    global _ROSTER_MEMORY_ROWS, _ROSTER_MEMORY_STAMP, _ROSTER_MEMORY_LOADED_AT
    now = time.monotonic()
    with _ROSTER_MEMORY_LOCK:
        cached = _ROSTER_MEMORY_ROWS
        if cached is not None and (
            not revalidate
            or now - _ROSTER_MEMORY_LOADED_AT < _ROSTER_MEMORY_REVALIDATE_SECONDS
        ):
            return cached

    meta = get_fund_manager_roster_meta()
    with _ROSTER_MEMORY_LOCK:
        cached = _ROSTER_MEMORY_ROWS
        cached_stamp = _ROSTER_MEMORY_STAMP
        meta_stamp = str((meta or {}).get("snapshot_available_at") or "")
        if cached is not None and meta is not None and cached_stamp == meta_stamp:
            _ROSTER_MEMORY_LOADED_AT = time.monotonic()
            return cached
        if meta is None:
            _ROSTER_MEMORY_ROWS = None
            _ROSTER_MEMORY_STAMP = ""
            _ROSTER_MEMORY_LOADED_AT = 0.0
            return {}

    rows = list_fund_manager_roster()
    return _pin_roster_rows(rows, stamp=str(meta.get("snapshot_available_at") or ""))


def list_manager_roster_by_codes(
    fund_codes: list[str] | set[str],
) -> dict[str, list[dict]]:
    mapped = _read_roster_map(revalidate=True)
    requested = {
        str(code).strip().zfill(6)
        for code in fund_codes
        if str(code).strip().isdigit()
    }
    if not requested:
        return {}
    if mapped:
        return {
            code: [dict(row) for row in rows]
            for code, rows in mapped.items()
            if code in requested
        }
    return list_fund_manager_roster_by_codes(requested)


def select_managers_for_fund(
    managers: list[Mapping[str, Any]] | None,
    *,
    fund_manager: object = None,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in managers or [] if isinstance(row, Mapping)]
    if not rows:
        return []
    wanted = split_manager_names(fund_manager)
    if wanted:
        by_name = {str(row.get("manager_name") or "").strip(): row for row in rows}
        matched = [by_name[name] for name in wanted if name in by_name]
        if matched:
            return matched
    return rows


def compact_manager_row(row: Mapping[str, Any]) -> dict[str, Any]:
    days = row.get("career_days")
    try:
        career_days = int(days) if days is not None else None
    except (TypeError, ValueError):
        career_days = None
    payload: dict[str, Any] = {
        "manager_id": str(row.get("manager_id") or "").strip() or None,
        "manager_name": str(row.get("manager_name") or "").strip() or None,
        "company": str(row.get("company") or "").strip() or None,
        "career_days": career_days,
        "career_tenure": format_career_tenure(career_days),
        "current_best_tenure_return_percent": row.get(
            "current_best_tenure_return_percent"
        ),
        "current_best_fund_code": row.get("current_best_fund_code"),
        "source": row.get("source") or _ROSTER_SNAPSHOT_SOURCE,
    }
    return {key: value for key, value in payload.items() if value is not None}


def apply_manager_roster_to_row(
    row: dict,
    managers: list[Mapping[str, Any]] | None,
) -> dict:
    selected = select_managers_for_fund(
        managers,
        fund_manager=row.get("fund_manager"),
    )
    if not selected:
        return row
    compacted = [compact_manager_row(item) for item in selected]
    row["fund_managers"] = compacted
    if not _has_value(row.get("fund_manager")):
        names = [
            str(item.get("manager_name") or "").strip()
            for item in compacted
            if item.get("manager_name")
        ]
        if names:
            row["fund_manager"] = "/".join(names)
    days = [
        int(item["career_days"])
        for item in compacted
        if item.get("career_days") is not None
    ]
    if days:
        row["manager_career_days"] = max(days)
        row["manager_career_tenure"] = format_career_tenure(row["manager_career_days"])
        row["manager_career_days_basis"] = "max_among_current"
    returns = [
        float(item["current_best_tenure_return_percent"])
        for item in compacted
        if item.get("current_best_tenure_return_percent") is not None
    ]
    if returns:
        row["manager_best_tenure_return_percent"] = max(returns)
        row["manager_best_tenure_return_basis"] = "eastmoney.current_best_tenure"
    return row


def attach_manager_roster_to_rows(rows: list[dict] | dict[str, dict]) -> None:
    if isinstance(rows, dict):
        items = list(rows.values())
    else:
        items = rows
    codes = {
        str(row.get("fund_code") or "").zfill(6)
        for row in items
        if isinstance(row, dict)
    }
    roster = list_manager_roster_by_codes(codes)
    if not roster:
        return
    for row in items:
        if not isinstance(row, dict):
            continue
        code = str(row.get("fund_code") or "").zfill(6)
        apply_manager_roster_to_row(row, roster.get(code))


def refresh_fund_manager_roster(*, force: bool = False) -> int:
    """整包替换经理名册。失败时保留上一份快照。"""

    meta = get_fund_manager_roster_meta()
    if not force and _roster_is_fresh(meta):
        return int((meta or {}).get("row_count") or 0)

    from app.services.akshare_subprocess import fetch_eastmoney_fund_manager_roster

    try:
        raw_rows = fetch_eastmoney_fund_manager_roster(timeout_seconds=180) or []
    except Exception:  # noqa: BLE001 - stale snapshot stays usable
        logger.exception("fund manager roster refresh failed")
        raw_rows = []
    rows = explode_eastmoney_manager_rows(raw_rows)
    if not rows:
        return int((meta or {}).get("row_count") or 0)
    available_at = _now_utc().isoformat()
    written = replace_fund_manager_roster(
        rows,
        snapshot_available_at=available_at,
        source=_ROSTER_SNAPSHOT_SOURCE,
    )
    _pin_roster_rows(
        list_fund_manager_roster(),
        stamp=available_at,
    )
    return written


def _refresh_roster_under_lock(*, force: bool) -> int:
    try:
        with cross_process_lock(
            "discovery-manager-roster-refresh",
            timeout_seconds=3.0,
        ):
            return refresh_fund_manager_roster(force=force)
    except CrossProcessLockError:
        meta = get_fund_manager_roster_meta()
        return int((meta or {}).get("row_count") or 0)


def run_fund_manager_roster_refresh(*, force: bool = True) -> dict[str, Any]:
    """后台/定时任务入口：整包拉东财经理大全。荐基请求路径不得调用。"""

    with _ROSTER_FETCH_LOCK:
        written = _refresh_roster_under_lock(force=force)
    meta = get_fund_manager_roster_meta() or {}
    row_count = int(meta.get("row_count") or 0)
    return {
        "ok": row_count > 0,
        "written": written,
        "row_count": row_count,
        "snapshot_available_at": meta.get("snapshot_available_at"),
        "source": meta.get("source"),
        "forced": force,
    }
