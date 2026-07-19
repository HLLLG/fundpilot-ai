from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from threading import RLock
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_SUGGEST_URL = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
_CACHE_MAX_ENTRIES = 128
_CACHE_TTL_SECONDS = 15 * 60
_cache: OrderedDict[str, tuple[float, list[dict[str, object]]]] = OrderedDict()
_cache_lock = RLock()


def _timeout_seconds() -> float:
    raw = os.getenv("FUND_AI_FUND_SEARCH_SUGGEST_TIMEOUT_SECONDS", "1.2").strip()
    try:
        return min(max(float(raw), 0.2), 3.0)
    except ValueError:
        return 1.2


def _read_cached(query: str) -> list[dict[str, object]] | None:
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(query)
        if cached is None:
            return None
        cached_at, rows = cached
        if now - cached_at > _CACHE_TTL_SECONDS:
            _cache.pop(query, None)
            return None
        _cache.move_to_end(query)
        return [dict(row) for row in rows]


def _save_cached(query: str, rows: list[dict[str, object]]) -> None:
    with _cache_lock:
        _cache[query] = (time.monotonic(), [dict(row) for row in rows])
        _cache.move_to_end(query)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def fetch_ranked_fund_suggestions(query: str) -> list[dict[str, object]]:
    """Return the public provider's current ordered fund suggestions.

    This order is only a lightweight attention/relevance signal for search UI.
    It never participates in fund-sector resolution, NAV calculations, or any
    investment decision evidence.
    """

    normalized = query.strip().casefold()
    if len(normalized) < 2:
        return []
    cached = _read_cached(normalized)
    if cached is not None:
        return cached

    params = urlencode({"m": "1", "key": query.strip()})
    request = Request(
        f"{_SUGGEST_URL}?{params}",
        headers={
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://fund.eastmoney.com/",
            "User-Agent": "FundPilot/1.0",
        },
    )
    rows: list[dict[str, object]] = []
    try:
        with urlopen(request, timeout=_timeout_seconds()) as response:  # noqa: S310 - fixed HTTPS host
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        for raw in payload.get("Datas") or []:
            if not isinstance(raw, dict) or raw.get("CATEGORYDESC") != "基金":
                continue
            code = str(raw.get("CODE") or "").strip().zfill(6)
            name = str(raw.get("NAME") or "").strip()
            if len(code) != 6 or not code.isdigit() or not name:
                continue
            base = raw.get("FundBaseInfo")
            fund_type = str(base.get("FTYPE") or "").strip() if isinstance(base, dict) else ""
            rows.append(
                {
                    "fund_code": code,
                    "fund_name": name,
                    "fund_type": fund_type or None,
                    "provider_rank": len(rows) + 1,
                }
            )
            if len(rows) >= 10:
                break
    except (OSError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
        rows = []

    _save_cached(normalized, rows)
    return rows
