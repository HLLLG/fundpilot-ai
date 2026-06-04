from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Connection": "close",
}


def fetch_boards_via_relay(*, timeout_seconds: float | None = None) -> dict[str, dict[str, float]]:
    settings = get_settings()
    relay_url = str(settings.sector_quotes_relay_url or "").strip()
    if not relay_url:
        return _empty_boards()

    timeout = _relay_timeout(settings.sector_quotes_relay_timeout_seconds, timeout_seconds)
    headers = dict(_HEADERS)
    token = str(settings.sector_quotes_relay_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-Relay-Token"] = token
    try:
        with httpx.Client(
            headers=headers,
            timeout=timeout,
            trust_env=False,
            follow_redirects=True,
            http2=False,
        ) as client:
            response = client.get(relay_url)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.info("sector quote relay failed: %s", exc)
        return _empty_boards()

    return _coerce_boards(payload)


def _relay_timeout(default_timeout: float, timeout_seconds: float | None) -> float:
    if timeout_seconds is None:
        return default_timeout
    return round(max(0.5, min(default_timeout, timeout_seconds * 0.45)), 3)


def _coerce_boards(payload: Any) -> dict[str, dict[str, float]]:
    if isinstance(payload, dict):
        for key in ("boards", "data", "result"):
            nested = payload.get(key)
            if isinstance(nested, dict) and any(name in nested for name in ("index", "concept", "industry")):
                return _coerce_boards(nested)

        return {
            "index": _coerce_board_map(payload.get("index")),
            "concept": _coerce_board_map(payload.get("concept")),
            "industry": _coerce_board_map(payload.get("industry")),
        }

    return _empty_boards()


def _coerce_board_map(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}

    result: dict[str, float] = {}
    for name, value in raw.items():
        cleaned = str(name).strip()
        if not cleaned:
            continue
        try:
            result[cleaned] = round(float(value), 4)
        except (TypeError, ValueError):
            continue
    return result


def _empty_boards() -> dict[str, dict[str, float]]:
    return {"index": {}, "concept": {}, "industry": {}}
