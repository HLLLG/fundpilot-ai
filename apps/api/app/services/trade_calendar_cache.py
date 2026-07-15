from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Mapping

from app.config import get_settings
from app.services.decision_quality_provider_receipts import (
    DecisionQualityProviderRead,
    ProviderReceiptValidationError,
    build_provider_origin_receipt,
    build_provider_read,
    validate_provider_read,
)


_CACHE_FILENAME = "trade_dates.json"
_MAX_AGE_DAYS = 7
_SUBPROCESS_TIMEOUT = 45

_QUALITY_CACHE_SCHEMA_VERSION = "trade_calendar_cache.v2"
_QUALITY_PROVIDER_ID = "akshare.tool_trade_date_hist_sina"
_QUALITY_OPERATION = "tool_trade_date_hist_sina"
_QUALITY_ADAPTER_CONTRACT_VERSION_V1 = (
    "decision_quality_trade_calendar_adapter.v1"
)
_QUALITY_ADAPTER_CONTRACT_VERSION = _QUALITY_ADAPTER_CONTRACT_VERSION_V1
_QUALITY_CACHE_POLICY_V1 = "disk_7d_plus_process_expiry.v1"
_QUALITY_CACHE_POLICY = _QUALITY_CACHE_POLICY_V1
_QUALITY_UPSTREAM_RAW_REASON = (
    "akshare exposes a dataframe; this receipt captures adapter stdout, not "
    "the upstream HTTP response"
)
_TRADE_CALENDAR_SCRIPT_V1 = (
    "import akshare as ak, json; "
    "frame=ak.tool_trade_date_hist_sina(); "
    "column='trade_date' if 'trade_date' in frame.columns else frame.columns[0]; "
    "print(json.dumps([str(value)[:10] for value in frame[column].tolist()]))"
)
_TRADE_CALENDAR_SCRIPT = _TRADE_CALENDAR_SCRIPT_V1

_QUALITY_CACHE_LOCK = threading.RLock()
_QUALITY_PROCESS_ORIGIN: tuple[dict[str, Any], object] | None = None


def _cache_path() -> Path:
    return get_settings().db_path.parent / _CACHE_FILENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _akshare_version() -> str:
    try:
        return package_version("akshare")
    except PackageNotFoundError:
        return "unavailable"


def _utf8_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _load_cached_dates() -> frozenset[str] | None:
    """Load both legacy cache rows and the quality cache's normalized view.

    Legacy cache rows remain usable by the historical public API, but the
    quality-only API below deliberately refuses to turn them into a receipt.
    """

    path = _cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") == _QUALITY_CACHE_SCHEMA_VERSION:
            read = _quality_read_from_cache_payload(payload, served_at=_utc_now())
            if read is None:
                return None
            normalized = read.normalized_payload
            if not isinstance(normalized, Mapping):
                return None
            return frozenset(str(value) for value in normalized.get("dates") or [])
        fetched = date.fromisoformat(str(payload["fetched_at"])[:10])
        if date.today() - fetched > timedelta(days=_MAX_AGE_DAYS):
            return None
        return frozenset(str(value)[:10] for value in payload["dates"])
    except Exception:
        return None


def _save_cached_dates(dates: frozenset[str]) -> None:
    """Persist the legacy cache shape without granting it formal provenance."""

    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetched_at": date.today().isoformat(),
                "dates": sorted(dates),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _fetch_dates_subprocess() -> frozenset[str] | None:
    """Fetch Sina trade dates for the compatibility API."""

    try:
        completed = subprocess.run(
            [sys.executable, "-c", _TRADE_CALENDAR_SCRIPT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
            env=_utf8_subprocess_env(),
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        dates = json.loads(completed.stdout.strip())
        if not dates:
            return None
        return frozenset(str(value)[:10] for value in dates)
    except Exception:
        return None


def _parse_json_stdout(stdout: bytes) -> object | None:
    try:
        text = stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text:
        return None
    for candidate in (text, *reversed(text.splitlines())):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _normalize_trade_calendar_payload(parsed: object) -> dict[str, list[str]] | None:
    if not isinstance(parsed, list) or not parsed:
        return None
    normalized: set[str] = set()
    for value in parsed:
        raw = str(value or "")[:10]
        try:
            normalized.add(date.fromisoformat(raw).isoformat())
        except ValueError:
            continue
    if not normalized:
        return None
    return {"dates": sorted(normalized)}


def _cache_key_material() -> dict[str, object]:
    return {
        "provider_id": _QUALITY_PROVIDER_ID,
        "operation": _QUALITY_OPERATION,
        "parameters": {},
        "adapter_contract_version": _QUALITY_ADAPTER_CONTRACT_VERSION,
    }


def _cache_key_material_v1() -> dict[str, object]:
    return {
        "provider_id": _QUALITY_PROVIDER_ID,
        "operation": _QUALITY_OPERATION,
        "parameters": {},
        "adapter_contract_version": _QUALITY_ADAPTER_CONTRACT_VERSION_V1,
    }


def trade_calendar_quality_adapter_policy_material(
    *,
    contract_version: str | None = None,
) -> dict[str, object]:
    """Expose the exact production adapter inputs used by formal D4 evidence.

    The candidate evidence verifier consumes this function instead of copying
    the script or contract constants.  Consequently a production adapter
    change fails closed until the formal policy is deliberately reviewed.
    """

    requested = contract_version or _QUALITY_ADAPTER_CONTRACT_VERSION
    if requested != _QUALITY_ADAPTER_CONTRACT_VERSION_V1:
        raise ValueError("unknown trade-calendar quality adapter contract")
    return {
        "provider_id": _QUALITY_PROVIDER_ID,
        "operation": _QUALITY_OPERATION,
        "request_parameters": {},
        "adapter_contract_version": _QUALITY_ADAPTER_CONTRACT_VERSION_V1,
        "adapter_script": _TRADE_CALENDAR_SCRIPT_V1,
        "adapter_policy_script": _TRADE_CALENDAR_SCRIPT_V1,
        "cache_policy": _QUALITY_CACHE_POLICY_V1,
        "cache_key_material": _cache_key_material_v1(),
        "cache_key_policy_material": _cache_key_material_v1(),
        "library_name": "akshare",
    }


def _build_quality_origin_read(
    *,
    started_at: str,
    completed_at: str,
    stdout: bytes,
    parsed_payload: object,
    normalized_payload: object,
    status: str,
) -> DecisionQualityProviderRead:
    receipt = build_provider_origin_receipt(
        provider_id=_QUALITY_PROVIDER_ID,
        operation=_QUALITY_OPERATION,
        request_parameters={},
        request_started_at=started_at,
        response_completed_at=completed_at,
        response_status=status,
        adapter_contract_version=_QUALITY_ADAPTER_CONTRACT_VERSION,
        adapter_script=_TRADE_CALENDAR_SCRIPT,
        library_name="akshare",
        library_version=_akshare_version(),
        python_version=platform.python_version(),
        cache_policy=_QUALITY_CACHE_POLICY,
        cache_key_material=_cache_key_material(),
        stdout_bytes=stdout,
        parsed_payload=parsed_payload,
        normalized_payload=normalized_payload,
        upstream_raw_unavailable_reason=_QUALITY_UPSTREAM_RAW_REASON,
    )
    return build_provider_read(
        origin_receipt=receipt,
        normalized_payload=normalized_payload,
        cache_status="miss",
        cache_layer="live",
        served_at=completed_at,
    )


def _timeout_stdout(exc: subprocess.TimeoutExpired) -> bytes:
    value = exc.stdout or b""
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def _fetch_trade_calendar_quality_origin() -> DecisionQualityProviderRead:
    started_at = _utc_now()
    stdout = b""
    parsed: object = None
    normalized: object = None
    status = "exception"
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _TRADE_CALENDAR_SCRIPT],
            capture_output=True,
            text=False,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
            env=_utf8_subprocess_env(),
        )
        stdout = bytes(completed.stdout or b"")
        parsed = _parse_json_stdout(stdout)
        normalized = _normalize_trade_calendar_payload(parsed)
        if completed.returncode != 0:
            status = "subprocess_error"
            normalized = None
        elif not stdout.strip():
            status = "empty"
            normalized = None
        elif parsed is None:
            status = "invalid_json"
            normalized = None
        elif normalized is None:
            status = "invalid_payload"
        else:
            status = "success"
    except subprocess.TimeoutExpired as exc:
        stdout = _timeout_stdout(exc)
        parsed = _parse_json_stdout(stdout)
        normalized = None
        status = "timeout"
    except Exception:
        stdout = b""
        parsed = None
        normalized = None
        status = "exception"
    completed_at = _utc_now()
    return _build_quality_origin_read(
        started_at=started_at,
        completed_at=completed_at,
        stdout=stdout,
        parsed_payload=parsed,
        normalized_payload=normalized,
        status=status,
    )


def _origin_completed_at(origin_receipt: Mapping[str, Any]) -> datetime:
    response = origin_receipt.get("response")
    if not isinstance(response, Mapping):
        raise ProviderReceiptValidationError("calendar receipt response is missing")
    parsed = datetime.fromisoformat(str(response.get("completed_at") or ""))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderReceiptValidationError("calendar receipt completion is naive")
    return parsed.astimezone(timezone.utc)


def _origin_is_fresh(origin_receipt: Mapping[str, Any], *, served_at: str) -> bool:
    try:
        served = datetime.fromisoformat(served_at)
        completed = _origin_completed_at(origin_receipt)
    except (TypeError, ValueError, ProviderReceiptValidationError):
        return False
    if served.tzinfo is None or served.utcoffset() is None or served < completed:
        return False
    return served - completed <= timedelta(days=_MAX_AGE_DAYS)


def _quality_cache_payload(read: DecisionQualityProviderRead) -> dict[str, Any]:
    validate_provider_read(read)
    return {
        "schema_version": _QUALITY_CACHE_SCHEMA_VERSION,
        "origin_receipt": deepcopy(read.origin_receipt),
        "normalized_payload": deepcopy(read.normalized_payload),
    }


def _save_quality_cached_read(read: DecisionQualityProviderRead) -> None:
    if not read.ok:
        return
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _quality_cache_payload(read),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _quality_read_from_cache_payload(
    payload: object,
    *,
    served_at: str,
) -> DecisionQualityProviderRead | None:
    if not isinstance(payload, Mapping) or payload.get(
        "schema_version"
    ) != _QUALITY_CACHE_SCHEMA_VERSION:
        return None
    origin = payload.get("origin_receipt")
    normalized = payload.get("normalized_payload")
    if not isinstance(origin, Mapping) or not _origin_is_fresh(
        origin,
        served_at=served_at,
    ):
        return None
    try:
        read = build_provider_read(
            origin_receipt=origin,
            normalized_payload=normalized,
            cache_status="hit",
            cache_layer="disk",
            served_at=served_at,
        )
    except (ProviderReceiptValidationError, TypeError, ValueError):
        return None
    return read if read.ok else None


def _load_quality_cached_read(*, served_at: str) -> DecisionQualityProviderRead | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _quality_read_from_cache_payload(payload, served_at=served_at)


def _quality_process_hit(*, served_at: str) -> DecisionQualityProviderRead | None:
    global _QUALITY_PROCESS_ORIGIN
    if _QUALITY_PROCESS_ORIGIN is None:
        return None
    origin, normalized = _QUALITY_PROCESS_ORIGIN
    if not _origin_is_fresh(origin, served_at=served_at):
        _QUALITY_PROCESS_ORIGIN = None
        return None
    try:
        return build_provider_read(
            origin_receipt=origin,
            normalized_payload=normalized,
            cache_status="hit",
            cache_layer="process",
            served_at=served_at,
        )
    except ProviderReceiptValidationError:
        _QUALITY_PROCESS_ORIGIN = None
        return None


def _remember_quality_origin(read: DecisionQualityProviderRead) -> None:
    global _QUALITY_PROCESS_ORIGIN
    if read.ok:
        _QUALITY_PROCESS_ORIGIN = (
            deepcopy(read.origin_receipt),
            deepcopy(read.normalized_payload),
        )


def clear_trade_calendar_quality_cache() -> None:
    """Clear only the in-process quality cache; useful after configuration changes."""

    global _QUALITY_PROCESS_ORIGIN
    with _QUALITY_CACHE_LOCK:
        _QUALITY_PROCESS_ORIGIN = None


def get_trade_calendar_quality_read() -> DecisionQualityProviderRead:
    """Return a typed, provenance-preserving trade-calendar provider read."""

    served_at = _utc_now()
    with _QUALITY_CACHE_LOCK:
        process_hit = _quality_process_hit(served_at=served_at)
        if process_hit is not None:
            return process_hit
        disk_hit = _load_quality_cached_read(served_at=served_at)
        if disk_hit is not None:
            _remember_quality_origin(disk_hit)
            return disk_hit

    captured = _fetch_trade_calendar_quality_origin()
    if captured.ok:
        with _QUALITY_CACHE_LOCK:
            _save_quality_cached_read(captured)
            _remember_quality_origin(captured)
    return captured


def get_trade_date_set_quality_read() -> DecisionQualityProviderRead:
    """Compatibility alias using the existing public API's noun order."""

    return get_trade_calendar_quality_read()


def get_trade_date_set() -> frozenset[str] | None:
    """Return dates for legacy callers without manufacturing provider receipts."""

    cached = _load_cached_dates()
    if cached is not None:
        return cached

    fetched = _fetch_dates_subprocess()
    if fetched:
        _save_cached_dates(fetched)
        return fetched
    return None


__all__ = [
    "clear_trade_calendar_quality_cache",
    "get_trade_calendar_quality_read",
    "get_trade_date_set",
    "get_trade_date_set_quality_read",
    "trade_calendar_quality_adapter_policy_material",
]
