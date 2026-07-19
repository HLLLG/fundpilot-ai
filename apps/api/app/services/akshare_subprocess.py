"""在独立子进程调用 AkShare，避免 py_mini_racer 在主进程中 crash."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from typing import Any

from app.services.decision_quality_provider_receipts import (
    DecisionQualityProviderRead,
    build_provider_origin_receipt,
    build_provider_read,
)

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 60
_FUND_RANK_ATTEMPTS = 3
_FUND_RANK_RETRY_DELAYS = (2.0, 5.0)
_FUND_RANK_SUBPROCESS_TIMEOUT = 35
_FUND_UNIVERSE_SUBPROCESS_TIMEOUT = 150

_FUND_NAV_PROVIDER_ID = "akshare.fund_open_fund_info_em"
_FUND_NAV_OPERATION = "fund_open_fund_info_em"
_FUND_NAV_INDICATOR = "单位净值走势"
_FUND_NAV_ADAPTER_CONTRACT_VERSION_V1 = "decision_quality_fund_nav_adapter.v1"
_FUND_NAV_ADAPTER_CONTRACT_VERSION = _FUND_NAV_ADAPTER_CONTRACT_VERSION_V1
_FUND_NAV_CACHE_POLICY_V1 = "utc_hour_lru_512.v1"
_FUND_NAV_CACHE_POLICY = _FUND_NAV_CACHE_POLICY_V1
_FUND_NAV_UPSTREAM_RAW_REASON = (
    "akshare exposes a dataframe; this receipt captures adapter stdout, not "
    "the upstream HTTP response"
)
_FUND_NAV_QUALITY_CACHE_MAXSIZE = 512
_FUND_NAV_QUALITY_CACHE: OrderedDict[
    tuple[str, int, str, str, int], tuple[dict[str, Any], object]
] = OrderedDict()
_FUND_NAV_QUALITY_CACHE_LOCK = threading.RLock()


def _utf8_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_akshare_json_script(
    script: str,
    *,
    label: str,
    timeout: int | float = _SUBPROCESS_TIMEOUT,
    warn_on_failure: bool = True,
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
            env=_utf8_subprocess_env(),
        )
        stdout = result.stdout or ""
        if not stdout.strip():
            log = logger.warning if warn_on_failure else logger.debug
            log(
                "akshare subprocess failed for %s: %s",
                label,
                result.stderr[:300] if result.stderr else "no output",
            )
            return None

        payload = _parse_json_stdout(stdout)
        if payload is None:
            log = logger.warning if warn_on_failure else logger.debug
            log(
                "akshare subprocess returned invalid JSON for %s: %r",
                label,
                stdout[-300:],
            )
            return None
        if isinstance(payload, dict) and payload.get("error"):
            log = (
                logger.warning
                if warn_on_failure and payload.get("stage")
                else logger.debug
            )
            log(
                "akshare subprocess returned error for %s: %s",
                label,
                payload.get("error"),
            )
            return None
        if result.returncode != 0:
            log = logger.warning if warn_on_failure else logger.debug
            log("akshare subprocess exited rc=%s for %s", result.returncode, label)
            return None
        return payload
    except subprocess.TimeoutExpired:
        logger.warning("akshare subprocess timeout for %s", label)
        return None
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("akshare subprocess exception for %s: %s", label, exc)
        return None


def _parse_json_stdout(stdout: str) -> object | None:
    """Return the last valid JSON value, ignoring optional provider diagnostics."""
    text = (stdout or "").strip()
    if not text:
        return None
    for candidate in (text, *reversed(text.splitlines())):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


@lru_cache(maxsize=512)
def _fetch_fund_nav_history_cached(
    fund_code: str,
    trading_days: int,
    _cache_hour: int,
) -> dict | None:
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _akshare_version() -> str:
    try:
        return package_version("akshare")
    except PackageNotFoundError:
        return "unavailable"


def _fund_nav_history_script_v1(
    fund_code: str,
    trading_days: int,
    indicator: str,
) -> str:
    code_literal = json.dumps(fund_code, ensure_ascii=True)
    indicator_literal = json.dumps(indicator, ensure_ascii=False)
    return f"""
import akshare as ak
import json
try:
    frame = ak.fund_open_fund_info_em(symbol={code_literal}, indicator={indicator_literal})
    if frame is None or frame.empty:
        print(json.dumps({{"error": "empty"}}))
    else:
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
        print(json.dumps({{"data": data}}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({{"error": str(exc)}}, ensure_ascii=False))
"""


def _fund_nav_history_script(
    fund_code: str,
    trading_days: int,
    indicator: str,
) -> str:
    """Build the current production adapter script.

    Historical contract builders remain separately named so a future contract
    bump cannot invalidate already-frozen origin receipts.
    """

    return _fund_nav_history_script_v1(fund_code, trading_days, indicator)


def _fund_nav_cache_key_material(
    *,
    fund_code: str,
    trading_days: int,
    indicator: str,
    cache_hour: int,
) -> dict[str, object]:
    return {
        "provider_id": _FUND_NAV_PROVIDER_ID,
        "operation": _FUND_NAV_OPERATION,
        "parameters": {
            "fund_code": fund_code,
            "trading_days": trading_days,
            "indicator": indicator,
        },
        "adapter_contract_version": _FUND_NAV_ADAPTER_CONTRACT_VERSION,
        "cache_hour": cache_hour,
    }


def _fund_nav_cache_key_material_v1(
    *,
    fund_code: str,
    trading_days: int,
    indicator: str,
    cache_hour: int,
) -> dict[str, object]:
    return {
        "provider_id": _FUND_NAV_PROVIDER_ID,
        "operation": _FUND_NAV_OPERATION,
        "parameters": {
            "fund_code": fund_code,
            "trading_days": trading_days,
            "indicator": indicator,
        },
        "adapter_contract_version": _FUND_NAV_ADAPTER_CONTRACT_VERSION_V1,
        "cache_hour": cache_hour,
    }


def fund_nav_quality_adapter_policy_material(
    *,
    fund_code: str,
    trading_days: int,
    cache_hour: int,
    contract_version: str | None = None,
) -> dict[str, object]:
    """Expose the exact request-specific production NAV adapter inputs.

    This is intentionally a builder: the production script embeds the frozen
    fund identity and requested lookback, so a verifier must reconstruct that
    exact script rather than trust a self-declared script hash.
    """

    requested = contract_version or _FUND_NAV_ADAPTER_CONTRACT_VERSION
    if requested != _FUND_NAV_ADAPTER_CONTRACT_VERSION_V1:
        raise ValueError("unknown fund-NAV quality adapter contract")
    policy_fund_code = "__candidate_fund_code__"
    policy_trading_days = 2_147_483_647
    policy_cache_hour = 2_147_483_647
    return {
        "provider_id": _FUND_NAV_PROVIDER_ID,
        "operation": _FUND_NAV_OPERATION,
        "request_parameters": {
            "fund_code": fund_code,
            "trading_days": trading_days,
            "indicator": _FUND_NAV_INDICATOR,
        },
        "adapter_contract_version": _FUND_NAV_ADAPTER_CONTRACT_VERSION_V1,
        "adapter_script": _fund_nav_history_script_v1(
            fund_code,
            trading_days,
            _FUND_NAV_INDICATOR,
        ),
        "adapter_policy_script": _fund_nav_history_script_v1(
            policy_fund_code,
            policy_trading_days,
            _FUND_NAV_INDICATOR,
        ),
        "cache_policy": _FUND_NAV_CACHE_POLICY_V1,
        "cache_key_material": _fund_nav_cache_key_material_v1(
            fund_code=fund_code,
            trading_days=trading_days,
            indicator=_FUND_NAV_INDICATOR,
            cache_hour=cache_hour,
        ),
        "cache_key_policy_material": _fund_nav_cache_key_material_v1(
            fund_code=policy_fund_code,
            trading_days=policy_trading_days,
            indicator=_FUND_NAV_INDICATOR,
            cache_hour=policy_cache_hour,
        ),
        "library_name": "akshare",
    }


def _normalize_fund_nav_payload(parsed: object) -> dict[str, object] | None:
    if not isinstance(parsed, dict):
        return None
    rows = parsed.get("data")
    if not isinstance(rows, list):
        return None
    if any(not isinstance(row, dict) for row in rows):
        return None
    return {"data": deepcopy(rows)}


def _timeout_stdout_bytes(exc: subprocess.TimeoutExpired) -> bytes:
    value = exc.stdout or b""
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def _build_fund_nav_origin_read(
    *,
    fund_code: str,
    trading_days: int,
    indicator: str,
    script: str,
    started_at: str,
    completed_at: str,
    stdout: bytes,
    parsed_payload: object,
    normalized_payload: object,
    status: str,
    cache_hour: int,
) -> DecisionQualityProviderRead:
    parameters = {
        "fund_code": fund_code,
        "trading_days": trading_days,
        "indicator": indicator,
    }
    receipt = build_provider_origin_receipt(
        provider_id=_FUND_NAV_PROVIDER_ID,
        operation=_FUND_NAV_OPERATION,
        request_parameters=parameters,
        request_started_at=started_at,
        response_completed_at=completed_at,
        response_status=status,
        adapter_contract_version=_FUND_NAV_ADAPTER_CONTRACT_VERSION,
        adapter_script=script,
        library_name="akshare",
        library_version=_akshare_version(),
        python_version=platform.python_version(),
        cache_policy=_FUND_NAV_CACHE_POLICY,
        cache_key_material=_fund_nav_cache_key_material(
            fund_code=fund_code,
            trading_days=trading_days,
            indicator=indicator,
            cache_hour=cache_hour,
        ),
        stdout_bytes=stdout,
        parsed_payload=parsed_payload,
        normalized_payload=normalized_payload,
        upstream_raw_unavailable_reason=_FUND_NAV_UPSTREAM_RAW_REASON,
    )
    return build_provider_read(
        origin_receipt=receipt,
        normalized_payload=normalized_payload,
        cache_status="miss",
        cache_layer="live",
        served_at=completed_at,
    )


def _capture_fund_nav_quality_origin(
    fund_code: str,
    *,
    trading_days: int,
    indicator: str,
    cache_hour: int,
    request_started_at: str | None = None,
) -> DecisionQualityProviderRead:
    script = _fund_nav_history_script(fund_code, trading_days, indicator)
    started_at = request_started_at or _utc_now()
    stdout = b""
    parsed: object = None
    normalized: object = None
    status = "exception"
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=False,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
            env=_utf8_subprocess_env(),
        )
        stdout = bytes(result.stdout or b"")
        try:
            decoded = stdout.decode("utf-8")
        except UnicodeDecodeError:
            decoded = ""
        parsed = _parse_json_stdout(decoded)
        normalized = _normalize_fund_nav_payload(parsed)
        if result.returncode != 0:
            status = "subprocess_error"
            normalized = None
        elif not stdout.strip():
            status = "empty"
            normalized = None
        elif parsed is None:
            status = "invalid_json"
            normalized = None
        elif isinstance(parsed, dict) and parsed.get("error"):
            status = "provider_error"
            normalized = None
        elif normalized is None:
            status = "invalid_payload"
        else:
            status = "success"
    except subprocess.TimeoutExpired as exc:
        stdout = _timeout_stdout_bytes(exc)
        try:
            parsed = _parse_json_stdout(stdout.decode("utf-8"))
        except UnicodeDecodeError:
            parsed = None
        normalized = None
        status = "timeout"
    except Exception:
        stdout = b""
        parsed = None
        normalized = None
        status = "exception"
    completed_at = _utc_now()
    return _build_fund_nav_origin_read(
        fund_code=fund_code,
        trading_days=trading_days,
        indicator=indicator,
        script=script,
        started_at=started_at,
        completed_at=completed_at,
        stdout=stdout,
        parsed_payload=parsed,
        normalized_payload=normalized,
        status=status,
        cache_hour=cache_hour,
    )


def _fund_nav_quality_cache_key(
    *,
    fund_code: str,
    trading_days: int,
    indicator: str,
    cache_hour: int,
) -> tuple[str, int, str, str, int]:
    return (
        fund_code,
        trading_days,
        indicator,
        _FUND_NAV_ADAPTER_CONTRACT_VERSION,
        cache_hour,
    )


def clear_fund_nav_quality_cache() -> None:
    with _FUND_NAV_QUALITY_CACHE_LOCK:
        _FUND_NAV_QUALITY_CACHE.clear()


def _fetch_fund_nav_history_quality_read_for_hour(
    fund_code: str,
    *,
    trading_days: int,
    indicator: str,
    cache_hour: int,
    request_started_at: str | None = None,
) -> DecisionQualityProviderRead:
    key = _fund_nav_quality_cache_key(
        fund_code=fund_code,
        trading_days=trading_days,
        indicator=indicator,
        cache_hour=cache_hour,
    )
    served_at = request_started_at or _utc_now()
    with _FUND_NAV_QUALITY_CACHE_LOCK:
        cached = _FUND_NAV_QUALITY_CACHE.pop(key, None)
        if cached is not None:
            origin, normalized = cached
            _FUND_NAV_QUALITY_CACHE[key] = (origin, normalized)
            return build_provider_read(
                origin_receipt=origin,
                normalized_payload=normalized,
                cache_status="hit",
                cache_layer="process",
                served_at=served_at,
            )

    capture_kwargs: dict[str, Any] = {}
    if request_started_at is not None:
        capture_kwargs["request_started_at"] = request_started_at
    captured = _capture_fund_nav_quality_origin(
        fund_code,
        trading_days=trading_days,
        indicator=indicator,
        cache_hour=cache_hour,
        **capture_kwargs,
    )
    with _FUND_NAV_QUALITY_CACHE_LOCK:
        _FUND_NAV_QUALITY_CACHE[key] = (
            deepcopy(captured.origin_receipt),
            deepcopy(captured.normalized_payload),
        )
        while len(_FUND_NAV_QUALITY_CACHE) > _FUND_NAV_QUALITY_CACHE_MAXSIZE:
            _FUND_NAV_QUALITY_CACHE.popitem(last=False)
    return captured


def fetch_fund_nav_history_quality_read(
    fund_code: str,
    trading_days: int = 90,
    *,
    indicator: str = _FUND_NAV_INDICATOR,
) -> DecisionQualityProviderRead:
    """Fetch NAV history with a frozen origin receipt and delivery metadata."""

    normalized_code = str(fund_code).strip()
    safe_days = max(1, int(trading_days))
    normalized_indicator = str(indicator).strip()
    if not normalized_code:
        raise ValueError("fund_code is required")
    if not normalized_indicator:
        raise ValueError("indicator is required")
    request_started_at = _utc_now()
    cache_hour = int(
        datetime.fromisoformat(request_started_at).astimezone(timezone.utc).timestamp()
        // 3600
    )
    return _fetch_fund_nav_history_quality_read_for_hour(
        normalized_code,
        trading_days=safe_days,
        indicator=normalized_indicator,
        cache_hour=cache_hour,
        request_started_at=request_started_at,
    )


def _fund_nav_payload_from_quality_read(
    fund_code: str,
    trading_days: int,
    _cache_hour: int,
) -> dict | None:
    """Compatibility helper retaining the historical explicit hour argument."""

    read = _fetch_fund_nav_history_quality_read_for_hour(
        str(fund_code).strip(),
        trading_days=max(1, int(trading_days)),
        indicator=_FUND_NAV_INDICATOR,
        cache_hour=int(_cache_hour),
    )
    return (
        deepcopy(read.normalized_payload)
        if read.ok and isinstance(read.normalized_payload, dict)
        else None
    )


def fetch_fund_nav_history(fund_code: str, trading_days: int = 90) -> dict | None:
    """Fetch NAV rows with the historical payload-only hourly cache."""

    return _fetch_fund_nav_history_cached(
        fund_code,
        max(1, int(trading_days)),
        int(time.time() // 3600),
    )


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


@lru_cache(maxsize=256)
def _fetch_index_daily_history_cached(
    index_symbol: str,
    trading_days: int,
    _cache_hour: int,
) -> dict | None:
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


def fetch_index_daily_history(index_symbol: str, trading_days: int = 252) -> dict | None:
    """Fetch index closes with an hourly cache key for outcome materialization."""

    return _fetch_index_daily_history_cached(
        index_symbol,
        max(1, int(trading_days)),
        int(time.time() // 3600),
    )


@lru_cache(maxsize=64)
def _fetch_hk_index_daily_history_cached(
    index_symbol: str,
    trading_days: int,
    _cache_hour: int,
) -> dict | None:
    script = f"""
import akshare as ak
import json

symbol = {index_symbol!r}
trading_days = {trading_days}
try:
    frame = ak.stock_hk_index_daily_sina(symbol=symbol)
    rows = []
    if frame is not None and not frame.empty:
        for _, row in frame.tail(trading_days).iterrows():
            day = str(row.get("date") or "")[:10]
            close = row.get("close")
            if day and close is not None:
                rows.append({{"date": day, "close": float(close)}})
    print(json.dumps({{"data": rows}}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({{"error": str(exc)}}, ensure_ascii=False))
"""
    payload = run_akshare_json_script(
        script,
        label=f"hk_index_daily:{index_symbol}",
        timeout=25,
    )
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    return {"data": rows, "source": "sina_hk_index_daily"}


def fetch_hk_index_daily_history(
    index_symbol: str,
    trading_days: int = 252,
) -> dict | None:
    """Fetch Hong Kong index closes without importing AkShare in the API process."""

    return _fetch_hk_index_daily_history_cached(
        str(index_symbol).strip().upper(),
        max(20, min(int(trading_days), 800)),
        int(time.time() // 3600),
    )


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


def fetch_open_fund_universe(
    *,
    limit: int = 20_000,
    timeout_seconds: int | float | None = None,
) -> list[dict] | None:
    """Fetch the full fund catalogue and optionally enrich it with rank data.

    The static catalogue is the availability boundary: a transient ranking
    failure must not discard 25,000 otherwise usable code/name/type rows.
    Ranking is fetched in one bounded request and only adds current NAV and
    trailing-return fields when it succeeds.
    """
    cap = max(300, min(int(limit), 25_000))
    script = f"""
from datetime import date
import json
import math
import requests
import time
from akshare.utils import demjson

cap = {cap}
end = date.today()
try:
    start = end.replace(year=end.year - 1)
except ValueError:
    start = end.replace(year=end.year - 1, day=28)
headers = {{
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://fund.eastmoney.com/fundguzhi.html",
}}

def request_with_retry(url, *, params=None, timeout=(5, 25), attempts=3):
    last_error = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1 + attempt * 2)
    raise last_error

def normalize_fund_type(value):
    raw = str(value or "").strip()
    upper = raw.upper()
    if "QDII" in upper:
        return "qdii"
    if "FOF" in upper:
        return "fof"
    if raw.startswith("\u6307\u6570"):
        return "zs"
    if raw.startswith("\u80a1\u7968"):
        return "gp"
    if raw.startswith("\u6df7\u5408"):
        return "hh"
    if raw.startswith("\u503a\u5238"):
        return "zq"
    return None

def number(parts, index):
    if index >= len(parts) or parts[index] in ("", "--"):
        return None
    try:
        return float(parts[index])
    except (TypeError, ValueError):
        return None

def observed_number(parts, index, field):
    if index >= len(parts) or parts[index].strip() in ("", "--"):
        return None
    try:
        value = float(parts[index])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{{field}} is not numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"{{field}} is not finite")
    return value

def text(parts, index):
    if index >= len(parts):
        return None
    value = parts[index].strip()
    return None if value in ("", "--") else value

def fetch_catalogue():
    response = request_with_retry(
        "https://fund.eastmoney.com/js/fundcode_search.js",
        timeout=(5, 30),
        attempts=3,
    )
    text_value = response.content.decode("utf-8-sig")
    start_index = text_value.find("[[")
    end_index = text_value.rfind("]]")
    if start_index < 0 or end_index < start_index:
        raise ValueError("catalogue payload missing array")
    raw_rows = json.loads(text_value[start_index : end_index + 2])
    rows = []
    seen = set()
    for parts in raw_rows:
        if not isinstance(parts, list) or len(parts) < 4:
            continue
        code = str(parts[0] or "").strip().zfill(6)
        name = str(parts[2] or "").strip()
        source_type = str(parts[3] or "").strip()
        fund_type = normalize_fund_type(source_type)
        if (
            not code.isdigit()
            or len(code) != 6
            or not name
            or fund_type is None
            or code in seen
        ):
            continue
        seen.add(code)
        rows.append({{
            "fund_code": code,
            "fund_name": name,
            "fund_type": fund_type,
            "source_fund_type": source_type,
            "rank_enriched": False,
            "nav_date": None,
            "latest_nav": None,
            "daily_growth_percent": None,
            "established_date": None,
            "return_1y_percent": None,
            "return_6m_percent": None,
            "return_3m_percent": None,
            "max_drawdown_1y_percent": None,
            "fund_scale_yi": None,
        }})
    minimum_rows = 20_000 if cap >= 20_000 else cap
    if len(rows) < minimum_rows:
        raise ValueError(
            f"catalogue has {{len(rows)}} eligible rows; expected at least {{minimum_rows}}"
        )
    return rows

def fetch_rank_enrichment():
    params = {{
        "op": "ph", "dt": "kf", "ft": "all", "rs": "", "gs": "0",
        "sc": "1nzf", "st": "desc", "sd": start.isoformat(),
        "ed": end.isoformat(), "qdii": "", "tabSubtype": ",,,,,",
        "pi": "1", "pn": "25000", "dx": "1", "v": "0.1591891419018292",
    }}
    response = request_with_retry(
        "https://fund.eastmoney.com/data/rankhandler.aspx",
        params=params,
        timeout=(5, 25),
        attempts=2,
    )
    start_index = response.text.find("{{")
    end_index = response.text.rfind("}}")
    if start_index < 0 or end_index < start_index:
        raise ValueError("rank payload missing object")
    return demjson.decode(response.text[start_index : end_index + 1])

def parse_rank_rows(payload):
    raw_rows = payload.get("datas") or []
    total_records = int(payload.get("allRecords") or 0)
    all_pages = int(payload.get("allPages") or 1)
    if total_records <= 0 or len(raw_rows) != total_records or all_pages != 1:
        raise ValueError(
            f"rank payload incomplete: rows={{len(raw_rows)}} "
            f"total={{total_records}} pages={{all_pages}}"
        )
    rows = {{}}
    for raw in raw_rows:
        parts = str(raw).split(",")
        code = parts[0].strip().zfill(6) if parts else ""
        if not code.isdigit() or len(code) != 6:
            continue
        latest_nav = observed_number(parts, 4, "latest_nav")
        if latest_nav is not None and latest_nav <= 0:
            raise ValueError("latest_nav must be positive")
        rows[code] = {{
            "rank_enriched": True,
            "nav_date": text(parts, 3),
            "latest_nav": latest_nav,
            "daily_growth_percent": observed_number(parts, 6, "daily_growth_percent"),
            "established_date": parts[16].strip() if len(parts) > 16 else None,
            "return_1y_percent": number(parts, 11),
            "return_6m_percent": number(parts, 10),
            "return_3m_percent": number(parts, 9),
        }}
    return rows

try:
    catalogue = fetch_catalogue()
    rank_error = None
    rank_payload = None
    rank_rows = {{}}
    try:
        rank_payload = fetch_rank_enrichment()
        rank_rows = parse_rank_rows(rank_payload)
    except Exception as exc:
        rank_error = f"{{type(exc).__name__}}: {{exc}}"
    selected = catalogue[:cap]
    enriched_count = 0
    for row in selected:
        enrichment = rank_rows.get(row["fund_code"])
        if enrichment is None:
            continue
        row.update(enrichment)
        enriched_count += 1
    print(json.dumps({{
        "data": selected,
        "metadata": {{
            "catalogue_source": "eastmoney.fundcode_search",
            "catalogue_eligible_rows": len(catalogue),
            "selected_rows": len(selected),
            "rank_source": "eastmoney.open_fund_rankhandler",
            "rank_total_records": (
                int(rank_payload.get("allRecords") or 0)
                if isinstance(rank_payload, dict)
                else 0
            ),
            "rank_enriched_rows": enriched_count,
            "rank_error": rank_error,
        }},
    }}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({{
        "error": f"catalogue fetch failed: {{type(exc).__name__}}: {{exc}}",
        "stage": "catalogue",
    }}, ensure_ascii=False))
"""
    payload = run_akshare_json_script(
        script,
        label=f"fund_open_universe:{cap}",
        timeout=timeout_seconds or _FUND_UNIVERSE_SUBPROCESS_TIMEOUT,
    )
    if isinstance(payload, dict):
        rows = payload.get("data")
        if isinstance(rows, list) and rows:
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                rank_error = metadata.get("rank_error")
                if rank_error:
                    logger.warning(
                        "fund catalogue fetched (%s rows) without optional rank enrichment: %s",
                        len(rows),
                        rank_error,
                    )
                else:
                    logger.info(
                        "fund catalogue fetched: rows=%s rank_enriched=%s",
                        len(rows),
                        metadata.get("rank_enriched_rows"),
                    )
            return rows
    return None


def fetch_open_fund_research_profiles(
    fund_codes: list[str],
    *,
    timeout_seconds: int | float = 45,
) -> list[dict] | None:
    """读取候选基金的规模、经理和成立日期，供荐基准入守卫使用。

    Sina 的开放式基金规模接口按大类返回全表。这里在子进程内只序列化目标代码，
    并在目标全部命中后停止继续拉取，避免把数万行明细传回 API 进程。
    """

    targets = sorted(
        {
            str(code).strip().zfill(6)
            for code in fund_codes
            if str(code).strip().isdigit()
        }
    )[:80]
    if not targets:
        return []
    script = f"""
import akshare as ak
import json

targets = set({targets!r})
symbols = (
    "股票型基金",
    "混合型基金",
    "债券型基金",
    "QDII基金",
)

def number(value):
    if value is None or str(value).strip().lower() in ("", "nan", "--", "nat"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def iso_date(value):
    if value is None or str(value).strip().lower() in ("", "nan", "nat", "--"):
        return None
    if hasattr(value, "date"):
        try:
            return value.date().isoformat()
        except Exception:
            pass
    text = str(value).strip().replace("/", "-")
    return text[:10] if len(text) >= 10 else text

rows = {{}}
errors = []
for symbol in symbols:
    try:
        frame = ak.fund_scale_open_sina(symbol=symbol)
        if frame is None or frame.empty:
            continue
        for _, row in frame.iterrows():
            raw_code = str(row.get("基金代码", "")).strip().split(".", 1)[0]
            code = raw_code.zfill(6)
            if code not in targets or code in rows:
                continue
            nav = number(row.get("单位净值"))
            shares = number(row.get("最近总份额"))
            current_scale_yi = (
                nav * shares / 100_000_000
                if nav is not None and shares is not None and nav > 0 and shares > 0
                else None
            )
            rows[code] = {{
                "fund_code": code,
                "fund_name": str(row.get("基金简称") or "").strip(),
                "fund_category": symbol.replace("基金", ""),
                "latest_nav": nav,
                "fund_scale_yi": round(current_scale_yi, 4) if current_scale_yi is not None else None,
                "fund_scale_basis": "nav_times_latest_shares",
                "established_date": iso_date(row.get("成立日期")),
                "fund_manager": str(row.get("基金经理") or "").strip() or None,
                "profile_updated_at": iso_date(row.get("更新日期")),
                "profile_source": "sina.fund_scale_open_sina",
            }}
        if targets.issubset(rows):
            break
    except Exception as exc:
        errors.append(f"{{symbol}}:{{type(exc).__name__}}")

print(json.dumps({{"data": list(rows.values()), "errors": errors}}, ensure_ascii=False))
"""
    payload = run_akshare_json_script(
        script,
        label=f"fund_research_profiles:{len(targets)}",
        timeout=timeout_seconds,
    )
    if isinstance(payload, dict):
        rows = payload.get("data")
        if isinstance(rows, list):
            return rows
    return None


def fetch_fund_basic_profiles_xq(
    fund_codes: list[str],
    *,
    timeout_seconds: int | float = 35,
) -> list[dict] | None:
    """按代码批量读取基金基本资料，作为规模/经理补全的独立回退源。

    雪球基金接口单次只返回一只基金，因此放在同一个隔离子进程内做有限并发，
    避免主 API 进程加载第三方运行时，也避免候选池逐只串行等待。
    """

    targets = sorted(
        {
            str(code).strip().zfill(6)
            for code in fund_codes
            if str(code).strip().isdigit()
        }
    )[:80]
    if not targets:
        return []
    script = f"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import akshare as ak

targets = {targets!r}

def clean(value):
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in ("", "nan", "nat", "none", "<na>", "--"):
        return None
    return text

def shares_yi(value):
    text = clean(value)
    if text is None:
        return None
    normalized = text.replace(",", "").replace(" ", "")
    matched = re.search(r"[-+]?\\d+(?:\\.\\d+)?", normalized)
    if matched is None:
        return None
    number = float(matched.group(0))
    if number <= 0:
        return None
    if "亿" in normalized:
        return round(number, 4)
    if "万" in normalized:
        return round(number / 10000.0, 4)
    return None

def fetch_one(code):
    try:
        try:
            frame = ak.fund_individual_basic_info_xq(symbol=code, timeout=6)
        except TypeError:
            frame = ak.fund_individual_basic_info_xq(symbol=code)
        if frame is None or frame.empty:
            return None
        mapping = {{}}
        for _, row in frame.iterrows():
            key = clean(row.get("item"))
            if key is None:
                key = clean(row.get("字段"))
            if key is None:
                continue
            mapping[key] = row.get("value") if "value" in row else row.get("值")
        tracking_reference = clean(mapping.get("跟踪标的")) or clean(mapping.get("标的指数"))
        performance_benchmark = clean(mapping.get("业绩比较基准"))
        benchmark_text = tracking_reference or performance_benchmark
        return {{
            "fund_code": code,
            "fund_name": clean(mapping.get("基金名称")) or clean(mapping.get("基金简称")),
            "fund_category": clean(mapping.get("基金类型")),
            # AKShare 将蛋卷接口的 totshare 重命名为“最新规模”，但原始
            # 字段实际是基金份额，不是资产净值。这里只保存亿份，待候选
            # 已取得最新单位净值后再估算 AUM，避免把 12.46 亿份误写成
            # 12.46 亿元并参与清盘阈值判断。
            "fund_shares_yi": shares_yi(mapping.get("最新规模")),
            "fund_shares_basis": "xq_latest_reported_shares",
            "established_date": clean(mapping.get("成立时间")) or clean(mapping.get("成立日期")),
            "fund_manager": clean(mapping.get("基金经理")),
            "tracking_reference_text": tracking_reference,
            "benchmark_text": benchmark_text,
            "benchmark_text_kind": (
                "tracking_target"
                if tracking_reference is not None
                else "performance_benchmark"
                if performance_benchmark is not None
                else None
            ),
            "benchmark_text_source_kind": "xq_akshare_aggregator" if benchmark_text is not None else None,
            "profile_source": "xq.fund_individual_basic_info_xq",
        }}
    except Exception:
        return None

rows = []
worker_count = max(1, min(6, len(targets)))
with ThreadPoolExecutor(max_workers=worker_count) as executor:
    futures = {{executor.submit(fetch_one, code): code for code in targets}}
    for future in as_completed(futures):
        row = future.result()
        if row is not None:
            rows.append(row)

rows.sort(key=lambda item: item["fund_code"])
print(json.dumps({{"data": rows}}, ensure_ascii=False))
"""
    payload = run_akshare_json_script(
        script,
        label=f"fund_basic_profiles_xq:{len(targets)}",
        timeout=timeout_seconds,
    )
    if isinstance(payload, dict):
        rows = payload.get("data")
        if isinstance(rows, list):
            return rows
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
