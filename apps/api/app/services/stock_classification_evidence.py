"""Current, first-observed stock classification evidence.

The Eastmoney stock-detail endpoint exposes a useful broad industry label, but
the AkShare wrapper requests hundreds of unrelated fields and is fragile under
provider throttling.  This module requests only the fields we need, records the
observation clock, and caches the evidence so a later decision can prove that
the classification was already known.

These helpers deliberately do not support historical backfilling.  A current
classification must never be attached retroactively to an older decision.
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

import httpx

from app.services.eastmoney_spot_client import (
    _CLIST_HOSTS,
    _COMMON_PARAMS,
    _EASTMONEY_HEADERS,
    _STOCK_HOSTS,
)
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)

logger = logging.getLogger(__name__)

_INDUSTRY_CACHE_PREFIX = "stock-classification:industry:v1:"
_BOARD_CACHE_PREFIX = "stock-classification:board-members:v1:"
_INDUSTRY_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
_BOARD_CACHE_TTL_SECONDS = 24 * 60 * 60
_MAX_STALE_AGE = timedelta(days=180)
_MAX_WORKERS = 6
_REQUEST_TIMEOUT_SECONDS = 4.0
_MAX_HOSTS = 4


def fetch_current_stock_industry_evidence(
    targets: Iterable[Mapping[str, Any]],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return broad-industry evidence for A-share stock codes.

    Every returned row includes ``available_at``, ``source`` and ``ref_id``.
    The observation is PIT-qualified only from that timestamp onward.
    """

    normalized: dict[str, str] = {}
    for target in targets:
        code = str(target.get("security_code") or target.get("stock_code") or "").strip()
        if not _is_supported_stock_code(code):
            continue
        normalized.setdefault(
            code,
            str(target.get("security_name") or target.get("stock_name") or "").strip(),
        )
    if not normalized:
        return {}

    result: dict[str, dict[str, Any]] = {}
    missing: dict[str, str] = {}
    for code, name in normalized.items():
        cached = None if force_refresh else _read_cached_evidence(
            _industry_cache_key(code),
            ttl_seconds=_INDUSTRY_CACHE_TTL_SECONDS,
        )
        if _valid_industry_evidence(cached) and _not_older_than(
            cached.get("available_at"),
            timedelta(seconds=_INDUSTRY_CACHE_TTL_SECONDS),
        ):
            result[code] = dict(cached)
        else:
            missing[code] = name

    live: dict[str, dict[str, Any]] = {}
    if missing:
        with httpx.Client(
            headers=_EASTMONEY_HEADERS,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            trust_env=False,
            follow_redirects=True,
            http2=False,
        ) as client:
            worker_count = min(_MAX_WORKERS, len(missing))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(_fetch_one_stock_industry, client, code, name): code
                    for code, name in missing.items()
                }
                for future in as_completed(futures):
                    code = futures[future]
                    try:
                        evidence = future.result()
                    except Exception as exc:  # noqa: BLE001 - best-effort enrichment
                        logger.debug("stock industry fetch failed for %s: %s", code, exc)
                        continue
                    if evidence is not None:
                        live[code] = evidence

    for code, evidence in live.items():
        result[code] = evidence
        _save_cached_evidence(_industry_cache_key(code), evidence)

    # A transient provider failure may use a previously observed classification.
    # It is still auditable because the original available_at/ref_id are retained.
    for code in missing:
        if code in result:
            continue
        stale = _read_cached_evidence(
            _industry_cache_key(code),
            ttl_seconds=None,
        )
        if _valid_industry_evidence(stale) and _not_older_than(
            stale.get("available_at"),
            _MAX_STALE_AGE,
        ):
            result[code] = dict(stale)
    return result


def fetch_current_board_constituent_evidence(
    board_codes: Iterable[str],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Return first-observed constituent sets for Eastmoney ``BK`` boards."""

    normalized_codes = list(
        dict.fromkeys(
            code
            for raw in board_codes
            if (code := _normalize_board_code(raw)) is not None
        )
    )
    if not normalized_codes:
        return {}

    result: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for board_code in normalized_codes:
        cached = None if force_refresh else _read_cached_evidence(
            _board_cache_key(board_code),
            ttl_seconds=_BOARD_CACHE_TTL_SECONDS,
        )
        if _valid_board_evidence(cached) and _not_older_than(
            cached.get("available_at"),
            timedelta(seconds=_BOARD_CACHE_TTL_SECONDS),
        ):
            result[board_code] = dict(cached)
        else:
            missing.append(board_code)

    live: dict[str, dict[str, Any]] = {}
    if missing:
        with httpx.Client(
            headers=_EASTMONEY_HEADERS,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            trust_env=False,
            follow_redirects=True,
            http2=False,
        ) as client:
            worker_count = min(_MAX_WORKERS, len(missing))
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(_fetch_one_board_constituents, client, board_code): board_code
                    for board_code in missing
                }
                for future in as_completed(futures):
                    board_code = futures[future]
                    try:
                        evidence = future.result()
                    except Exception as exc:  # noqa: BLE001 - best-effort refinement
                        logger.debug("board constituent fetch failed for %s: %s", board_code, exc)
                        continue
                    if evidence is not None:
                        live[board_code] = evidence

    for board_code, evidence in live.items():
        result[board_code] = evidence
        _save_cached_evidence(_board_cache_key(board_code), evidence)

    for board_code in missing:
        if board_code in result:
            continue
        stale = _read_cached_evidence(
            _board_cache_key(board_code),
            ttl_seconds=None,
        )
        if _valid_board_evidence(stale) and _not_older_than(
            stale.get("available_at"),
            _MAX_STALE_AGE,
        ):
            result[board_code] = dict(stale)
    return result


def _fetch_one_stock_industry(
    client: httpx.Client,
    code: str,
    fallback_name: str,
) -> dict[str, Any] | None:
    params = {
        **_COMMON_PARAMS,
        "secid": _stock_secid(code),
        "fields": "f57,f58,f127,f198",
    }
    last_error: Exception | None = None
    for host in _STOCK_HOSTS[:_MAX_HOSTS]:
        try:
            response = client.get(f"https://{host}/api/qt/stock/get", params=params)
            response.raise_for_status()
            data = response.json().get("data") or {}
            industry = str(data.get("f127") or "").strip()
            if not industry:
                continue
            observed_at = datetime.now(timezone.utc).isoformat()
            stock_name = str(data.get("f58") or fallback_name or "").strip()
            raw = {
                "stock_code": code,
                "stock_name": stock_name,
                "industry": industry,
                "provider_board_code": str(data.get("f198") or "").strip() or None,
                "available_at": observed_at,
                "source": "eastmoney_push2_stock_get_f127",
            }
            return {
                "value": industry,
                **raw,
                "ref_id": _evidence_ref(raw),
                "pit_qualified": True,
            }
        except Exception as exc:  # noqa: BLE001 - try the next provider host
            last_error = exc
    if last_error is not None:
        logger.debug("all Eastmoney industry hosts failed for %s: %s", code, last_error)
    return None


def _fetch_one_board_constituents(
    client: httpx.Client,
    board_code: str,
) -> dict[str, Any] | None:
    params = {
        **_COMMON_PARAMS,
        "pn": "1",
        "pz": "200",
        "fid": "f12",
        "fs": f"b:{board_code} f:!50",
        "fields": "f12,f13,f14",
    }
    last_error: Exception | None = None
    for host in _CLIST_HOSTS[:_MAX_HOSTS]:
        try:
            response = client.get(f"https://{host}/api/qt/clist/get", params=params)
            response.raise_for_status()
            data = response.json().get("data") or {}
            rows = data.get("diff") or []
            members = sorted(
                (
                    {
                        "stock_code": str(row.get("f12") or "").strip(),
                        "stock_name": str(row.get("f14") or "").strip(),
                    }
                    for row in rows
                    if _is_supported_stock_code(str(row.get("f12") or "").strip())
                ),
                key=lambda row: row["stock_code"],
            )
            if not members:
                continue
            observed_at = datetime.now(timezone.utc).isoformat()
            raw = {
                "board_code": board_code,
                "members": members,
                "available_at": observed_at,
                "source": "eastmoney_push2_clist_board_members",
            }
            return {
                **raw,
                "codes": [member["stock_code"] for member in members],
                "ref_id": _evidence_ref(raw),
                "pit_qualified": True,
            }
        except Exception as exc:  # noqa: BLE001 - try the next provider host
            last_error = exc
    if last_error is not None:
        logger.debug("all Eastmoney board hosts failed for %s: %s", board_code, last_error)
    return None


def _read_cached_evidence(
    cache_key: str,
    *,
    ttl_seconds: float | None,
) -> dict[str, Any] | None:
    try:
        if ttl_seconds is None:
            payload = get_spot_snapshot_any_age(cache_key)
        else:
            payload = get_spot_snapshot(cache_key, ttl_seconds=ttl_seconds)
    except Exception as exc:  # noqa: BLE001 - cache is an optimization
        logger.debug("classification cache read failed for %s: %s", cache_key, exc)
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def _save_cached_evidence(cache_key: str, evidence: dict[str, Any]) -> None:
    try:
        save_spot_snapshot(cache_key, evidence)
    except Exception as exc:  # noqa: BLE001 - live evidence remains usable
        logger.debug("classification cache write failed for %s: %s", cache_key, exc)


def _valid_industry_evidence(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    return bool(
        str(value.get("value") or value.get("industry") or "").strip()
        and _aware_datetime(value.get("available_at")) is not None
        and str(value.get("source") or "").strip()
        and str(value.get("ref_id") or "").strip()
        and value.get("pit_qualified") is True
    )


def _valid_board_evidence(value: object) -> bool:
    if not isinstance(value, Mapping) or not isinstance(value.get("codes"), list):
        return False
    return bool(
        value.get("codes")
        and _aware_datetime(value.get("available_at")) is not None
        and str(value.get("source") or "").strip()
        and str(value.get("ref_id") or "").strip()
        and value.get("pit_qualified") is True
    )


def _not_older_than(value: object, max_age: timedelta) -> bool:
    observed_at = _aware_datetime(value)
    if observed_at is None:
        return False
    age = datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc)
    return timedelta(0) <= age <= max_age


def _aware_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _normalize_board_code(value: object) -> str | None:
    code = str(value or "").strip().upper()
    if code.isdigit():
        code = f"BK{code}"
    return code if len(code) == 6 and code.startswith("BK") and code[2:].isdigit() else None


def _is_supported_stock_code(code: str) -> bool:
    return len(code) == 6 and code.isdigit()


def _stock_secid(code: str) -> str:
    market = "1" if code.startswith(("5", "6", "9")) else "0"
    return f"{market}.{code}"


def _industry_cache_key(code: str) -> str:
    return f"{_INDUSTRY_CACHE_PREFIX}{code}"


def _board_cache_key(board_code: str) -> str:
    return f"{_BOARD_CACHE_PREFIX}{board_code}"


def _evidence_ref(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "fetch_current_board_constituent_evidence",
    "fetch_current_stock_industry_evidence",
]
