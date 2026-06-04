from __future__ import annotations

import time
from typing import Any

from app.config import get_settings
from app.services.akshare_spot_client import fetch_boards_via_akshare
from app.services.eastmoney_spot_client import fetch_eastmoney_boards, fetch_eastmoney_quote_by_secid
from app.services.sector_canonical import _CANONICAL_BY_LABEL
from app.services.sector_quote_browser_provider import fetch_boards_via_browser_command
from app.services.sector_quote_relay_provider import fetch_boards_via_relay

_PROBE_SECIDS: list[tuple[str, str]] = [
    (label, sector.eastmoney_secid)
    for label, sector in _CANONICAL_BY_LABEL.items()
]


def run_sector_quote_diagnostic(*, timeout_seconds: float = 8.0) -> dict[str, Any]:
    """Probe each sector quote provider path; read-only, no DB writes."""
    settings = get_settings()
    probes: list[dict[str, Any]] = []

    probes.append(_probe_eastmoney_batch(timeout_seconds))
    probes.extend(_probe_eastmoney_secids(timeout_seconds))
    probes.append(_probe_relay(settings, timeout_seconds))
    probes.append(_probe_browser(settings, timeout_seconds))
    probes.append(_probe_akshare())

    ok_paths = [probe["name"] for probe in probes if probe.get("ok")]
    recommendation = _recommend(probes)

    return {
        "ok": bool(ok_paths),
        "timeout_seconds": timeout_seconds,
        "relay_configured": bool(str(settings.sector_quotes_relay_url or "").strip()),
        "browser_enabled": bool(settings.sector_quotes_browser_enabled),
        "probes": probes,
        "ok_paths": ok_paths,
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
    results: list[dict[str, Any]] = []
    for label, secid in _PROBE_SECIDS:
        start = time.time()
        try:
            name, change = fetch_eastmoney_quote_by_secid(
                secid,
                timeout=per_secid_timeout,
                max_retries=1,
            )
            ok = change is not None
            results.append({
                "name": f"eastmoney_secid:{label}",
                "secid": secid,
                "ok": ok,
                "elapsed_ms": _elapsed_ms(start),
                "entry_count": 1 if ok else 0,
                "sample": {label: change} if ok and change is not None else {},
                "matched_name": name,
                "error": None if ok else "secid quote miss",
            })
        except Exception as exc:
            results.append({
                "name": f"eastmoney_secid:{label}",
                "secid": secid,
                "ok": False,
                "elapsed_ms": _elapsed_ms(start),
                "entry_count": 0,
                "sample": {},
                "error": str(exc),
            })
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


def _recommend(probes: list[dict[str, Any]]) -> str:
    by_name = {probe["name"]: probe for probe in probes}
    batch = by_name.get("eastmoney_batch", {})
    if batch.get("ok"):
        return "eastmoney_batch_ok: tune fast refresh timeouts; relay optional"

    browser = by_name.get("browser", {})
    if browser.get("ok"):
        return "enable_browser: set FUND_AI_SECTOR_QUOTES_BROWSER_ENABLED=true"

    relay = by_name.get("relay", {})
    if relay.get("ok"):
        return "relay_ok: configure FUND_AI_SECTOR_QUOTES_RELAY_URL on this PC"

    if not relay.get("error", "").startswith("FUND_AI"):
        if str(relay.get("error", "")).startswith("FUND_AI"):
            pass
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
