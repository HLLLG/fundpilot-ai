from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from threading import RLock

import requests

from app.services.amac_benchmark_index_data import amac_code_to_entry
from app.services.sector_registry_data import CANONICAL_SECTORS, THEME_BOARD_INDEX

logger = logging.getLogger(__name__)

INDEX_DAILY_RESPONSE_TTL_SECONDS = 3600
INDEX_DAILY_RESPONSE_CACHE_MAX_ENTRIES = 128
_INDEX_TTL_CACHE: OrderedDict[str, tuple[float, dict | None]] = OrderedDict()
_INDEX_TTL_CACHE_LOCK = RLock()

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}

_EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EASTMONEY_HEADERS = {
    **_HEADERS,
    "Referer": "https://quote.eastmoney.com/",
}

_INDEX_NAMES = {
    "000300": "沪深300",
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "930713": "中证人工智能主题指数",
    "931071": "中证人工智能产业指数",
}


def _build_eastmoney_quote_refs() -> dict[str, tuple[str, str]]:
    refs: dict[str, tuple[str, str]] = {}
    for _label, (secid, code, _kind, source_name) in CANONICAL_SECTORS.items():
        if code:
            refs.setdefault(code.upper(), (secid, source_name))
    for label, (secid, code, _kind) in THEME_BOARD_INDEX.items():
        refs.setdefault(code.upper(), (secid, label))
    return refs


_EASTMONEY_QUOTE_REFS = _build_eastmoney_quote_refs()


def index_display_name(index_symbol: str) -> str:
    code = index_symbol.strip().upper()
    if code in _INDEX_NAMES:
        return _INDEX_NAMES[code]
    known_quote = _EASTMONEY_QUOTE_REFS.get(code)
    if known_quote is None:
        amac_entry = amac_code_to_entry().get(code)
        if amac_entry:
            name = str(amac_entry.get("index_full_name") or "").strip()
            if name:
                return name
    return _INDEX_NAMES.get(code, known_quote[1] if known_quote else code)


def _eastmoney_quote_ref(index_symbol: str) -> tuple[str, str] | None:
    raw = index_symbol.strip()
    if not raw:
        return None
    if "." in raw and raw.split(".", 1)[0] in {"0", "1", "2", "90"}:
        return raw, raw.split(".", 1)[1]

    code = raw.upper()
    if code.startswith(("SH", "SZ")):
        code = code[2:]
    known_quote = _EASTMONEY_QUOTE_REFS.get(code)
    if known_quote is not None:
        return known_quote[0], code
    if code in _INDEX_NAMES:
        return None

    amac_entry = amac_code_to_entry().get(code)
    if amac_entry:
        secid = str(amac_entry.get("eastmoney_secid") or "").strip()
        if secid:
            return secid, code
    return None


def _fetch_eastmoney_daily_history(
    index_symbol: str,
    trading_days: int,
) -> dict | None:
    quote_ref = _eastmoney_quote_ref(index_symbol)
    if quote_ref is None:
        return None

    secid, _code = quote_ref
    try:
        response = requests.get(
            _EASTMONEY_URL,
            params={
                "secid": secid,
                "klt": 101,
                "fqt": 0,
                "lmt": trading_days,
                "end": "20500101",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            },
            headers=_EASTMONEY_HEADERS,
            timeout=6,
            proxies={"http": None, "https": None},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("eastmoney index daily failed for %s: %s", index_symbol, exc)
        return None

    data_node = payload.get("data") if isinstance(payload, dict) else None
    rows = data_node.get("klines") if isinstance(data_node, dict) else None
    if not isinstance(rows, list) or not rows:
        return None

    data: list[dict[str, float | str]] = []
    for row in rows:
        columns = str(row).split(",")
        if len(columns) < 3:
            continue
        try:
            data.append({"date": columns[0][:10], "close": round(float(columns[2]), 4)})
        except (TypeError, ValueError):
            continue

    if len(data) < 2:
        return None
    data.sort(key=lambda item: str(item["date"]))
    return {"data": data[-trading_days:], "source": "eastmoney"}


def _sina_symbol(index_symbol: str) -> str:
    code = index_symbol.strip()
    if code.startswith(("sz", "sh")):
        return code
    if code.startswith(("39", "98")):
        return f"sz{code}"
    return f"sh{code}"


def _fetch_index_daily_history_impl(index_symbol: str, trading_days: int = 252) -> dict | None:
    """拉取指数日线收盘价：已登记主题走东财，其余优先新浪。"""
    days = max(20, min(trading_days, 800))
    eastmoney_result = _fetch_eastmoney_daily_history(index_symbol, days)
    if eastmoney_result is not None:
        return eastmoney_result

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
    now = time.monotonic()
    with _INDEX_TTL_CACHE_LOCK:
        cached = _INDEX_TTL_CACHE.get(key)
        if cached is not None:
            cached_at, value = cached
            if now - cached_at < INDEX_DAILY_RESPONSE_TTL_SECONDS:
                _INDEX_TTL_CACHE.move_to_end(key)
                return value
            _INDEX_TTL_CACHE.pop(key, None)

    result = _fetch_index_daily_history_impl(index_symbol, trading_days)
    with _INDEX_TTL_CACHE_LOCK:
        _INDEX_TTL_CACHE[key] = (now, result)
        _INDEX_TTL_CACHE.move_to_end(key)
        while len(_INDEX_TTL_CACHE) > max(1, INDEX_DAILY_RESPONSE_CACHE_MAX_ENTRIES):
            _INDEX_TTL_CACHE.popitem(last=False)
    return result
