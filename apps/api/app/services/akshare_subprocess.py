"""在独立子进程调用 AkShare，避免 py_mini_racer 在主进程中 crash."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from functools import lru_cache

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 60
_FUND_RANK_ATTEMPTS = 3
_FUND_RANK_RETRY_DELAYS = (2.0, 5.0)
_FUND_RANK_SUBPROCESS_TIMEOUT = 35


def run_akshare_json_script(
    script: str,
    *,
    label: str,
    timeout: int | float = _SUBPROCESS_TIMEOUT,
) -> object | None:
    """Run an AkShare script in a child process and parse its JSON stdout."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.warning(
                "akshare subprocess failed for %s: %s",
                label,
                result.stderr[:300] if result.stderr else "no output",
            )
            return None
        payload = json.loads(result.stdout.strip())
        if isinstance(payload, dict) and payload.get("error"):
            logger.debug(
                "akshare subprocess returned error for %s: %s",
                label,
                payload.get("error"),
            )
            return None
        return payload
    except subprocess.TimeoutExpired:
        logger.warning("akshare subprocess timeout for %s", label)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("akshare subprocess exception for %s: %s", label, exc)
        return None


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
            encoding="utf-8",
            errors="replace",
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


def fetch_fund_daily_nav_returns(fund_codes: list[str], trade_date: str) -> dict | None:
    """一次性读取开放式基金最新净值表，返回指定基金在 trade_date 的日增长率/单位净值。"""
    codes = sorted({str(code).strip().zfill(6) for code in fund_codes if str(code).strip()})
    if not codes or not trade_date:
        return {"data": {}}
    codes_json = json.dumps(codes, ensure_ascii=True)
    script = f"""
import akshare as ak
import json

codes = set({codes_json})
trade_date = {trade_date!r}
try:
    frame = ak.fund_open_fund_daily_em()
    if frame is None or frame.empty:
        print(json.dumps({{"error": "empty"}}))
    else:
        unit_col = f"{{trade_date}}-单位净值"
        if unit_col not in frame.columns:
            print(json.dumps({{"data": {{}}, "date_mismatch": True}}, ensure_ascii=True))
        else:
            data = {{}}
            for _, row in frame.iterrows():
                code = str(row.get("基金代码", "")).strip().zfill(6)
                if code not in codes:
                    continue

                def _num(key):
                    raw = row.get(key)
                    if raw is None or str(raw).strip().lower() in ("", "nan", "--"):
                        return None
                    try:
                        return float(raw)
                    except (TypeError, ValueError):
                        return None

                daily_growth = _num("日增长率")
                unit_nav = _num(unit_col)
                data[code] = {{
                    "daily_growth": daily_growth,
                    "unit_nav": unit_nav,
                    "fund_name": str(row.get("基金简称", "")).strip(),
                }}
            print(json.dumps({{"data": data}}, ensure_ascii=True))
except Exception as e:
    print(json.dumps({{"error": str(e)}}, ensure_ascii=True))
"""
    return run_akshare_json_script(
        script,
        label=f"fund_daily_nav_returns:{trade_date}:{len(codes)}",
        timeout=_SUBPROCESS_TIMEOUT,
    )


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
            encoding="utf-8",
            errors="replace",
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
    """读取开放式基金近一年排行榜；限量、有界并重试瞬时失败。"""
    cap = max(50, min(limit, 500))
    script = f"""
from datetime import date
import json
import requests
from akshare.utils import demjson

end = date.today()
try:
    start = end.replace(year=end.year - 1)
except ValueError:
    start = end.replace(year=end.year - 1, day=28)

params = {{
    "op": "ph", "dt": "kf", "ft": "all", "rs": "", "gs": "0",
    "sc": "1nzf", "st": "desc", "sd": start.isoformat(),
    "ed": end.isoformat(), "qdii": "", "tabSubtype": ",,,,,",
    "pi": "1", "pn": "{cap}", "dx": "1", "v": "0.1591891419018292",
}}
headers = {{
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://fund.eastmoney.com/fundguzhi.html",
}}

def number(parts, index):
    if index >= len(parts) or parts[index] in ("", "--"):
        return None
    try:
        return float(parts[index])
    except (TypeError, ValueError):
        return None

try:
    response = requests.get(
        "https://fund.eastmoney.com/data/rankhandler.aspx",
        params=params,
        headers=headers,
        timeout=(5, 20),
    )
    response.raise_for_status()
    start_index = response.text.find("{{")
    end_index = response.text.rfind("}}")
    if start_index < 0 or end_index < start_index:
        raise ValueError("rank payload missing object")
    payload = demjson.decode(response.text[start_index : end_index + 1])
    rows = []
    for raw in (payload.get("datas") or [])[:{cap}]:
        parts = str(raw).split(",")
        code = parts[0].strip().zfill(6) if parts else ""
        if not code.isdigit() or len(code) != 6:
            continue
        rows.append({{
            "fund_code": code,
            "fund_name": parts[1].strip() if len(parts) > 1 else "",
            "return_1y_percent": number(parts, 11),
            "return_6m_percent": number(parts, 10),
            "return_3m_percent": number(parts, 9),
            "max_drawdown_1y_percent": None,
            "fund_scale_yi": None,
        }})
    if not rows:
        raise ValueError("empty rank rows")
    print(json.dumps({{"data": rows}}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({{"error": str(exc)}}, ensure_ascii=False))
"""
    for attempt in range(_FUND_RANK_ATTEMPTS):
        payload = run_akshare_json_script(
            script,
            label=f"fund_open_rank:{cap}:attempt-{attempt + 1}",
            timeout=_FUND_RANK_SUBPROCESS_TIMEOUT,
        )
        if isinstance(payload, dict):
            rows = payload.get("data")
            if isinstance(rows, list) and rows:
                return rows
        if attempt < len(_FUND_RANK_RETRY_DELAYS):
            time.sleep(_FUND_RANK_RETRY_DELAYS[attempt])
    logger.warning(
        "akshare fund rank unavailable after %s attempts",
        _FUND_RANK_ATTEMPTS,
    )
    return None


def fetch_open_fund_rank_worst_recent(*, limit: int = 150) -> list[dict] | None:
    """近1周跌幅靠前的开放式基金（雷达预筛；排行表默认 head 是涨幅冠军，不适用大跌扫描）。"""
    cap = max(80, min(limit, 300))
    script = f"""
import akshare as ak
import json
try:
    frame = ak.fund_open_fund_rank_em(symbol="全部")
    if frame is None or frame.empty:
        print(json.dumps({{"error": "empty"}}))
    else:
        def _num(raw):
            if raw is None or str(raw).strip().lower() in ("", "nan", "--"):
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        frame = frame.copy()
        frame["_w1"] = frame["近1周"].map(_num)
        frame = frame.sort_values("_w1", ascending=True, na_position="last")
        rows = []
        for _, row in frame.iterrows():
            code = str(row.get("基金代码", "")).strip().zfill(6)
            name = str(row.get("基金简称", "")).strip()
            if not code.isdigit() or len(code) != 6:
                continue
            scale = _num(row.get("基金规模"))
            if scale is not None and scale < 1.0:
                continue
            r1w = _num(row.get("近1周"))
            if r1w is None:
                continue
            rows.append({{
                "fund_code": code,
                "fund_name": name,
                "return_1w_percent": r1w,
                "return_1m_percent": _num("近1月"),
                "return_3m_percent": _num("近3月"),
                "return_6m_percent": _num("近6月"),
                "return_1y_percent": _num("近1年"),
                "max_drawdown_1y_percent": _num("最大回撤"),
                "fund_scale_yi": scale,
            }})
            if len(rows) >= {cap}:
                break
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
            logger.warning("akshare fund worst-rank subprocess failed: %s", result.stderr)
            return None
        output = json.loads(result.stdout.strip())
        if output.get("error"):
            return None
        return output.get("data") or []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.warning("akshare fund worst-rank exception: %s", exc)
        return None


def fetch_new_fund_offerings(*, limit: int = 300) -> list[dict] | None:
    """新发/成立不久基金列表（东财 fund_new_found_em），子进程拉取。"""
    cap = max(50, min(limit, 800))
    script = f"""
import akshare as ak
import json
try:
    frame = ak.fund_new_found_em()
    if frame is None or frame.empty:
        print(json.dumps({{"error": "empty"}}))
    else:
        rows = []
        for _, row in frame.head({cap}).iterrows():
            code = str(row.get("基金代码", "")).strip().zfill(6)
            name = str(row.get("基金简称", "")).strip()
            if not code.isdigit() or len(code) != 6:
                continue
            established = row.get("成立日期")
            if established is not None:
                established = str(established)[:10]
            status = str(row.get("申购状态", "")).strip()
            if status and "开放" not in status and "申购" not in status:
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
                "established_date": established,
                "return_since_issue_percent": _num("成立来涨幅"),
                "fund_company": str(row.get("基金公司", "")).strip() or None,
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
            logger.warning("akshare new fund subprocess failed: %s", result.stderr)
            return None
        output = json.loads(result.stdout.strip())
        if output.get("error"):
            return None
        return output.get("data") or []
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.warning("akshare new fund exception: %s", exc)
        return None


def fetch_board_daily_kline_series(
    source_type: str,
    source_name: str,
    *,
    source_code: str | None = None,
    max_days: int = 400,
) -> list[dict[str, str | float | None]] | None:
    """东财日 K 直连不可达时，用 AkShare 板块日 K 作回测兜底（子进程隔离）。"""
    board_type = (source_type or "").strip().lower()
    if board_type not in {"concept", "industry"}:
        return None

    symbol = (source_code or source_name or "").strip()
    if not symbol:
        return None

    days = max(30, min(max_days, 800))
    fn_name = (
        "stock_board_concept_hist_em"
        if board_type == "concept"
        else "stock_board_industry_hist_em"
    )
    script = f"""
import akshare as ak
import json
from datetime import date, timedelta

def _num(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "nan", "none"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

symbol = {symbol!r}
days = {days}
end = date.today().strftime("%Y%m%d")
beg = (date.today() - timedelta(days=days + 90)).strftime("%Y%m%d")
try:
    frame = ak.{fn_name}(
        symbol=symbol,
        period="daily",
        start_date=beg,
        end_date=end,
        adjust="",
    )
    if frame is None or frame.empty:
        print(json.dumps({{"error": "empty"}}))
    else:
        rows = []
        for _, row in frame.iterrows():
            rows.append({{
                "date": str(row.get("日期", ""))[:10],
                "open": _num(row.get("开盘")),
                "close": _num(row.get("收盘")),
                "high": _num(row.get("最高")),
                "low": _num(row.get("最低")),
                "volume": _num(row.get("成交量")),
                "amount": _num(row.get("成交额")),
                "change_percent": _num(row.get("涨跌幅")),
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
            encoding="utf-8",
            errors="replace",
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.debug(
                "akshare board daily subprocess failed for %s: %s",
                symbol,
                result.stderr[:200] if result.stderr else "no output",
            )
            return None
        output = json.loads(result.stdout.strip())
        if output.get("error"):
            logger.debug(
                "akshare board daily returned error for %s: %s",
                symbol,
                output["error"],
            )
            return None
        return _akshare_board_rows_to_daily_bars(output.get("data") or [], max_days=days)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.warning("akshare board daily exception for %s: %s", symbol, exc)
        return None


def _akshare_board_rows_to_daily_bars(
    rows: list[dict[str, object]],
    *,
    max_days: int,
) -> list[dict[str, str | float | None]]:
    bars: list[dict[str, str | float | None]] = []
    prior_close: float | None = None
    for row in rows:
        day = str(row.get("date") or "")[:10]
        close = _as_board_float(row.get("close"))
        high = _as_board_float(row.get("high"))
        volume = _as_board_float(row.get("volume"))
        amount = _as_board_float(row.get("amount"))
        change_pct = _as_board_float(row.get("change_percent"))
        if not day or close is None or close <= 0:
            continue

        if change_pct is not None:
            change = round(change_pct, 4)
        elif prior_close and prior_close > 0:
            change = round((close / prior_close - 1) * 100, 4)
        else:
            prior_close = close
            continue

        high_change = (
            round((high / prior_close - 1) * 100, 4)
            if high is not None and prior_close and prior_close > 0
            else None
        )
        bars.append(
            {
                "date": day,
                "change_percent": change,
                "high_change_percent": high_change,
                "close": close,
                "volume": volume,
                "amount": amount,
            }
        )
        prior_close = close

    if len(bars) > max_days:
        bars = bars[-max_days:]
    return bars


def _as_board_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
