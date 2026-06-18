"""美股指数现货收盘涨跌（盘后 / 休市顶部指标，方案 A）。

主源：东财全球指数现货 ``push2delay`` ``clist/get``（``100.DJIA/SPX/NDX``），
与小倍「指数收盘涨跌」口径一致。部分网络下 ``push2`` 主域不可用但
``push2delay`` 仍可连通（与 A 股板块 K 线同源）。

回退链::

    push2delay 东财全球指数现货 → 新浪 ``index_us_stock_sina`` → ETF 日 K 代理
    （QQQ / SPY / DIA）

公开函数 ``fetch_us_index_spot()`` 返回与 ``us_futures_client`` 相同 symbol 键::

    [{"symbol": "NASDAQ_FUT", "display_name": "纳斯达克", "last_price": 26021.66,
      "change_percent": -1.34, "quote_time": "2026-06-18 04:00:00+08:00",
      "source": "eastmoney_global_spot_push2delay"}]
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 60
_REQUEST_TIMEOUT = 12.0
_BJ = ZoneInfo("Asia/Shanghai")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/center/gridlist.html",
    "Accept": "*/*",
    "Connection": "close",
}

_DISPLAY_NAMES: dict[str, str] = {
    "NASDAQ_FUT": "纳斯达克",
    "SP500_FUT": "标普500",
    "DOW_FUT": "道琼斯",
}

# clist 返回 f12 代码 → 内部 symbol
_EM_CODE_TO_SYMBOL: dict[str, str] = {
    "NDX": "NASDAQ_FUT",
    "SPX": "SP500_FUT",
    "DJIA": "DOW_FUT",
}

# 内部 symbol → 新浪指数代码
_SINA_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("NASDAQ_FUT", ".IXIC"),
    ("SP500_FUT", ".INX"),
    ("DOW_FUT", ".DJI"),
)

# 内部 symbol → ETF 代理（东财/新浪均失败时）
_ETF_PROXIES: dict[str, str] = {
    "NASDAQ_FUT": "QQQ",
    "SP500_FUT": "SPY",
    "DOW_FUT": "DIA",
}

_EM_CLIST_URLS: tuple[str, ...] = (
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
)

_EM_CLIST_PARAMS = {
    "np": "2",
    "fltt": "1",
    "invt": "2",
    "fs": "i:100.DJIA,i:100.SPX,i:100.NDX",
    "fields": "f12,f14,f2,f3,f4,f18,f124",
    "fid": "f3",
    "pn": "1",
    "pz": "10",
    "po": "1",
    "dect": "1",
    "wbp2u": "|0|0|0|web",
}

_SINA_SCRIPT = """
import json, os, sys
for key in list(os.environ):
    if "proxy" in key.lower() or "http" in key.lower():
        os.environ.pop(key, None)
os.environ["NO_PROXY"] = "*"
os.environ.pop("REQUESTS_CA_BUNDLE", None)
os.environ.pop("CURL_CA_BUNDLE", None)

try:
    import akshare as ak
    targets = [(".IXIC", "NASDAQ_FUT"), (".INX", "SP500_FUT"), (".DJI", "DOW_FUT")]
    out = {}
    for sina_sym, internal in targets:
        frame = ak.index_us_stock_sina(symbol=sina_sym)
        if frame is None or getattr(frame, "empty", True):
            continue
        records = json.loads(frame.to_json(orient="records", force_ascii=False))
        out[internal] = records
    print(json.dumps(out, ensure_ascii=False))
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"error": str(exc)}, ensure_ascii=False))
    sys.exit(1)
"""

_ETF_DAILY_SCRIPT = r"""
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
    last_date = str(frame.iloc[-1]["date"])[:10]
    if prev_close == 0:
        print(json.dumps({"error": "zero"}))
        sys.exit(1)
    pct = round((last_close - prev_close) / prev_close * 100, 2)
    print(json.dumps({
        "change_percent": pct,
        "last_price": last_close,
        "quote_time": last_date,
    }))
except Exception as exc:
    print(json.dumps({"error": str(exc)}))
    sys.exit(1)
"""


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "nan", "none", "null", "--", "-"):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value).strip().replace("/", "-")
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 13:
        try:
            ms = int(digits[:13])
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError):
            pass
    if len(digits) >= 10 and digits == text.replace(".", ""):
        try:
            sec = int(digits[:10])
            return datetime.fromtimestamp(sec, tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError):
            pass
    return text[:10] if text else None


def _format_quote_time_from_unix(ts: object) -> str | None:
    if ts in (None, "", "-"):
        return None
    try:
        return (
            datetime.fromtimestamp(int(ts), tz=timezone.utc)
            .astimezone(_BJ)
            .isoformat(timespec="seconds")
        )
    except (TypeError, ValueError, OSError):
        return str(ts)


def _scaled_em_price(raw: object) -> float | None:
    value = _to_float(raw)
    if value is None:
        return None
    # 东财 clist 全球指数：f2/f3 等为 ×100 定点数
    return round(value / 100, 4)


def parse_eastmoney_global_spot(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """解析 ``clist/get`` diff 行列表为内部指数报价（纯函数，便于 fixture 测试）。"""
    by_symbol: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("f12") or "").strip().upper()
        symbol = _EM_CODE_TO_SYMBOL.get(code)
        if symbol is None:
            continue
        last_price = _scaled_em_price(row.get("f2"))
        if last_price is None:
            continue
        change_percent = _scaled_em_price(row.get("f3"))
        if change_percent is None:
            prev_close = _scaled_em_price(row.get("f18"))
            if prev_close and prev_close > 0:
                change_percent = round((last_price / prev_close - 1) * 100, 2)
        quote_time = _format_quote_time_from_unix(row.get("f124"))
        by_symbol[symbol] = {
            "symbol": symbol,
            "display_name": _DISPLAY_NAMES[symbol],
            "last_price": last_price,
            "change_percent": change_percent,
            "quote_time": quote_time,
            "source": "eastmoney_global_spot_push2delay",
        }

    return [by_symbol[sym] for sym, _ in _SINA_SYMBOLS if sym in by_symbol]


def parse_us_index_spot_sina(payload: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    """将新浪 ``{symbol: records[]}`` 解析为指数现货报价列表。"""
    result: list[dict[str, object]] = []
    for symbol, _ in _SINA_SYMBOLS:
        records = payload.get(symbol)
        if not isinstance(records, list) or len(records) < 1:
            continue
        latest = records[-1]
        if not isinstance(latest, dict):
            continue
        last_price = _to_float(latest.get("close"))
        if last_price is None:
            continue
        change_percent: float | None = None
        if len(records) >= 2:
            prev = records[-2]
            if isinstance(prev, dict):
                prev_close = _to_float(prev.get("close"))
                if prev_close and prev_close > 0:
                    change_percent = round((last_price - prev_close) / prev_close * 100, 2)
        quote_time = _normalize_date(latest.get("date"))
        result.append(
            {
                "symbol": symbol,
                "display_name": _DISPLAY_NAMES[symbol],
                "last_price": last_price,
                "change_percent": change_percent,
                "quote_time": quote_time,
                "source": "index_us_stock_sina",
            }
        )
    return result


def parse_us_index_spot(payload: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    """兼容旧测试：等同 ``parse_us_index_spot_sina``。"""
    return parse_us_index_spot_sina(payload)


def _fetch_eastmoney_global_index_spot() -> list[dict[str, object]] | None:
    proxies = {"http": None, "https": None}
    session = requests.Session()
    session.headers.update(_HEADERS)
    last_error: Exception | None = None

    for url in _EM_CLIST_URLS:
        try:
            response = session.get(
                url,
                params=_EM_CLIST_PARAMS,
                timeout=_REQUEST_TIMEOUT,
                proxies=proxies,
            )
            response.raise_for_status()
            payload = response.json()
            diff = (payload.get("data") or {}).get("diff") or {}
            rows = [row for row in diff.values() if isinstance(row, dict)]
            parsed = parse_eastmoney_global_spot(rows)
            if parsed:
                source = "eastmoney_global_spot_push2delay"
                if "push2delay" not in url:
                    source = "eastmoney_global_spot"
                for row in parsed:
                    row["source"] = source
                return parsed
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.debug("eastmoney global index spot %s failed: %s", url, exc)

    if last_error:
        logger.warning("eastmoney global index spot exhausted: %s", last_error)
    return None


def _fetch_sina_index_spot_subprocess() -> list[dict[str, object]] | None:
    try:
        result = subprocess.run(
            [sys.executable, "-c", _SINA_SCRIPT],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("us index sina subprocess timeout")
        return None
    except OSError as exc:
        logger.warning("us index sina subprocess OSError: %s", exc)
        return None

    if not result.stdout.strip():
        return None

    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        logger.warning("us index sina JSON parse failed: %s", exc)
        return None

    if not isinstance(payload, dict) or payload.get("error"):
        return None

    parsed = parse_us_index_spot_sina(payload)
    return parsed if parsed else None


def _fetch_etf_proxy_quote(symbol: str) -> dict[str, object] | None:
    etf = _ETF_PROXIES.get(symbol)
    if not etf:
        return None
    try:
        result = subprocess.run(
            [sys.executable, "-c", _ETF_DAILY_SCRIPT, etf],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("us index etf proxy %s failed: %s", etf, exc)
        return None

    if not result.stdout.strip():
        return None
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    change_percent = _to_float(payload.get("change_percent"))
    last_price = _to_float(payload.get("last_price"))
    if change_percent is None or last_price is None:
        return None
    return {
        "symbol": symbol,
        "display_name": _DISPLAY_NAMES[symbol],
        "last_price": last_price,
        "change_percent": change_percent,
        "quote_time": payload.get("quote_time"),
        "source": f"etf_proxy_{etf.lower()}",
    }


def _merge_index_quotes(*groups: list[dict[str, object]] | None) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for group in groups:
        if not group:
            continue
        for row in group:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            if symbol and symbol not in merged:
                merged[symbol] = row
    return [merged[sym] for sym, _ in _SINA_SYMBOLS if sym in merged]


def fetch_us_index_spot() -> list[dict[str, object]] | None:
    """拉取美股三大指数最近收盘涨跌；失败返回 ``None``。

  主源东财 ``push2delay`` 全球指数现货；不足时新浪日 K；仍缺则用 ETF 代理。
    """
    eastmoney = _fetch_eastmoney_global_index_spot()
    if eastmoney and len(eastmoney) >= 3:
        return eastmoney

    sina = _fetch_sina_index_spot_subprocess()
    combined = _merge_index_quotes(eastmoney, sina)
    if len(combined) >= 3:
        return combined

    missing = [sym for sym, _ in _SINA_SYMBOLS if sym not in {r["symbol"] for r in combined}]
    for symbol in missing:
        proxy = _fetch_etf_proxy_quote(symbol)
        if proxy:
            combined.append(proxy)

    return combined if combined else None
