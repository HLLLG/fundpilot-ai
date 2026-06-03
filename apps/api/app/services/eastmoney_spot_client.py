from __future__ import annotations

import logging
import math
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_EASTMONEY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
    "Connection": "close",
}

_COMMON_PARAMS = {
    "po": "1",
    "np": "1",
    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    "fltt": "2",
    "invt": "2",
}

_HOST_POOL = ("79", "88", "48", "17", "33", "91")


def fetch_eastmoney_boards(
    *,
    timeout: float = 25.0,
    max_retries: int = 3,
    max_hosts: int | None = None,
) -> dict[str, dict[str, float]]:
    """拉取东财概念/行业/指数板块列表（含涨跌幅），直连且不走系统代理。"""
    specs = [
        ("concept", {
            **_COMMON_PARAMS,
            "pz": "100",
            "fid": "f12",
            "fs": "m:90 t:3 f:!50",
            "fields": "f3,f14",
        }),
        ("industry", {
            **_COMMON_PARAMS,
            "pz": "100",
            "fid": "f3",
            "fs": "m:90 t:2 f:!50",
            "fields": "f3,f14",
        }),
        ("index_main", {
            **_COMMON_PARAMS,
            "pz": "100",
            "dect": "1",
            "wbp2u": "|0|0|0|web",
            "fid": "",
            "fs": "b:MK0010",
            "fields": "f3,f14",
        }),
        ("index_csi", {
            **_COMMON_PARAMS,
            "pz": "100",
            "wbp2u": "|0|0|0|web",
            "fid": "f12",
            "fs": "m:2",
            "fields": "f3,f14",
        }),
        ("index_sh", {
            **_COMMON_PARAMS,
            "pz": "100",
            "wbp2u": "|0|0|0|web",
            "fid": "f12",
            "fs": "m:1+t:1",
            "fields": "f3,f14",
        }),
        ("index_sz", {
            **_COMMON_PARAMS,
            "pz": "100",
            "wbp2u": "|0|0|0|web",
            "fid": "f12",
            "fs": "m:0+t:5",
            "fields": "f3,f14",
        }),
    ]

    boards: dict[str, dict[str, float]] = {
        "concept": {},
        "industry": {},
        "index": {},
    }

    failed_specs: list[str] = []

    with httpx.Client(
        headers=_EASTMONEY_HEADERS,
        timeout=timeout,
        trust_env=False,
        follow_redirects=True,
        http2=False,
    ) as client:
        for key, params in specs:
            try:
                fetched = _fetch_paginated_board(
                    client,
                    params,
                    max_retries=max_retries,
                    max_hosts=max_hosts,
                )
            except Exception as exc:
                failed_specs.append(key)
                logger.debug("eastmoney board fetch failed (%s): %s", key, exc)
                continue
            if key.startswith("index"):
                boards["index"].update(fetched)
            else:
                boards[key].update(fetched)

    if failed_specs:
        logger.info(
            "eastmoney partial fetch (%s failed); concept=%s industry=%s index=%s",
            ",".join(failed_specs),
            len(boards["concept"]),
            len(boards["industry"]),
            len(boards["index"]),
        )

    return boards


def _fetch_paginated_board(
    client: httpx.Client,
    base_params: dict[str, str],
    *,
    max_retries: int,
    max_hosts: int | None = None,
) -> dict[str, float]:
    params = {**base_params, "pn": "1"}
    first = _request_board_page(client, params, max_retries=max_retries, max_hosts=max_hosts)
    rows = list(first.get("diff") or [])
    total = int(first.get("total") or 0)
    page_size = max(len(rows), 1)
    total_pages = max(1, math.ceil(total / page_size))

    result: dict[str, float] = {}
    _absorb_board_rows(rows, result)
    for page in range(2, total_pages + 1):
        page_params = {**params, "pn": str(page)}
        try:
            payload = _request_board_page(
                client,
                page_params,
                max_retries=max_retries,
                max_hosts=max_hosts,
            )
            _absorb_board_rows(payload.get("diff") or [], result)
        except Exception as exc:
            logger.debug(
                "eastmoney pagination stopped at page %s with %s rows: %s",
                page,
                len(result),
                exc,
            )
            break
        time.sleep(0.15)
    if not result:
        raise RuntimeError("eastmoney board returned no rows")
    return result


def _request_board_page(
    client: httpx.Client,
    params: dict[str, str],
    *,
    max_retries: int,
    max_hosts: int | None = None,
) -> dict[str, Any]:
    last_error: Exception | None = None
    host_pool = _HOST_POOL[:max_hosts] if max_hosts is not None else _HOST_POOL
    for attempt in range(max_retries):
        for host in host_pool:
            url = f"https://{host}.push2.eastmoney.com/api/qt/clist/get"
            try:
                response = client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or {}
                return {
                    "diff": data.get("diff") or [],
                    "total": data.get("total") or 0,
                }
            except Exception as exc:
                last_error = exc
                logger.debug("eastmoney request failed host=%s attempt=%s: %s", host, attempt + 1, exc)
        if attempt + 1 < max_retries:
            time.sleep(0.4 * (attempt + 1))
    if last_error is not None:
        raise last_error
    return {"diff": [], "total": 0}


def fetch_eastmoney_quote_by_secid(
    secid: str,
    *,
    timeout: float = 8.0,
    max_retries: int = 2,
) -> tuple[str | None, float | None]:
    """按东财 secid 拉单板块/指数涨跌幅（如商业航天 90.BK0963）。"""
    cleaned = str(secid).strip()
    if not cleaned:
        return None, None

    params = {
        "secid": cleaned,
        "fields": "f14,f3",
        "ut": _COMMON_PARAMS["ut"],
        "fltt": "2",
        "invt": "2",
    }

    with httpx.Client(
        headers=_EASTMONEY_HEADERS,
        timeout=timeout,
        trust_env=False,
        follow_redirects=True,
        http2=False,
    ) as client:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            for host in _HOST_POOL:
                url = f"https://{host}.push2.eastmoney.com/api/qt/stock/get"
                try:
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    data = response.json().get("data") or {}
                    name = data.get("f14")
                    change = data.get("f3")
                    if change in (None, "-"):
                        return (str(name).strip() if name else None), None
                    return (
                        str(name).strip() if name else None,
                        round(float(change), 4),
                    )
                except Exception as exc:
                    last_error = exc
                    logger.debug("eastmoney secid %s host=%s failed: %s", cleaned, host, exc)
            if attempt + 1 < max_retries:
                time.sleep(0.35 * (attempt + 1))
        if last_error:
            logger.info("eastmoney secid quote %s failed: %s", cleaned, last_error)
    return None, None


def fetch_eastmoney_sector_quote(
    sector_name: str,
    *,
    source_type: str = "concept",
    timeout: float = 10.0,
    max_pages: int = 12,
) -> float | None:
    """按板块名称精确匹配单条涨跌幅（分页早停，用于商业航天等补拉）。"""
    cleaned = str(sector_name).strip()
    if not cleaned:
        return None

    if source_type == "industry":
        params = {
            **_COMMON_PARAMS,
            "pz": "100",
            "fid": "f3",
            "fs": "m:90 t:2 f:!50",
            "fields": "f3,f14",
        }
    else:
        params = {
            **_COMMON_PARAMS,
            "pz": "100",
            "fid": "f12",
            "fs": "m:90 t:3 f:!50",
            "fields": "f3,f14",
        }

    with httpx.Client(
        headers=_EASTMONEY_HEADERS,
        timeout=timeout,
        trust_env=False,
        follow_redirects=True,
        http2=False,
    ) as client:
        page_params = {**params, "pn": "1"}
        try:
            first = _request_board_page(client, page_params, max_retries=2)
        except Exception as exc:
            logger.debug("eastmoney single sector fetch failed: %s", exc)
            return None

        rows = list(first.get("diff") or [])
        hit = _find_sector_row(rows, cleaned)
        if hit is not None:
            return hit

        total = int(first.get("total") or 0)
        page_size = max(len(rows), 1)
        total_pages = min(max(1, math.ceil(total / page_size)), max_pages)

        for page in range(2, total_pages + 1):
            try:
                payload = _request_board_page(
                    client,
                    {**page_params, "pn": str(page)},
                    max_retries=2,
                )
                hit = _find_sector_row(payload.get("diff") or [], cleaned)
                if hit is not None:
                    return hit
            except Exception:
                break
            time.sleep(0.08)
    return None


def _find_sector_row(rows: list[dict[str, Any]], sector_name: str) -> float | None:
    for row in rows:
        name = row.get("f14")
        change = row.get("f3")
        if name is None or change in (None, "-"):
            continue
        if str(name).strip() != sector_name:
            continue
        try:
            return round(float(change), 4)
        except (TypeError, ValueError):
            return None
    return None


def _absorb_board_rows(rows: list[dict[str, Any]], target: dict[str, float]) -> None:
    for row in rows:
        name = row.get("f14")
        change = row.get("f3")
        if name is None or change in (None, "-"):
            continue
        cleaned = str(name).strip()
        if not cleaned:
            continue
        try:
            target[cleaned] = round(float(change), 4)
        except (TypeError, ValueError):
            continue
