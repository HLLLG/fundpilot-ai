"""跨市场个股涨跌幅（按重仓代码定向采集）。

Phase 2.1：不再拉全市场 ``stock_*_spot_em``（易断连），改为对季报重仓 dedupe 后
逐只请求东财 ``qt/stock/get``（``105.NVDA`` / ``116.00700`` / ``0.300502``）。
美股在东财失败时回退 ``stock_us_daily`` 最近两日收盘价推算涨跌幅。

返回扁平索引 ``{market:code} → change_percent``；任一失败仅跳过该代码，不编造。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from app.services.eastmoney_spot_client import (
    _COMMON_PARAMS,
    _EASTMONEY_HEADERS,
)

# 本环境 push2 主域常断连；push2delay 可连通（与 us_index_client / 板块分时一致）
_EM_STOCK_GET_URLS = (
    "https://push2delay.eastmoney.com/api/qt/stock/get",
)
# 部分美股在 105 无数据、106 有（如 TSM / GLW / CIEN）
_US_SECID_PREFIXES = ("105", "106", "107")
from app.services.eastmoney_trends_client import fetch_eastmoney_kline_close_percent
from app.services.sector_quote_cache import get_spot_snapshot, save_spot_snapshot
from app.services.us_qdii_quote_policy import UsQuoteMode

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 30
_PER_SYMBOL_TIMEOUT = 8.0
_MAX_WORKERS = 6
_SYMBOL_CACHE_TTL = 300.0  # 5min per ticker
_SYMBOL_CACHE_PREFIX = "market:us_stock_quote:v3"

_US_DAILY_SCRIPT = r"""
import json
import os
import sys

for key in list(os.environ):
    if "proxy" in key.lower() or "http" in key.lower():
        os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"

symbol = sys.argv[1].upper()
try:
    import akshare as ak
    frame = ak.stock_us_daily(symbol=symbol, adjust="")
    if frame is None or frame.empty or len(frame) < 2:
        print(json.dumps({"error": "empty"}))
        sys.exit(1)
    prev_close = float(frame.iloc[-2]["close"])
    last_close = float(frame.iloc[-1]["close"])
    if prev_close == 0:
        print(json.dumps({"error": "zero"}))
        sys.exit(1)
    pct = round((last_close - prev_close) / prev_close * 100, 4)
    print(json.dumps({"change_percent": pct}))
except Exception as exc:
    print(json.dumps({"error": str(exc)}))
    sys.exit(1)
"""


def quote_key(market: str, code: str) -> str:
    return f"{market}:{code}"


def to_eastmoney_secid(market: str, code: str) -> str | None:
    """market + code → 东财 secid（首选）。"""
    candidates = eastmoney_secid_candidates(market, code)
    return candidates[0] if candidates else None


def eastmoney_secid_candidates(market: str, code: str) -> list[str]:
    """market + code → 东财 secid 候选列表（美股多前缀回退）。"""
    m = str(market or "").strip().lower()
    c = str(code or "").strip().upper()
    if not c:
        return []
    if m == "us":
        return [f"{prefix}.{c}" for prefix in _US_SECID_PREFIXES]
    if m == "hk":
        digits = "".join(ch for ch in c if ch.isdigit()).zfill(5)[-5:]
        return [f"116.{digits}"]
    if m == "cn":
        digits = "".join(ch for ch in c if ch.isdigit()).zfill(6)[-6:]
        if not digits:
            return []
        prefix = "1" if digits.startswith("6") else "0"
        return [f"{prefix}.{digits}"]
    return []


def collect_quote_targets(
    holdings_by_fund: dict[str, dict[str, Any]],
) -> set[tuple[str, str]]:
    """从批量持仓提取去重后的 (market, code)。"""
    targets: set[tuple[str, str]] = set()
    for payload in holdings_by_fund.values():
        rows = payload.get("holdings") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            market = str(row.get("market", "")).strip().lower()
            code = str(row.get("code", "")).strip()
            if market in {"us", "hk", "cn"} and code:
                targets.add((market, code))
    return targets


def _symbol_cache_key(market: str, code: str) -> str:
    return f"{_SYMBOL_CACHE_PREFIX}:{market}:{code}"


def _parse_eastmoney_change(data: dict[str, Any]) -> float | None:
    """解析东财个股涨跌幅（f170 为百分数×100；f3 为已缩放涨跌幅）。"""
    raw170 = data.get("f170")
    if raw170 not in (None, "-", ""):
        try:
            return round(float(raw170) / 100.0, 4)
        except (TypeError, ValueError):
            pass
    raw3 = data.get("f3")
    if raw3 not in (None, "-", ""):
        try:
            return round(float(raw3), 4)
        except (TypeError, ValueError):
            pass
    return None


def _fetch_change_via_eastmoney(market: str, code: str) -> float | None:
    params_base = {
        "fields": "f14,f3,f170",
        "ut": _COMMON_PARAMS["ut"],
        "fltt": "2",
        "invt": "2",
    }
    last_error: Exception | None = None
    with httpx.Client(
        headers=_EASTMONEY_HEADERS,
        timeout=_PER_SYMBOL_TIMEOUT,
        trust_env=False,
        follow_redirects=True,
        http2=False,
    ) as client:
        for attempt in range(2):
            for secid in eastmoney_secid_candidates(market, code):
                for url in _EM_STOCK_GET_URLS:
                    try:
                        response = client.get(url, params={**params_base, "secid": secid})
                        response.raise_for_status()
                        data = response.json().get("data") or {}
                        change = _parse_eastmoney_change(data)
                        if change is not None:
                            return change
                    except Exception as exc:  # noqa: BLE001
                        last_error = exc
            if attempt == 0:
                time.sleep(0.25)
    if last_error:
        logger.debug("eastmoney stock %s:%s failed: %s", market, code, last_error)
    return None


def _fetch_us_change_via_daily(ticker: str) -> float | None:
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _US_DAILY_SCRIPT, ticker.upper()],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        payload = json.loads(completed.stdout.strip())
        if not isinstance(payload, dict) or payload.get("error"):
            return None
        return round(float(payload["change_percent"]), 4)
    except Exception:
        logger.debug("stock_us_daily fallback failed for %s", ticker, exc_info=True)
        return None


def _fetch_rth_close_change(market: str, code: str) -> float | None:
    """最近交易日收盘涨跌（盘后/休市口径）。

    美股优先东财 push2delay 实时字段（夜盘口径），避免 ``stock_us_daily`` 滞后一日；
    港股/A 股走 K 线收盘；均失败再回退日线。
    """
    m = str(market).strip().lower()
    live = _fetch_change_via_eastmoney(m, code)
    if live is not None:
        return live
    if m == "us":
        return _fetch_us_change_via_daily(code)
    secid = to_eastmoney_secid(m, code)
    if secid:
        change = fetch_eastmoney_kline_close_percent(secid)
        if change is not None:
            return round(float(change), 4)
    return None


def fetch_stock_change(
    market: str,
    code: str,
    *,
    mode: UsQuoteMode = "live",
) -> float | None:
    """单只股票涨跌幅；按 ``mode`` 选择盘前实时或收盘口径。"""
    cache_key = f"{_symbol_cache_key(market, code)}:{mode}"
    cached = get_spot_snapshot(cache_key, ttl_seconds=_SYMBOL_CACHE_TTL)
    if cached and cached.get("change_percent") is not None:
        try:
            return float(cached["change_percent"])
        except (TypeError, ValueError):
            pass

    change: float | None = None
    if mode == "rth_close":
        change = _fetch_rth_close_change(market, code)
        if change is None:
            change = _fetch_change_via_eastmoney(market, code)
    else:
        change = _fetch_change_via_eastmoney(market, code)
        if change is None and market == "us":
            change = _fetch_us_change_via_daily(code)

    if change is not None:
        save_spot_snapshot(cache_key, {"change_percent": change})
    return change


def fetch_stock_changes_for_targets(
    targets: set[tuple[str, str]],
    *,
    max_workers: int = _MAX_WORKERS,
    deadline: float | None = None,
    mode: UsQuoteMode = "live",
) -> dict[str, float]:
    """并行拉取一组 (market, code) 涨跌幅。"""
    if not targets:
        return {}

    quotes: dict[str, float] = {}
    if deadline is None:
        deadline = time.monotonic() + 25.0

    workers = min(max_workers, max(1, len(targets)))
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {
            executor.submit(fetch_stock_change, market, code, mode=mode): (market, code)
            for market, code in sorted(targets)
        }
        for future in as_completed(futures):
            if time.monotonic() > deadline:
                break
            market, code = futures[future]
            try:
                change = future.result(timeout=max(0.1, deadline - time.monotonic()))
            except Exception:  # noqa: BLE001
                continue
            if change is not None:
                quotes[quote_key(market, code)] = change
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return quotes


def fetch_stock_changes_for_holdings(
    holdings_by_fund: dict[str, dict[str, Any]],
    *,
    deadline: float | None = None,
    mode: UsQuoteMode = "live",
) -> dict[str, float]:
    """按重仓 dedupe 后批量拉取涨跌幅。"""
    targets = collect_quote_targets(holdings_by_fund)
    return fetch_stock_changes_for_targets(targets, deadline=deadline, mode=mode)


def fetch_global_stock_changes() -> dict[str, float] | None:
    """兼容旧入口：无持仓上下文时返回 None（由上层先加载持仓再定向采集）。"""
    return None
