from __future__ import annotations

import json
import logging
import time
from functools import lru_cache

import requests

logger = logging.getLogger(__name__)

INDEX_DAILY_RESPONSE_TTL_SECONDS = 3600
_INDEX_TTL_CACHE: dict[str, tuple[float, dict | None]] = {}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}

_INDEX_NAMES = {
    "000300": "沪深300",
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
}


def index_display_name(index_symbol: str) -> str:
    return _INDEX_NAMES.get(index_symbol, index_symbol)


def _sina_symbol(index_symbol: str) -> str:
    code = index_symbol.strip()
    if code.startswith(("sz", "sh")):
        return code
    if code.startswith(("39", "98")):
        return f"sz{code}"
    return f"sh{code}"


@lru_cache(maxsize=64)
def _fetch_index_daily_history_impl(index_symbol: str, trading_days: int = 252) -> dict | None:
    """拉取指数日线收盘价，优先新浪（AkShare 指数接口在部分网络下不稳定）。"""
    days = max(20, min(trading_days, 800))
    symbol = _sina_symbol(index_symbol)
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData"
    )
    try:
        response = requests.get(
            url,
            params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": days},
            headers=_HEADERS,
            timeout=15,
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = json.loads(response.text)
    except Exception as exc:
        logger.warning("sina index daily failed for %s: %s", index_symbol, exc)
        return None

    if not isinstance(payload, list) or not payload:
        return None

    data: list[dict[str, float | str]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        day = str(row.get("day") or "")[:10]
        close = row.get("close")
        if not day or close is None:
            continue
        try:
            data.append({"date": day, "close": round(float(close), 4)})
        except (TypeError, ValueError):
            continue

    if len(data) < 2:
        return None

    data.sort(key=lambda item: str(item["date"]))
    if len(data) > days:
        data = data[-days:]

    return {"data": data, "source": "sina"}


def fetch_index_daily_history(index_symbol: str, trading_days: int = 252) -> dict | None:
    key = f"{index_symbol.strip()}:{max(20, min(trading_days, 800))}"
    now = time.time()
    cached = _INDEX_TTL_CACHE.get(key)
    if cached is not None and now - cached[0] < INDEX_DAILY_RESPONSE_TTL_SECONDS:
        return cached[1]
    result = _fetch_index_daily_history_impl(index_symbol, trading_days)
    _INDEX_TTL_CACHE[key] = (now, result)
    return result
