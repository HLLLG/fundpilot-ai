from __future__ import annotations

import math
import time
from typing import Any

from app.config import get_settings
from app.services.akshare_spot_client import fetch_boards_via_akshare
from app.services.eastmoney_spot_client import (
    fetch_eastmoney_boards,
    fetch_eastmoney_quotes_by_secid,
)
from app.services.eastmoney_trends_client import fetch_eastmoney_intraday_trends
from app.services.sector_canonical import _CANONICAL_BY_LABEL
from app.services.sector_intraday_browser_provider import (
    fetch_intraday_via_browser_command,
)
from app.services.sector_quote_browser_provider import fetch_boards_via_browser_command
from app.services.sector_quote_relay_provider import fetch_boards_via_relay

_PROBE_SECIDS: list[tuple[str, str]] = [
    (label, sector.eastmoney_secid)
    for label, sector in _CANONICAL_BY_LABEL.items()
]

# One representative CSI index is enough to verify the shared push2delay minute
# path. Fund/sector-specific mappings can then be checked through the public
# force-refresh endpoint without making this general diagnostic unbounded.
_INTRADAY_PROBE = {
    "label": "中证电网设备",
    "secid": "2.931994",
    "source_code": "931994",
}
_MIN_INTRADAY_POINTS = 30
_MIN_INTRADAY_MAX_ABS_PERCENT = 0.1


def run_sector_quote_diagnostic(*, timeout_seconds: float = 8.0) -> dict[str, Any]:
    """Probe spot and intraday provider paths; read-only, no cache/DB writes."""
    settings = get_settings()
    probes: list[dict[str, Any]] = []

    probes.append(_probe_eastmoney_batch(timeout_seconds))
    probes.extend(_probe_eastmoney_secids(timeout_seconds))
    probes.append(_probe_relay(settings, timeout_seconds))
    probes.append(_probe_browser(settings, timeout_seconds))
    probes.append(_probe_akshare())
    probes.append(_probe_eastmoney_intraday(timeout_seconds))
    probes.append(_probe_browser_intraday(settings, timeout_seconds))

    ok_paths = [probe["name"] for probe in probes if probe.get("ok")]
    spot_ok_paths = [
        probe["name"]
        for probe in probes
        if probe.get("capability", "spot") == "spot" and probe.get("ok")
    ]
    intraday_ok_paths = [
        probe["name"]
        for probe in probes
        if probe.get("capability") == "intraday" and probe.get("ok")
    ]
    capabilities = {
        "spot": bool(spot_ok_paths),
        "intraday": bool(intraday_ok_paths),
    }
    recommendation = _recommend(probes)

    return {
        "ok": all(capabilities.values()),
        "timeout_seconds": timeout_seconds,
        "relay_configured": bool(str(settings.sector_quotes_relay_url or "").strip()),
        "browser_enabled": bool(settings.sector_quotes_browser_enabled),
        "capabilities": capabilities,
        "probes": probes,
        "ok_paths": ok_paths,
        "spot_ok_paths": spot_ok_paths,
        "intraday_ok_paths": intraday_ok_paths,
        "recommendation": recommendation,
    }


def _probe_eastmoney_batch(timeout_seconds: float) -> dict[str, Any]:
    start = time.time()
    try:
        boards = fetch_eastmoney_boards(
            timeout=min(6.0, timeout_seconds * 0.6),
            max_retries=1,
            max_hosts=2,
        )
        entry_count = _board_entry_count(boards)
        sample = _sample_boards(boards)
        return {
            "name": "eastmoney_batch",
            "ok": entry_count >= 8,
            "elapsed_ms": _elapsed_ms(start),
            "entry_count": entry_count,
            "sample": sample,
            "error": None if entry_count >= 8 else "entry_count below 8",
        }
    except Exception as exc:
        return {
            "name": "eastmoney_batch",
            "ok": False,
            "elapsed_ms": _elapsed_ms(start),
            "entry_count": 0,
            "sample": {},
            "error": str(exc),
        }


def _probe_eastmoney_secids(timeout_seconds: float) -> list[dict[str, Any]]:
    per_secid_timeout = min(4.0, timeout_seconds * 0.35)
    start = time.time()
    try:
        quotes = fetch_eastmoney_quotes_by_secid(
            [secid for _, secid in _PROBE_SECIDS],
            timeout=per_secid_timeout,
            max_retries=1,
            max_hosts=3,
        )
        batch_error = None
    except Exception as exc:
        quotes = {}
        batch_error = str(exc)
    elapsed_ms = _elapsed_ms(start)

    results: list[dict[str, Any]] = []
    for label, secid in _PROBE_SECIDS:
        quote = quotes.get(secid) or {}
        change = quote.get("change_percent")
        ok = change is not None
        results.append(
            {
                "name": f"eastmoney_secid:{label}",
                "secid": secid,
                "ok": ok,
                "elapsed_ms": elapsed_ms,
                "entry_count": 1 if ok else 0,
                "sample": {label: change} if ok and change is not None else {},
                "matched_name": quote.get("security_name"),
                "error": None if ok else batch_error or "secid quote miss",
            }
        )
    return results


def _probe_relay(settings: Any, timeout_seconds: float) -> dict[str, Any]:
    relay_url = str(settings.sector_quotes_relay_url or "").strip()
    if not relay_url:
        return {
            "name": "relay",
            "ok": False,
            "elapsed_ms": 0,
            "entry_count": 0,
            "sample": {},
            "error": "FUND_AI_SECTOR_QUOTES_RELAY_URL not configured",
        }

    start = time.time()
    try:
        boards = fetch_boards_via_relay(timeout_seconds=timeout_seconds)
        entry_count = _board_entry_count(boards)
        return {
            "name": "relay",
            "ok": entry_count >= 8,
            "elapsed_ms": _elapsed_ms(start),
            "entry_count": entry_count,
            "sample": _sample_boards(boards),
            "error": None if entry_count >= 8 else "entry_count below 8",
        }
    except Exception as exc:
        return {
            "name": "relay",
            "ok": False,
            "elapsed_ms": _elapsed_ms(start),
            "entry_count": 0,
            "sample": {},
            "error": str(exc),
        }


def _probe_browser(settings: Any, timeout_seconds: float) -> dict[str, Any]:
    if not settings.sector_quotes_browser_enabled:
        return {
            "name": "browser",
            "ok": False,
            "elapsed_ms": 0,
            "entry_count": 0,
            "sample": {},
            "error": "FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED is false",
        }
    if not str(settings.sector_quotes_browser_command or "").strip():
        return {
            "name": "browser",
            "ok": False,
            "elapsed_ms": 0,
            "entry_count": 0,
            "sample": {},
            "error": "FUND_AI_SECTOR_QUOTES_BROWSER_COMMAND not configured",
        }

    start = time.time()
    try:
        boards = fetch_boards_via_browser_command(timeout_seconds=timeout_seconds)
        entry_count = _board_entry_count(boards)
        return {
            "name": "browser",
            "ok": entry_count >= 8,
            "elapsed_ms": _elapsed_ms(start),
            "entry_count": entry_count,
            "sample": _sample_boards(boards),
            "error": None if entry_count >= 8 else "entry_count below 8",
        }
    except Exception as exc:
        return {
            "name": "browser",
            "ok": False,
            "elapsed_ms": _elapsed_ms(start),
            "entry_count": 0,
            "sample": {},
            "error": str(exc),
        }


def _probe_akshare() -> dict[str, Any]:
    start = time.time()
    try:
        boards = fetch_boards_via_akshare(include_index=False)
        entry_count = _board_entry_count(boards)
        return {
            "name": "akshare",
            "ok": entry_count >= 8,
            "elapsed_ms": _elapsed_ms(start),
            "entry_count": entry_count,
            "sample": _sample_boards(boards),
            "error": None if entry_count >= 8 else "entry_count below 8",
        }
    except Exception as exc:
        return {
            "name": "akshare",
            "ok": False,
            "elapsed_ms": _elapsed_ms(start),
            "entry_count": 0,
            "sample": {},
            "error": str(exc),
        }


def _probe_eastmoney_intraday(timeout_seconds: float) -> dict[str, Any]:
    start = time.time()
    try:
        points = fetch_eastmoney_intraday_trends(
            _INTRADAY_PROBE["secid"],
            source_code=_INTRADAY_PROBE["source_code"],
            timeout=min(8.0, max(1.0, timeout_seconds)),
            max_retries=1,
        )
        return _intraday_probe_result(
            name="eastmoney_intraday",
            start=start,
            points=points,
        )
    except Exception as exc:
        return _intraday_probe_result(
            name="eastmoney_intraday",
            start=start,
            points=[],
            error=str(exc),
        )


def _probe_browser_intraday(settings: Any, timeout_seconds: float) -> dict[str, Any]:
    if not settings.sector_quotes_browser_enabled:
        return _intraday_probe_result(
            name="browser_intraday",
            start=None,
            points=[],
            error="FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED is false",
        )

    start = time.time()
    try:
        points = fetch_intraday_via_browser_command(
            _INTRADAY_PROBE["secid"],
            source_code=_INTRADAY_PROBE["source_code"],
            timeout_seconds=timeout_seconds,
        )
        return _intraday_probe_result(
            name="browser_intraday",
            start=start,
            points=points,
        )
    except Exception as exc:
        return _intraday_probe_result(
            name="browser_intraday",
            start=start,
            points=[],
            error=str(exc),
        )


def _intraday_probe_result(
    *,
    name: str,
    start: float | None,
    points: list[dict[str, Any]],
    error: str | None = None,
) -> dict[str, Any]:
    valid_points: list[dict[str, str | float]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        try:
            percent = float(point.get("percent"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(percent):
            continue
        time_value = str(point.get("time") or "").strip()[:5]
        if len(time_value) != 5 or time_value[2] != ":":
            continue
        valid_points.append(
            {
                "time": time_value,
                "percent": round(percent, 4),
            }
        )

    max_abs_percent = max(
        (abs(float(point["percent"])) for point in valid_points),
        default=0.0,
    )
    quality_error = error
    if quality_error is None and len(valid_points) < _MIN_INTRADAY_POINTS:
        quality_error = f"point_count below {_MIN_INTRADAY_POINTS}"
    elif quality_error is None and max_abs_percent < _MIN_INTRADAY_MAX_ABS_PERCENT:
        quality_error = (
            "percent scale looks fractional "
            f"(max_abs below {_MIN_INTRADAY_MAX_ABS_PERCENT})"
        )

    sample: dict[str, Any] = {
        "max_abs_percent": round(max_abs_percent, 4),
    }
    if valid_points:
        sample["first"] = valid_points[0]
        sample["last"] = valid_points[-1]

    return {
        "name": name,
        "capability": "intraday",
        "target": dict(_INTRADAY_PROBE),
        "ok": quality_error is None,
        "elapsed_ms": _elapsed_ms(start) if start is not None else 0,
        "entry_count": len(valid_points),
        "sample": sample,
        "error": quality_error,
    }


def _recommend(probes: list[dict[str, Any]]) -> str:
    by_name = {probe["name"]: probe for probe in probes}
    intraday = by_name.get("eastmoney_intraday", {})
    browser_intraday = by_name.get("browser_intraday", {})
    if not intraday.get("ok") and not browser_intraday.get("ok"):
        browser_disabled = "is false" in str(browser_intraday.get("error", ""))
        if browser_disabled:
            return (
                "intraday_failed: direct push2delay unavailable or invalid; "
                "enable FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED and rerun"
            )
        return "intraday_failed: direct and browser minute paths unavailable or invalid"

    if browser_intraday.get("ok") and not intraday.get("ok"):
        return (
            "browser_intraday_ok: keep browser fallback enabled; "
            "direct push2delay is unavailable"
        )

    batch = by_name.get("eastmoney_batch", {})
    if batch.get("ok"):
        return "eastmoney_spot_and_intraday_ok: relay/browser fallback optional"

    browser = by_name.get("browser", {})
    if browser.get("ok"):
        return "enable_browser: set FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED=true"

    relay = by_name.get("relay", {})
    if relay.get("ok"):
        return "relay_ok: configure FUND_AI_SECTOR_QUOTES_RELAY_URL on this PC"

    relay_url_missing = "not configured" in str(relay.get("error", ""))
    if relay_url_missing:
        akshare = by_name.get("akshare", {})
        if akshare.get("ok"):
            return "deploy_relay_or_enable_browser: eastmoney blocked; akshare works for accurate refresh"
        return "deploy_relay: eastmoney blocked on PC; deploy apps/sector-relay on VPS/NAS"

    return "all_paths_failed: check network/firewall or deploy sector-relay"


def _board_entry_count(boards: dict[str, dict[str, float]]) -> int:
    return sum(len(board or {}) for board in boards.values())


def _sample_boards(boards: dict[str, dict[str, float]], limit: int = 3) -> dict[str, dict[str, float]]:
    sample: dict[str, dict[str, float]] = {}
    for key in ("concept", "industry", "index"):
        board = boards.get(key) or {}
        if not board:
            continue
        items = list(board.items())[:limit]
        sample[key] = dict(items)
    return sample


def _elapsed_ms(start: float) -> int:
    return int(round((time.time() - start) * 1000))
