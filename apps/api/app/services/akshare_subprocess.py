"""在独立子进程调用 AkShare，避免 py_mini_racer 在主进程中 crash."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from functools import lru_cache

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 60


@lru_cache(maxsize=128)
def fetch_fund_nav_history(fund_code: str, trading_days: int = 90) -> dict | None:
    """在子进程中获取基金净值走势，避免 py_mini_racer crash 主进程."""
    script = f"""
import akshare as ak
import json
try:
    frame = ak.fund_open_fund_info_em(symbol="{fund_code}", indicator="单位净值走势")
    if frame is None or frame.empty:
        print(json.dumps({{"error": "empty"}}))
    else:
        # 保留最后 {trading_days} 条记录
        if len(frame) > {trading_days}:
            frame = frame.iloc[-{trading_days}:]
        data = []
        for _, row in frame.iterrows():
            growth_raw = row.get("日增长率")
            daily_growth = None
            if growth_raw is not None and str(growth_raw).strip().lower() not in ("", "nan"):
                try:
                    daily_growth = float(growth_raw)
                except (TypeError, ValueError):
                    daily_growth = None
            nav_raw = row.get("单位净值")
            nav_value = None
            if nav_raw is not None and str(nav_raw).strip().lower() not in ("", "nan"):
                try:
                    nav_value = float(nav_raw)
                except (TypeError, ValueError):
                    nav_value = None
            data.append({{
                "date": str(row.get("净值日期", "")),
                "nav": nav_value,
                "daily_growth": daily_growth,
            }})
        print(json.dumps({{"data": data}}))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning(f"akshare subprocess failed for {{fund_code}}: stderr={{result.stderr}}")
            return None

        output = json.loads(result.stdout.strip())
        if "error" in output:
            logger.debug(f"akshare returned error for {{fund_code}}: {{output['error']}}")
            return None

        return output
    except subprocess.TimeoutExpired:
        logger.warning(f"akshare subprocess timeout for {{fund_code}}")
        return None
    except Exception as e:
        logger.error(f"akshare subprocess exception for {{fund_code}}: {{e}}")
        return None


def _index_market_symbol(index_symbol: str) -> str:
    code = index_symbol.strip()
    if code.startswith(("sh", "sz")):
        return code
    if code.startswith(("39", "98")):
        return f"sz{code}"
    return f"sh{code}"


@lru_cache(maxsize=64)
def fetch_index_daily_history(index_symbol: str, trading_days: int = 252) -> dict | None:
    """在子进程中获取指数日线，用于业绩走势对比基准。"""
    market_symbol = _index_market_symbol(index_symbol)
    calendar_days = max(45, int(trading_days * 1.8))
    script = f"""
import akshare as ak
import json
from datetime import date, timedelta

symbol = "{index_symbol}"
market_symbol = "{market_symbol}"
trading_days = {trading_days}
calendar_days = {calendar_days}
end = date.today()
start = end - timedelta(days=calendar_days)
start_str = start.strftime("%Y%m%d")
end_str = end.strftime("%Y%m%d")

def parse_frame(frame):
    if frame is None or frame.empty:
        return []
    rows = []
    for _, row in frame.iterrows():
        date_value = row.get("日期") or row.get("date")
        close_value = row.get("收盘") or row.get("close")
        if date_value is None or close_value is None:
            continue
        text = str(date_value).replace("/", "-")
        rows.append({{
            "date": text[:10],
            "close": float(close_value),
        }})
    rows.sort(key=lambda item: item["date"])
    if len(rows) > trading_days:
        rows = rows[-trading_days:]
    return rows

try:
    data = []
    try:
        frame = ak.index_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_str,
            end_date=end_str,
        )
        data = parse_frame(frame)
    except Exception:
        data = []

    if not data:
        frame = ak.stock_zh_index_daily_em(symbol=market_symbol)
        data = parse_frame(frame)

    if not data:
        print(json.dumps({{"error": "empty"}}))
    else:
        print(json.dumps({{"data": data}}))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning(
                "akshare index subprocess failed for %s: stderr=%s",
                index_symbol,
                result.stderr,
            )
            return None

        output = json.loads(result.stdout.strip())
        if "error" in output:
            logger.debug("akshare index returned error for %s: %s", index_symbol, output["error"])
            return None
        return output
    except subprocess.TimeoutExpired:
        logger.warning("akshare index subprocess timeout for %s", index_symbol)
        return None
    except Exception as exc:
        logger.error("akshare index subprocess exception for %s: %s", index_symbol, exc)
        return None


def fetch_open_fund_rank(*, limit: int = 300) -> list[dict] | None:
    """开放式基金排行（近1年等），子进程拉取避免主进程 crash。"""
    cap = max(50, min(limit, 500))
    script = f"""
import akshare as ak
import json
try:
    frame = ak.fund_open_fund_rank_em(symbol="全部")
    if frame is None or frame.empty:
        print(json.dumps({{"error": "empty"}}))
    else:
        rows = []
        for _, row in frame.head({cap}).iterrows():
            code = str(row.get("基金代码", "")).strip().zfill(6)
            name = str(row.get("基金简称", "")).strip()
            if not code.isdigit() or len(code) != 6:
                continue
            def _num(key):
                raw = row.get(key)
                if raw is None or str(raw).strip().lower() in ("", "nan", "--"):
                    return None
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    return None
            rows.append({{
                "fund_code": code,
                "fund_name": name,
                "return_1y_percent": _num("近1年"),
                "return_6m_percent": _num("近6月"),
                "return_3m_percent": _num("近3月"),
                "max_drawdown_1y_percent": _num("最大回撤"),
                "fund_scale_yi": _num("基金规模"),
            }})
        print(json.dumps({{"data": rows}}))
except Exception as e:
    print(json.dumps({{"error": str(e)}}))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning("akshare fund rank subprocess failed: %s", result.stderr)
            return None
        output = json.loads(result.stdout.strip())
        if output.get("error"):
            return None
        return output.get("data") or []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.warning("akshare fund rank exception: %s", exc)
        return None
