from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Literal

import httpx

from app.services.eastmoney_http import (
    eastmoney_backoff,
    eastmoney_budgeted,
    eastmoney_httpx_client,
    eastmoney_requests_client,
)
from app.services.eastmoney_spot_client import (
    _COMMON_PARAMS,
    _EASTMONEY_HEADERS,
    _board_yuan_to_yi,
)
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_labels import normalize_sector_label
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.theme_board_snapshot import list_theme_board_universe
from app.services.trading_session import build_trading_session

logger = logging.getLogger(__name__)

FlowRange = Literal["week", "month"]

_FLOW_HISTORY_PATH = "/api/qt/stock/fflow/daykline/get"
# 实测 63/28 等子域常 Server disconnected；80/82 更稳，与 spot 接口一样需带 _COMMON_PARAMS。
# 仅保留最稳的 3 个 host：减少失败时逐 host 串行重试造成的尾延迟（原 5 host × 4 retry）。
_FLOW_HISTORY_HOSTS = (
    "80.push2his.eastmoney.com",
    "82.push2his.eastmoney.com",
    "push2his.eastmoney.com",
)
_FLOW_FETCH_MAX_RETRIES = 2
_CACHE_PREFIX = "board-flow-hist:v2:"
_LEGACY_CACHE_PREFIX = "board-flow-hist:v1:"
_LIVE_TTL_SECONDS = 900.0
_CLOSED_TTL_SECONDS = 3600.0
_INTRADAY_SESSIONS = {
    "trading_day_intraday",
    "trading_day_pre_close",
    "trading_day_pre_open",
}
_RANGE_TRADING_DAYS: dict[FlowRange, int] = {"week": 5, "month": 20}

_LABEL_TO_FLOW_CODE: dict[str, str] | None = None


def _normalize_board_code(board_code: str) -> str:
    code = board_code.strip().upper()
    if not code:
        return code
    if code.startswith("BK"):
        return code
    if code.isdigit():
        return f"BK{code}"
    return code


def _build_label_to_flow_code_map() -> dict[str, str]:
    global _LABEL_TO_FLOW_CODE
    if _LABEL_TO_FLOW_CODE is not None:
        return _LABEL_TO_FLOW_CODE

    mapping: dict[str, str] = {}
    try:
        for entry in list_theme_board_universe():
            label = str(entry.get("sector_label") or "").strip()
            flow_code = str(entry.get("flow_source_code") or "").strip()
            if label and flow_code:
                mapping[label] = flow_code
    except Exception as exc:
        logger.debug("board flow label map failed: %s", exc)

    _LABEL_TO_FLOW_CODE = mapping
    return mapping


def resolve_board_flow_code(
    *,
    sector_label: str | None = None,
    board_code: str | None = None,
) -> tuple[str | None, str | None]:
    """解析 BK 代码与展示用板块名（主题榜 → canonical）。"""
    if board_code:
        code = _normalize_board_code(board_code)
        label = (sector_label or "").strip() or None
        return (code or None), label

    return resolve_board_flow_code_for_sector(sector_label)


def resolve_board_flow_code_for_sector(sector_name: str | None) -> tuple[str | None, str | None]:
    """sector_name → BK 码：主题白名单 → canonical。"""
    label = normalize_sector_label(sector_name)
    if not label:
        return None, None

    flow_code = _build_label_to_flow_code_map().get(label)
    if flow_code:
        return flow_code, label

    canon = get_canonical_sector(label)
    if canon is not None and str(canon.eastmoney_secid).startswith("90."):
        resolved_code = str(canon.source_code or "").strip()
        if not resolved_code and "." in canon.eastmoney_secid:
            resolved_code = canon.eastmoney_secid.split(".", 1)[1]
        if resolved_code:
            return resolved_code, canon.label

    return None, label


def get_cached_board_flow_series(
    board_code: str,
    *,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """读取（或拉取并缓存）板块完整日资金流序列。"""
    code = _normalize_board_code(board_code)
    if not code:
        return []

    cache_key = _cache_key(code)
    ttl = _cache_ttl_seconds()
    cached = None if force_refresh else get_spot_snapshot(cache_key, ttl_seconds=ttl)

    if cached is None:
        series = fetch_board_flow_series(code)
        if series:
            save_spot_snapshot(
                cache_key,
                {
                    "board_code": code,
                    "series": series,
                    "refreshed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            return series

        stale = get_spot_snapshot_any_age(cache_key)
        if stale and stale.get("series"):
            logger.info("board flow history using stale cache for %s", code)
            return list(stale.get("series") or [])

        # v1 只保存资金流字段。新版源暂时不可用时继续保留旧资金流证据，
        # 但不会把缺失的收盘价伪装成价格强度证据。
        legacy = get_spot_snapshot_any_age(f"{_LEGACY_CACHE_PREFIX}{code}")
        if legacy and legacy.get("series"):
            logger.info("board flow history using legacy cache for %s", code)
            return list(legacy.get("series") or [])

        return []

    return list(cached.get("series") or [])


def get_board_flow_series_cache_only(board_code: str) -> list[dict[str, Any]]:
    """只读板块历史缓存；用于请求链路内的低延迟研究因子。"""
    code = _normalize_board_code(board_code)
    if not code:
        return []
    for prefix in (_CACHE_PREFIX, _LEGACY_CACHE_PREFIX):
        snapshot = get_spot_snapshot_any_age(f"{prefix}{code}")
        if snapshot and snapshot.get("series"):
            return list(snapshot.get("series") or [])
    return []


def _cache_ttl_seconds() -> float:
    session = build_trading_session()
    session_kind = str(session.get("session_kind") or "")
    if session_kind in _INTRADAY_SESSIONS:
        return _LIVE_TTL_SECONDS
    return _CLOSED_TTL_SECONDS


def _cache_key(board_code: str) -> str:
    return f"{_CACHE_PREFIX}{board_code}"


def parse_board_flow_kline(raw: str) -> dict[str, Any] | None:
    """解析东财 daykline 单行：资金流、收盘价与当日涨跌幅。"""
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) < 6:
        return None
    try:
        main_yi = _board_yuan_to_yi(float(parts[1]))
        small_yi = _board_yuan_to_yi(float(parts[2]))
        medium_yi = _board_yuan_to_yi(float(parts[3]))
        large_yi = _board_yuan_to_yi(float(parts[4]))
        super_large_yi = _board_yuan_to_yi(float(parts[5]))
    except (TypeError, ValueError):
        return None
    close_price = _optional_float(parts[11]) if len(parts) > 11 else None
    change_percent = _optional_float(parts[12]) if len(parts) > 12 else None
    return {
        "date": parts[0],
        "main_force_net_yi": main_yi,
        "close_price": close_price,
        "change_percent": change_percent,
        "flow_tiers": {
            "super_large_net_yi": super_large_yi,
            "large_net_yi": large_yi,
            "medium_net_yi": medium_yi,
            "small_net_yi": small_yi,
        },
    }


def _optional_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _parse_flow_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    klines = (payload.get("data") or {}).get("klines") or []
    points: list[dict[str, Any]] = []
    for raw in klines:
        if not isinstance(raw, str):
            continue
        parsed = parse_board_flow_kline(raw)
        if parsed is not None:
            points.append(parsed)
    return points


def _flow_history_params(board_code: str) -> dict[str, str]:
    return {
        **_COMMON_PARAMS,
        "lmt": "0",
        "klt": "101",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "secid": f"90.{board_code}",
    }


def _fetch_flow_history_via_httpx(
    client: httpx.Client,
    *,
    params: dict[str, str],
    max_retries: int,
) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        for host in _FLOW_HISTORY_HOSTS:
            url = f"https://{host}{_FLOW_HISTORY_PATH}"
            try:
                response = client.get(url, params=params)
                response.raise_for_status()
                points = _parse_flow_payload(response.json())
                if points:
                    return points
                last_error = RuntimeError(f"{host}: empty klines")
            except Exception as exc:
                last_error = exc
                logger.debug(
                    "board flow history httpx failed (host=%s attempt=%s): %s",
                    host,
                    attempt + 1,
                    exc,
                )
        if attempt + 1 < max_retries:
            eastmoney_backoff(attempt, base_seconds=0.6)
    if last_error is not None:
        raise last_error
    return []


@eastmoney_budgeted
def fetch_board_flow_series(board_code: str, *, timeout: float = 8.0) -> list[dict[str, Any]]:
    code = _normalize_board_code(board_code)
    if not code:
        return []

    params = _flow_history_params(code)
    headers = {**_EASTMONEY_HEADERS, "Referer": "https://data.eastmoney.com/bkzj/"}
    errors: list[str] = []

    try:
        with eastmoney_httpx_client(
            headers=headers,
            timeout=timeout,
            trust_env=False,
            follow_redirects=True,
        ) as client:
            points = _fetch_flow_history_via_httpx(
                client,
                params=params,
                max_retries=_FLOW_FETCH_MAX_RETRIES,
            )
            if points:
                return points
            errors.append("httpx: empty klines")
    except Exception as exc:
        errors.append(f"httpx: {exc}")
        logger.debug("board flow history httpx failed (%s): %s", code, exc)

    try:
        requests_client = eastmoney_requests_client(headers)

        for attempt in range(_FLOW_FETCH_MAX_RETRIES):
            for host in _FLOW_HISTORY_HOSTS:
                url = f"https://{host}{_FLOW_HISTORY_PATH}"
                try:
                    response = requests_client.get(
                        url,
                        params=params,
                        headers=headers,
                        timeout=timeout,
                        proxies={"http": None, "https": None},
                    )
                    response.raise_for_status()
                    points = _parse_flow_payload(response.json())
                    if points:
                        return points
                except Exception as exc:
                    errors.append(f"requests@{host}: {exc}")
            if attempt + 1 < _FLOW_FETCH_MAX_RETRIES:
                eastmoney_backoff(attempt, base_seconds=0.4)
    except Exception as exc:
        errors.append(f"requests: {exc}")

    logger.info("board flow history fetch failed (%s): %s", code, "; ".join(errors[:3]))
    return []


def prefetch_board_flow_histories(
    board_codes: list[str],
    *,
    max_workers: int = 2,
) -> int:
    """主题榜刷新后预热 BK 历史资金流缓存（跳过已有有效缓存）。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    unique_codes: list[str] = []
    seen: set[str] = set()
    for raw in board_codes:
        code = _normalize_board_code(str(raw or ""))
        if not code or code in seen:
            continue
        seen.add(code)
        cache_key = _cache_key(code)
        if get_spot_snapshot(cache_key, ttl_seconds=_cache_ttl_seconds()) is not None:
            continue
        unique_codes.append(code)

    if not unique_codes:
        return 0

    warmed = 0

    def warm_one(flow_code: str) -> int:
        time.sleep(0.75)
        series = get_cached_board_flow_series(flow_code, force_refresh=True)
        return 1 if series else 0

    workers = max(1, min(max_workers, len(unique_codes)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(warm_one, code) for code in unique_codes]
        for future in as_completed(futures):
            try:
                warmed += int(future.result())
            except Exception as exc:
                logger.debug("board flow prefetch worker failed: %s", exc)
    return warmed


def _slice_range(points: list[dict[str, Any]], flow_range: FlowRange) -> list[dict[str, Any]]:
    days = _RANGE_TRADING_DAYS[flow_range]
    if len(points) <= days:
        return list(points)
    return points[-days:]


def _sum_main_force(points: list[dict[str, Any]]) -> float | None:
    values = [
        float(point["main_force_net_yi"])
        for point in points
        if point.get("main_force_net_yi") is not None
    ]
    if not values:
        return None
    return round(sum(values), 2)


def get_board_flow_history(
    *,
    sector_label: str | None = None,
    board_code: str | None = None,
    flow_range: FlowRange = "week",
    force_refresh: bool = False,
) -> dict[str, Any]:
    resolved_code, resolved_label = resolve_board_flow_code(
        sector_label=sector_label,
        board_code=board_code,
    )
    if not resolved_code:
        return {
            "available": False,
            "range": flow_range,
            "sector_label": resolved_label,
            "board_code": None,
            "points": [],
            "cumulative_net_yi": None,
            "from_cache": False,
            "message": "未找到该板块的资金流代码",
        }

    series = get_cached_board_flow_series(resolved_code, force_refresh=force_refresh)
    cache_key = _cache_key(resolved_code)
    from_cache = (
        not force_refresh
        and get_spot_snapshot(cache_key, ttl_seconds=_cache_ttl_seconds()) is not None
    )
    refreshed_at = None
    cached_snap = get_spot_snapshot(cache_key, ttl_seconds=_cache_ttl_seconds() * 2)
    if cached_snap:
        refreshed_at = cached_snap.get("refreshed_at")

    ranged = _slice_range(series, flow_range)
    available = len(ranged) > 0

    return {
        "available": available,
        "range": flow_range,
        "sector_label": resolved_label,
        "board_code": resolved_code,
        "points": ranged,
        "cumulative_net_yi": _sum_main_force(ranged),
        "from_cache": from_cache,
        "refreshed_at": refreshed_at,
        "message": None if available else "暂无历史资金流数据",
    }
