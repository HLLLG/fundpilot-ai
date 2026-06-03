from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from app.models import Holding

logger = logging.getLogger(__name__)

_FUND_ESTIMATE_URL = "https://fundgz.1234567.com.cn/js/{fund_code}.js"
_JSONP_RE = re.compile(r"jsonpgz\((?P<payload>.*)\);?\s*$", re.S)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fund.eastmoney.com/",
    "Accept": "*/*",
    "Connection": "close",
}


def fetch_fund_estimate_quotes(
    holdings: list[Holding],
    *,
    timeout_seconds: float | None = None,
) -> dict[str, dict[str, Any]]:
    codes = sorted(
        {
            holding.fund_code.strip()
            for holding in holdings
            if (holding.fund_code or "").strip()
        }
    )
    if not codes:
        return {}

    timeout = 6.0 if timeout_seconds is None else max(0.8, min(1.2, timeout_seconds * 0.25))
    results: dict[str, dict[str, Any]] = {}
    max_workers = min(6, len(codes))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_fund_estimate_quote_for_code, code, timeout): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                payload = future.result()
            except Exception as exc:
                logger.info("fund estimate worker failed for %s: %s", code, exc)
                continue
            if payload is not None:
                results[code] = payload
    return results


def _fetch_fund_estimate_quote_for_code(
    fund_code: str,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    with _build_session() as session:
        return _fetch_fund_estimate_quote(session, fund_code, timeout_seconds=timeout_seconds)



def fetch_fund_estimate_quote(
    fund_code: str,
    *,
    timeout_seconds: float = 6.0,
) -> dict[str, Any] | None:
    with _build_session() as session:
        return _fetch_fund_estimate_quote(session, fund_code, timeout_seconds=timeout_seconds)



def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)
    session.trust_env = False
    return session



def _fetch_fund_estimate_quote(
    session: requests.Session,
    fund_code: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    cleaned = str(fund_code).strip()
    if len(cleaned) != 6 or not cleaned.isdigit():
        return None

    try:
        response = session.get(
            _FUND_ESTIMATE_URL.format(fund_code=cleaned),
            timeout=timeout_seconds,
            allow_redirects=True,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.info("fund estimate HTTP failed for %s: %s", cleaned, exc)
        return None

    match = _JSONP_RE.search(response.text.strip())
    if match is None:
        return None

    try:
        payload = json.loads(match.group("payload"))
    except json.JSONDecodeError:
        return None

    try:
        change_percent = round(float(payload["gszzl"]), 4)
    except (KeyError, TypeError, ValueError):
        return None

    return {
        "fund_code": cleaned,
        "fund_name": payload.get("name"),
        "change_percent": change_percent,
        "estimated_nav": _to_float(payload.get("gsz")),
        "previous_nav": _to_float(payload.get("dwjz")),
        "nav_date": payload.get("jzrq"),
        "estimated_at": payload.get("gztime"),
        "provider": "tiantian-fund-estimate",
    }



def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None
