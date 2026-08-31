"""全市场滚动 3 年净值：日更截面、历史回填、过期删除。"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path
from threading import RLock, Thread
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.database import (
    get_fund_nav_series_meta,
    list_fund_daily_catalogue,
    list_fund_nav_series_fund_codes,
    purge_fund_nav_series_before,
    upsert_fund_nav_series,
)
from app.services.cross_process_lock import CrossProcessLockError, cross_process_lock
from app.services.fund_sharpe import shift_calendar_years

NAV_SERIES_RETENTION_YEARS = 3
NAV_SERIES_BACKFILL_TRADING_DAYS = 800
NAV_SERIES_SOURCE_DAILY = "eastmoney.fund_jjjz"
NAV_SERIES_SOURCE_HISTORY = "akshare.fund_open_fund_info_em"
_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_BACKFILL_SLEEP_SECONDS = 0.15
_BACKFILL_LOCK_NAME = "fund-nav-series-backfill"
_DAILY_LOCK_NAME = "fund-nav-series-daily"
_DAILY_STATE_LOCK = RLock()
_BACKFILL_STATE_LOCK = RLock()
_DAILY_IN_FLIGHT = False
_BACKFILL_IN_FLIGHT = False

logger = logging.getLogger(__name__)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _shanghai_today() -> date:
    return datetime.now(_SHANGHAI_TZ).date()


def _parse_iso_datetime(raw: object) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def daily_nav_series_already_ran_today(*, today: date | None = None) -> bool:
    """同一上海自然日已成功写过日更状态则不再重复拉全表。"""

    stamp = _parse_iso_datetime(_load_status().get("daily_updated_at"))
    if stamp is None:
        return False
    return stamp.astimezone(_SHANGHAI_TZ).date() == (today or _shanghai_today())


def _status_path() -> Path:
    return get_settings().db_path.parent / "fund_nav_series_backfill_status.json"


def _load_status() -> dict[str, Any]:
    path = _status_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_status(payload: dict[str, Any]) -> None:
    path = _status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.info("failed to persist nav series backfill status: %s", exc)


def _filled_codes(status: dict[str, Any]) -> set[str]:
    raw = status.get("filled_codes")
    if not isinstance(raw, list):
        return set()
    filled: set[str] = set()
    for item in raw:
        code = str(item or "").strip().zfill(6)
        if len(code) == 6 and code.isdigit() and code != "000000":
            filled.add(code)
    return filled


def _normalize_fund_code(raw: object) -> str | None:
    code = str(raw or "").strip().zfill(6)
    if len(code) != 6 or not code.isdigit() or code == "000000":
        return None
    return code


def retention_cutoff_date(today: date | None = None) -> date:
    return shift_calendar_years(today or date.today(), NAV_SERIES_RETENTION_YEARS)


def expand_daily_snapshot_to_points(
    snapshot: Mapping[str, Any],
    *,
    available_at: str | None = None,
    source: str = NAV_SERIES_SOURCE_DAILY,
) -> list[dict[str, Any]]:
    """把全市场最新净值表展开成可入库的点。"""

    stamp = str(available_at or _now_utc_iso())
    latest_date = str(snapshot.get("latest_date") or "").strip()[:10]
    prior_date = str(snapshot.get("prior_date") or "").strip()[:10]
    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        return []
    points: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        code = _normalize_fund_code(raw.get("fund_code"))
        if code is None:
            continue
        latest_nav = raw.get("latest_nav")
        prior_nav = raw.get("prior_nav")
        growth = raw.get("daily_growth_percent", raw.get("daily_growth"))
        if latest_date and latest_nav is not None:
            points.append(
                {
                    "fund_code": code,
                    "nav_date": latest_date,
                    "unit_nav": latest_nav,
                    "daily_growth_percent": growth,
                    "source": source,
                    "snapshot_available_at": stamp,
                }
            )
        if prior_date and prior_nav is not None and prior_date != latest_date:
            points.append(
                {
                    "fund_code": code,
                    "nav_date": prior_date,
                    "unit_nav": prior_nav,
                    "daily_growth_percent": None,
                    "source": source,
                    "snapshot_available_at": stamp,
                }
            )
    return points


def points_from_nav_history(
    fund_code: str,
    payload: Mapping[str, Any] | None,
    *,
    available_at: str | None = None,
    source: str = NAV_SERIES_SOURCE_HISTORY,
) -> list[dict[str, Any]]:
    code = _normalize_fund_code(fund_code)
    if code is None or not isinstance(payload, Mapping):
        return []
    rows = payload.get("data")
    if not isinstance(rows, list):
        return []
    stamp = str(available_at or _now_utc_iso())
    points: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        points.append(
            {
                "fund_code": code,
                "nav_date": str(raw.get("date") or "")[:10],
                "unit_nav": raw.get("nav"),
                "daily_growth_percent": raw.get(
                    "daily_growth_percent", raw.get("daily_growth")
                ),
                "source": source,
                "snapshot_available_at": stamp,
            }
        )
    return points


def purge_expired_fund_nav_series(*, today: date | None = None) -> int:
    return purge_fund_nav_series_before(retention_cutoff_date(today).isoformat())


def sync_daily_fund_nav_series() -> dict[str, Any]:
    """用东财全市场最新净值表覆盖写入最近 1～2 日，并删掉 3 年以前的点。"""

    from app.services.akshare_subprocess import fetch_open_fund_daily_nav_snapshot

    available_at = _now_utc_iso()
    snapshot = fetch_open_fund_daily_nav_snapshot()
    if not snapshot:
        return {
            "written": 0,
            "purged": 0,
            "latest_date": None,
            "snapshot_available_at": available_at,
            "error": "daily_nav_snapshot_unavailable",
        }
    points = expand_daily_snapshot_to_points(snapshot, available_at=available_at)
    written = upsert_fund_nav_series(
        points,
        snapshot_available_at=available_at,
        source=NAV_SERIES_SOURCE_DAILY,
    )
    purged = purge_expired_fund_nav_series()
    status = _load_status()
    status["daily_as_of"] = snapshot.get("latest_date")
    status["daily_updated_at"] = available_at
    _save_status(status)
    return {
        "written": written,
        "purged": purged,
        "latest_date": snapshot.get("latest_date"),
        "prior_date": snapshot.get("prior_date"),
        "snapshot_available_at": available_at,
        "fund_count": len(
            {
                point["fund_code"]
                for point in points
                if isinstance(point.get("fund_code"), str)
            }
        ),
    }


def _candidate_backfill_codes() -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for row in list_fund_daily_catalogue():
        code = _normalize_fund_code(row.get("fund_code"))
        if code is None or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if codes:
        return codes
    return list_fund_nav_series_fund_codes()


def backfill_fund_nav_series(
    *,
    limit: int | None = None,
    fund_codes: Iterable[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """逐只回填近 800 个交易日（覆盖 3 年窗口），已拉过的代码默认跳过。"""

    from app.services.akshare_subprocess import fetch_fund_nav_history

    status = _load_status()
    filled = set() if force else _filled_codes(status)
    targets = [
        code
        for raw in (list(fund_codes) if fund_codes is not None else _candidate_backfill_codes())
        if (code := _normalize_fund_code(raw)) is not None and code not in filled
    ]
    if limit is not None:
        targets = targets[: max(0, int(limit))]
    available_at = _now_utc_iso()
    written = 0
    fetched = 0
    errors = 0
    last_error: str | None = None
    for index, code in enumerate(targets):
        try:
            payload = fetch_fund_nav_history(
                code,
                trading_days=NAV_SERIES_BACKFILL_TRADING_DAYS,
            )
            points = points_from_nav_history(code, payload, available_at=available_at)
            if points:
                written += upsert_fund_nav_series(
                    points,
                    snapshot_available_at=available_at,
                    source=NAV_SERIES_SOURCE_HISTORY,
                )
            filled.add(code)
            fetched += 1
        except Exception as exc:  # noqa: BLE001 - 单只失败不中断整包回填
            errors += 1
            last_error = f"{code}:{exc}"
            logger.info("fund nav series backfill failed for %s: %s", code, exc)
        if (index + 1) % 20 == 0 or index + 1 == len(targets):
            status["filled_codes"] = sorted(filled)
            status["filled_count"] = len(filled)
            status["updated_at"] = _now_utc_iso()
            status["last_code"] = code
            status["last_error"] = last_error
            _save_status(status)
        if index + 1 < len(targets) and _BACKFILL_SLEEP_SECONDS > 0:
            time.sleep(_BACKFILL_SLEEP_SECONDS)
    purged = purge_expired_fund_nav_series()
    status["filled_codes"] = sorted(filled)
    status["filled_count"] = len(filled)
    status["updated_at"] = _now_utc_iso()
    status["last_error"] = last_error
    _save_status(status)
    return {
        "attempted": len(targets),
        "fetched": fetched,
        "written": written,
        "errors": errors,
        "filled_count": len(filled),
        "purged": purged,
        "remaining": max(0, len(_candidate_backfill_codes()) - len(filled)),
        "snapshot_available_at": available_at,
        "last_error": last_error,
    }


def run_daily_nav_series_and_risk() -> dict[str, Any]:
    """日更净值、删过期点，再按表重算全市场 1/3 年回撤与夏普。"""

    daily = sync_daily_fund_nav_series()
    from app.services.fund_risk_metrics import refresh_fund_risk_metrics_from_nav_series

    risk_written = refresh_fund_risk_metrics_from_nav_series()
    meta = get_fund_nav_series_meta() or {}
    return {
        "daily": daily,
        "risk_written": risk_written,
        "series": meta,
        "coverage": {
            "fund_count": int(meta.get("fund_count") or 0),
            "row_count": int(meta.get("row_count") or 0),
        },
    }


def schedule_daily_nav_series_sync(*, force: bool = False) -> None:
    global _DAILY_IN_FLIGHT
    if not force and daily_nav_series_already_ran_today():
        logger.info("fund nav series daily sync skipped; already ran today")
        return
    with _DAILY_STATE_LOCK:
        if _DAILY_IN_FLIGHT:
            return
        _DAILY_IN_FLIGHT = True
    try:
        Thread(
            target=_run_daily_nav_series_sidecar,
            name="fund-nav-series-daily",
            daemon=True,
        ).start()
    except Exception:  # noqa: BLE001
        with _DAILY_STATE_LOCK:
            _DAILY_IN_FLIGHT = False
        logger.exception("failed to start fund nav series daily thread")


def schedule_nav_series_backfill() -> None:
    global _BACKFILL_IN_FLIGHT
    if not get_settings().resolved_fund_nav_series_backfill_enabled:
        return
    with _BACKFILL_STATE_LOCK:
        if _BACKFILL_IN_FLIGHT:
            return
        remaining = _remaining_backfill_count()
        if remaining <= 0:
            return
        _BACKFILL_IN_FLIGHT = True
    try:
        Thread(
            target=_run_nav_series_backfill_sidecar,
            name="fund-nav-series-backfill",
            daemon=True,
        ).start()
    except Exception:  # noqa: BLE001
        with _BACKFILL_STATE_LOCK:
            _BACKFILL_IN_FLIGHT = False
        logger.exception("failed to start fund nav series backfill thread")


def _remaining_backfill_count() -> int:
    filled = _filled_codes(_load_status())
    return sum(
        1
        for code in _candidate_backfill_codes()
        if code not in filled
    )


def _run_daily_nav_series_sidecar() -> None:
    global _DAILY_IN_FLIGHT
    try:
        try:
            with cross_process_lock(_DAILY_LOCK_NAME, timeout_seconds=1.0):
                summary = run_daily_nav_series_and_risk()
                logger.info(
                    "fund nav series daily sync written=%s purged=%s risk=%s",
                    (summary.get("daily") or {}).get("written"),
                    (summary.get("daily") or {}).get("purged"),
                    summary.get("risk_written"),
                )
        except CrossProcessLockError:
            logger.info("fund nav series daily sync skipped; another worker holds the lock")
    except Exception:  # noqa: BLE001
        logger.exception("scheduled fund nav series daily sync failed")
    finally:
        with _DAILY_STATE_LOCK:
            _DAILY_IN_FLIGHT = False


def _run_nav_series_backfill_sidecar() -> None:
    global _BACKFILL_IN_FLIGHT
    try:
        try:
            with cross_process_lock(_BACKFILL_LOCK_NAME, timeout_seconds=1.0):
                summary = backfill_fund_nav_series()
                if int(summary.get("fetched") or 0) > 0:
                    from app.services.fund_risk_metrics import (
                        refresh_fund_risk_metrics_from_nav_series,
                    )

                    refresh_fund_risk_metrics_from_nav_series()
                logger.info(
                    "fund nav series backfill fetched=%s written=%s remaining=%s",
                    summary.get("fetched"),
                    summary.get("written"),
                    summary.get("remaining"),
                )
        except CrossProcessLockError:
            logger.info("fund nav series backfill skipped; another worker holds the lock")
    except Exception:  # noqa: BLE001
        logger.exception("scheduled fund nav series backfill failed")
    finally:
        with _BACKFILL_STATE_LOCK:
            _BACKFILL_IN_FLIGHT = False


def _reset_nav_series_memory_for_tests() -> None:
    global _DAILY_IN_FLIGHT, _BACKFILL_IN_FLIGHT
    with _DAILY_STATE_LOCK:
        _DAILY_IN_FLIGHT = False
    with _BACKFILL_STATE_LOCK:
        _BACKFILL_IN_FLIGHT = False
