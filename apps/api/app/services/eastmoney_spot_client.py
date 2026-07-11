from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

_HOST_POOL = ("17", "79", "88", "48", "33", "91")
_PREFERRED_PUSH_HOST = "17"

# clist / stock 接口 host：push2delay 优先（push2 数字子域偶发 Server disconnected），
# 失败再依次回落 push2 子域。与 K 线/分时统一首选 push2delay。
_PUSH2_HOSTS = tuple(f"{host}.push2.eastmoney.com" for host in _HOST_POOL)
_CLIST_HOSTS = ("push2delay.eastmoney.com",) + _PUSH2_HOSTS
_STOCK_HOSTS = ("push2delay.eastmoney.com",) + _PUSH2_HOSTS
_CURRENT_FLOW_HOSTS = ("push2delay.eastmoney.com", "push2.eastmoney.com")


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
    host_pool = _CLIST_HOSTS[:max_hosts] if max_hosts is not None else _CLIST_HOSTS
    for attempt in range(max_retries):
        for host in host_pool:
            url = f"https://{host}/api/qt/clist/get"
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
            for host in _STOCK_HOSTS:
                url = f"https://{host}/api/qt/stock/get"
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


def _parse_current_board_flow_kline(
    raw: object,
    *,
    trade_date: str,
) -> dict[str, Any] | None:
    """Parse one dated ``fflow/kline`` row without relabeling stale data."""
    if not isinstance(raw, str):
        return None
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) < 6 or parts[0] != trade_date:
        return None
    try:
        main_force, small, medium, large, super_large = (
            float(value) for value in parts[1:6]
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(
        math.isfinite(value)
        for value in (main_force, small, medium, large, super_large)
    ):
        return None
    return {
        "date": trade_date,
        "main_force_net_yi": _board_yuan_to_yi(main_force),
        "flow_tiers": {
            "super_large_net_yi": _board_yuan_to_yi(super_large),
            "large_net_yi": _board_yuan_to_yi(large),
            "medium_net_yi": _board_yuan_to_yi(medium),
            "small_net_yi": _board_yuan_to_yi(small),
        },
    }


def fetch_eastmoney_current_board_flow(
    secid: str,
    *,
    trade_date: str,
    timeout: float = 1.0,
    max_retries: int = 1,
    max_hosts: int = 1,
) -> dict[str, Any] | None:
    """Fetch a small, exactly dated board-flow window for one Eastmoney secid.

    This endpoint is the targeted fallback for boards omitted from the bulk
    theme snapshot.  A response is usable only when one returned kline has the
    requested trade date; the latest row is never relabeled as that date.
    """
    cleaned_secid = str(secid or "").strip()
    cleaned_trade_date = str(trade_date or "").strip()
    if not cleaned_secid or not cleaned_trade_date:
        return None

    params = {
        "lmt": "10",
        "klt": "101",
        "secid": cleaned_secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "ut": _COMMON_PARAMS["ut"],
    }
    host_pool = _CURRENT_FLOW_HOSTS[: max(0, max_hosts)]
    last_error: Exception | None = None

    with httpx.Client(
        headers=_EASTMONEY_HEADERS,
        timeout=timeout,
        trust_env=False,
        follow_redirects=True,
        http2=False,
    ) as client:
        for attempt in range(max(0, max_retries)):
            for host in host_pool:
                url = f"https://{host}/api/qt/stock/fflow/kline/get"
                try:
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    klines = ((response.json().get("data") or {}).get("klines") or [])
                    for raw in klines:
                        parsed = _parse_current_board_flow_kline(
                            raw,
                            trade_date=cleaned_trade_date,
                        )
                        if parsed is not None:
                            return parsed
                    # Every host serves the same dated series. A successful
                    # response without the target date is authoritative and
                    # should not trigger extra host/retry latency.
                    return None
                except Exception as exc:
                    last_error = exc
                    logger.debug(
                        "eastmoney current board flow %s host=%s attempt=%s failed: %s",
                        cleaned_secid,
                        host,
                        attempt + 1,
                        exc,
                    )
            if attempt + 1 < max_retries:
                time.sleep(0.1 * (attempt + 1))
    if last_error is not None:
        logger.info(
            "eastmoney current board flow %s failed: %s",
            cleaned_secid,
            last_error,
        )
    return None


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


# 经典行业/概念板块（与蚂蚁财富、东财资金流向页一致）；勿加 f:!50（该过滤为细分行业，如「防水材料」）
_BOARD_TYPE_PARAMS: dict[str, tuple[str, str]] = {
    "industry": ("m:90 t:2", "f3"),
    "concept": ("m:90 t:3", "f12"),
}


def fetch_eastmoney_board_records(
    board_type: str,
    *,
    timeout: float = 15.0,
    max_retries: int = 2,
    max_hosts: int | None = None,
    max_pages: int = 6,
) -> list[dict[str, Any]]:
    """拉取东财行业/概念板块列表（涨跌幅 + 主力净流入）。"""
    spec = _BOARD_TYPE_PARAMS.get(board_type)
    if spec is None:
        raise ValueError(f"unsupported board_type: {board_type}")

    fs, fid = spec
    params = {
        **_COMMON_PARAMS,
        "pz": "100",
        "fid": fid,
        "fs": fs,
        "fields": "f3,f12,f14,f62,f66,f72,f78,f84",
    }

    errors: list[str] = []
    try:
        with httpx.Client(
            headers=_EASTMONEY_HEADERS,
            timeout=timeout,
            trust_env=False,
            follow_redirects=True,
            http2=False,
        ) as client:
            return _fetch_paginated_board_records(
                client,
                params,
                max_retries=max_retries,
                max_hosts=max_hosts,
                max_pages=max_pages,
            )
    except Exception as exc:
        errors.append(f"httpx: {exc}")
        logger.debug("eastmoney board httpx failed (%s): %s", board_type, exc)

    try:
        return _fetch_board_records_via_requests(
            params,
            timeout=timeout,
            max_pages=max_pages,
        )
    except Exception as exc:
        errors.append(f"requests: {exc}")
        logger.info("eastmoney board requests fallback failed (%s): %s", board_type, exc)

    raise RuntimeError("; ".join(errors) or "eastmoney board fetch failed")


def _fetch_board_records_via_requests(
    base_params: dict[str, str],
    *,
    timeout: float,
    max_pages: int,
) -> list[dict[str, Any]]:
    """与 AkShare 相同：requests + push2delay 优先（httpx 偶发 reset 时更稳）。"""
    import requests

    session = requests.Session()
    session.trust_env = False
    last_error: Exception | None = None

    for host in _CLIST_HOSTS:
        url = f"https://{host}/api/qt/clist/get"
        try:
            result: list[dict[str, Any]] = []
            for page in range(1, max_pages + 1):
                page_params = {**base_params, "pn": str(page)}
                response = session.get(url, params=page_params, headers=_EASTMONEY_HEADERS, timeout=timeout)
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or {}
                rows = list(data.get("diff") or [])
                if not rows:
                    break
                result.extend(_parse_board_record_rows(rows))
                total = int(data.get("total") or 0)
                page_size = max(len(rows), 1)
                if page >= math.ceil(total / page_size):
                    break
                time.sleep(0.1)
            if result:
                return _dedupe_board_records(result)
        except Exception as exc:
            last_error = exc
            logger.debug("eastmoney board requests host=%s failed: %s", host, exc)
            continue

    if last_error is not None:
        raise last_error
    raise RuntimeError("requests board returned no rows")


def _fetch_paginated_board_records(
    client: httpx.Client,
    base_params: dict[str, str],
    *,
    max_retries: int,
    max_hosts: int | None = None,
    max_pages: int = 6,
) -> list[dict[str, Any]]:
    params = {**base_params, "pn": "1"}
    first = _request_board_page(client, params, max_retries=max_retries, max_hosts=max_hosts)
    rows = list(first.get("diff") or [])
    total = int(first.get("total") or 0)
    page_size = max(len(rows), 1)
    total_pages = max(1, math.ceil(total / page_size))

    result = _parse_board_record_rows(rows)
    last_page = min(total_pages, max_pages)
    for page in range(2, last_page + 1):
        page_params = {**params, "pn": str(page)}
        try:
            payload = _request_board_page(
                client,
                page_params,
                max_retries=max_retries,
                max_hosts=max_hosts,
            )
            result.extend(_parse_board_record_rows(payload.get("diff") or []))
        except Exception as exc:
            logger.debug(
                "eastmoney board records pagination stopped at page %s: %s",
                page,
                exc,
            )
            break
        time.sleep(0.15)
    if not result:
        raise RuntimeError("eastmoney board records returned no rows")
    return _dedupe_board_records(result)


def _dedupe_board_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 code（优先）或 name 去重；分页重叠时东财可能返回重复板块。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        code = row.get("code")
        name = str(row.get("name", "")).strip()
        key = str(code).strip() if code not in (None, "") else name
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _parse_board_record_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("f14")
        if name is None:
            continue
        cleaned_name = str(name).strip()
        if not cleaned_name:
            continue
        code = row.get("f12")
        change = _as_board_float(row.get("f3"))
        main_force_yuan = _as_board_float(row.get("f62"))
        tier_yuan = [_as_board_float(row.get(key)) for key in ("f66", "f72", "f78", "f84")]
        if change is None and main_force_yuan is None and all(v is None for v in tier_yuan):
            continue
        parsed.append(
            {
                "name": cleaned_name,
                "code": str(code).strip() if code not in (None, "-", "") else None,
                "change_percent": change,
                "main_force_net_yi": _board_yuan_to_yi(main_force_yuan),
                "super_large_net_yi": _board_yuan_to_yi(tier_yuan[0]),
                "large_net_yi": _board_yuan_to_yi(tier_yuan[1]),
                "medium_net_yi": _board_yuan_to_yi(tier_yuan[2]),
                "small_net_yi": _board_yuan_to_yi(tier_yuan[3]),
            }
        )
    return parsed


def _as_board_float(value: object) -> float | None:
    if value in (None, "-", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _board_yuan_to_yi(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value / 1e8, 2)


# 主题板块批量指标：概念/行业/指数 clist（f3=1d, f109=5d, f62=主力净流入）
_CLIST_THEME_POOLS: dict[str, dict[str, str]] = {
    "concept": {
        "fs": "m:90 t:3 f:!50",
        "fid": "f3",
    },
    "industry": {
        "fs": "m:90 t:2 f:!50",
        "fid": "f3",
    },
    "index": {
        "fs": "m:2",
        "fid": "f12",
        "wbp2u": "|0|0|0|web",
    },
}
_CLIST_THEME_FIELDS = "f12,f14,f3,f109,f62,f66,f72,f78,f84"
_CLIST_THEME_PAGE_SIZE = "100"
_CLIST_THEME_MAX_PAGES = 8
_CLIST_THEME_METRIC_KEYS = (
    "change_1d",
    "change_5d",
    "main_force_net_yi",
    "super_large_net_yi",
    "large_net_yi",
    "medium_net_yi",
    "small_net_yi",
)


def _parse_clist_theme_rows(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float | None]]:
    by_code: dict[str, dict[str, float | None]] = {}
    for row in rows:
        code = row.get("f12")
        if code in (None, "-", ""):
            continue
        key = str(code).strip()
        if not key:
            continue
        parsed = {
            "change_1d": _as_board_float(row.get("f3")),
            "change_5d": _as_board_float(row.get("f109")),
            "main_force_net_yi": _board_yuan_to_yi(_as_board_float(row.get("f62"))),
            "super_large_net_yi": _board_yuan_to_yi(_as_board_float(row.get("f66"))),
            "large_net_yi": _board_yuan_to_yi(_as_board_float(row.get("f72"))),
            "medium_net_yi": _board_yuan_to_yi(_as_board_float(row.get("f78"))),
            "small_net_yi": _board_yuan_to_yi(_as_board_float(row.get("f84"))),
        }
        if all(parsed[key] is None for key in _CLIST_THEME_METRIC_KEYS):
            continue
        existing = by_code.get(key)
        if existing is None:
            by_code[key] = parsed
            continue
        for metric in _CLIST_THEME_METRIC_KEYS:
            if existing.get(metric) is None and parsed.get(metric) is not None:
                existing[metric] = parsed[metric]
    return by_code


def _merge_clist_theme_chunks(
    merged: dict[str, dict[str, float | None]],
    chunk: dict[str, dict[str, float | None]],
) -> None:
    for code, values in chunk.items():
        if code not in merged:
            merged[code] = values
            continue
        for metric in _CLIST_THEME_METRIC_KEYS:
            if merged[code].get(metric) is None and values.get(metric) is not None:
                merged[code][metric] = values[metric]


# 兼容旧名
_CLIST_CHANGE_POOLS = _CLIST_THEME_POOLS
_CLIST_CHANGE_FIELDS = _CLIST_THEME_FIELDS
_CLIST_CHANGE_PAGE_SIZE = _CLIST_THEME_PAGE_SIZE
_CLIST_CHANGE_MAX_PAGES = _CLIST_THEME_MAX_PAGES


def _parse_clist_change_rows(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float | None]]:
    return _parse_clist_theme_rows(rows)


def _fetch_clist_theme_pool(
    pool_name: str,
    *,
    timeout: float = 15.0,
    max_retries: int = 2,
    max_pages: int = 3,
) -> dict[str, dict[str, float | None]]:
    spec = _CLIST_THEME_POOLS.get(pool_name)
    if spec is None:
        raise ValueError(f"unsupported clist theme pool: {pool_name}")

    params = {
        **_COMMON_PARAMS,
        "pz": _CLIST_THEME_PAGE_SIZE,
        "fields": _CLIST_THEME_FIELDS,
        **spec,
    }
    errors: list[str] = []
    try:
        with httpx.Client(
            headers=_EASTMONEY_HEADERS,
            timeout=timeout,
            trust_env=False,
            follow_redirects=True,
            http2=False,
        ) as client:
            return _fetch_paginated_clist_theme(
                client,
                params,
                max_retries=max_retries,
                max_pages=max_pages,
            )
    except Exception as exc:
        errors.append(f"httpx: {exc}")
        logger.debug("clist theme pool httpx failed (%s): %s", pool_name, exc)

    try:
        return _fetch_clist_theme_via_requests(params, timeout=timeout, max_pages=max_pages)
    except Exception as exc:
        errors.append(f"requests: {exc}")
        logger.info("clist theme pool requests fallback failed (%s): %s", pool_name, exc)

    raise RuntimeError("; ".join(errors) or f"clist theme pool {pool_name} failed")


def _fetch_clist_change_pool(
    pool_name: str,
    *,
    timeout: float = 15.0,
    max_retries: int = 2,
    max_pages: int = 3,
) -> dict[str, dict[str, float | None]]:
    return _fetch_clist_theme_pool(
        pool_name,
        timeout=timeout,
        max_retries=max_retries,
        max_pages=max_pages,
    )


def _fetch_paginated_clist_theme(
    client: httpx.Client,
    base_params: dict[str, str],
    *,
    max_retries: int,
    max_pages: int,
) -> dict[str, dict[str, float | None]]:
    params = {**base_params, "pn": "1"}
    first = _request_board_page(client, params, max_retries=max_retries)
    rows = list(first.get("diff") or [])
    total = int(first.get("total") or 0)
    try:
        page_size = max(int(str(base_params.get("pz", _CLIST_THEME_PAGE_SIZE))), 1)
    except ValueError:
        page_size = max(len(rows), 1)
    total_pages = max(1, math.ceil(total / page_size))
    merged = _parse_clist_theme_rows(rows)
    last_page = min(total_pages, max_pages)
    for page in range(2, last_page + 1):
        page_params = {**params, "pn": str(page)}
        try:
            payload = _request_board_page(
                client,
                page_params,
                max_retries=max_retries,
            )
            _merge_clist_theme_chunks(merged, _parse_clist_theme_rows(payload.get("diff") or []))
        except Exception as exc:
            logger.debug("clist theme pagination stopped at page %s: %s", page, exc)
            break
        time.sleep(0.1)
    if not merged:
        raise RuntimeError("clist theme pool returned no rows")
    return merged


def _fetch_paginated_clist_changes(
    client: httpx.Client,
    base_params: dict[str, str],
    *,
    max_retries: int,
    max_pages: int,
) -> dict[str, dict[str, float | None]]:
    return _fetch_paginated_clist_theme(
        client,
        base_params,
        max_retries=max_retries,
        max_pages=max_pages,
    )


def _fetch_clist_theme_via_requests(
    base_params: dict[str, str],
    *,
    timeout: float,
    max_pages: int,
) -> dict[str, dict[str, float | None]]:
    import requests

    session = requests.Session()
    session.trust_env = False
    last_error: Exception | None = None
    for host in _CLIST_HOSTS:
        url = f"https://{host}/api/qt/clist/get"
        try:
            merged: dict[str, dict[str, float | None]] = {}
            for page in range(1, max_pages + 1):
                page_params = {**base_params, "pn": str(page)}
                response = session.get(
                    url,
                    params=page_params,
                    headers=_EASTMONEY_HEADERS,
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or {}
                rows = list(data.get("diff") or [])
                if not rows:
                    break
                _merge_clist_theme_chunks(merged, _parse_clist_theme_rows(rows))
                total = int(data.get("total") or 0)
                page_size = max(len(rows), 1)
                if page >= math.ceil(total / page_size):
                    break
                time.sleep(0.1)
            if merged:
                return merged
        except Exception as exc:
            last_error = exc
            logger.debug("clist theme requests host=%s failed: %s", host, exc)
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("requests clist theme returned no rows")


def _fetch_clist_changes_via_requests(
    base_params: dict[str, str],
    *,
    timeout: float,
    max_pages: int,
) -> dict[str, dict[str, float | None]]:
    return _fetch_clist_theme_via_requests(
        base_params,
        timeout=timeout,
        max_pages=max_pages,
    )


def fetch_eastmoney_clist_theme_metrics_by_code(
    *,
    timeout: float = 15.0,
    max_retries: int = 2,
    max_pages: int = _CLIST_THEME_MAX_PAGES,
) -> dict[str, dict[str, float | None]]:
    """东财 clist 批量：1d(f3)+5d(f109)+主力/四档流(f62/f66-f84)，按 f12 索引。"""
    merged: dict[str, dict[str, float | None]] = {}
    errors: list[str] = []
    pool_names = tuple(_CLIST_THEME_POOLS.keys())
    with ThreadPoolExecutor(max_workers=len(pool_names)) as executor:
        futures = {
            executor.submit(
                _fetch_clist_theme_pool,
                pool_name,
                timeout=timeout,
                max_retries=max_retries,
                max_pages=max_pages,
            ): pool_name
            for pool_name in pool_names
        }
        for future in as_completed(futures):
            pool_name = futures[future]
            try:
                chunk = future.result()
            except Exception as exc:
                errors.append(f"{pool_name}: {exc}")
                logger.info("clist theme pool %s failed: %s", pool_name, exc)
                continue
            _merge_clist_theme_chunks(merged, chunk)
    if not merged and errors:
        raise RuntimeError("; ".join(errors))
    return merged


def fetch_eastmoney_clist_change_by_code(
    *,
    timeout: float = 15.0,
    max_retries: int = 2,
    max_pages: int = _CLIST_CHANGE_MAX_PAGES,
) -> dict[str, dict[str, float | None]]:
    """兼容别名：同 fetch_eastmoney_clist_theme_metrics_by_code。"""
    return fetch_eastmoney_clist_theme_metrics_by_code(
        timeout=timeout,
        max_retries=max_retries,
        max_pages=max_pages,
    )
