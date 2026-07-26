from __future__ import annotations

"""开放式基金官方净值涨跌分布。

该模块只使用 ``fund_open_fund_daily_em`` 已公布的官方日增长率，不把盘中估值冒充
正式净值。统计粒度是基金份额代码（A/C/E 等分别计数），因此只能与同口径的基金
分布比较，不能与股票上涨/下跌家数直接比较。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.akshare_subprocess import run_akshare_json_script
from app.services.sector_quote_cache import (
    get_spot_snapshot,
    get_spot_snapshot_any_age,
    save_spot_snapshot,
)
from app.services.trading_session import build_trading_session

_CACHE_KEY = "fund:return-distribution:v1"
_CACHE_TTL_SECONDS = 30 * 60.0
_FETCH_TIMEOUT_SECONDS = 30.0
_CN_TZ = ZoneInfo("Asia/Shanghai")

_INTRADAY_CACHE_KEY = "fund:return-distribution:intraday:v1"
_INTRADAY_CACHE_TTL_SECONDS = 10 * 60.0
_INTRADAY_SOURCE_NAME = "东方财富实时估值"
_INTRADAY_UNIVERSE_SCOPE = "开放式基金份额代码（A/C/E 等分别计数，盘中估算口径）"
_INTRADAY_STALE_MESSAGE = "实时估值源本次更新失败，正在展示上次成功统计。"
_INTRADAY_UNAVAILABLE_MESSAGE = "暂未取得可核验的盘中实时估值分布。"

_OFFICIAL_SOURCE_NAME = "东方财富开放式基金净值"
_OFFICIAL_UNIVERSE_SCOPE = "开放式基金份额代码（A/C/E 等分别计数）"
_OFFICIAL_STALE_MESSAGE = "官方净值源本次更新失败，正在展示上次成功统计。"
_OFFICIAL_UNAVAILABLE_MESSAGE = "暂未取得可核验的开放式基金官方净值分布。"

_DISTRIBUTION_BIN_KEYS = (
    "le_neg5",
    "neg5_neg3",
    "neg3_neg1",
    "neg1_zero",
    "zero",
    "zero_one",
    "one_three",
    "three_five",
    "ge_five",
)


def _normalize_distribution_counts(payload: dict) -> dict | None:
    """校验 akshare 子进程回传的分布计数，失败返回 None。

    官方净值与盘中估值两条 fetcher 共用：bins 合计必须等于 valid_count，
    advance+decline+flat 也必须等于 valid_count，任一不符即视为本次拉取失败。
    """
    bins = payload.get("bins")
    valid_count = _as_non_negative_int(payload.get("valid_count"))
    if not isinstance(bins, dict) or valid_count is None or valid_count <= 0:
        return None

    normalized_bins = {
        key: _as_non_negative_int(bins.get(key)) or 0 for key in _DISTRIBUTION_BIN_KEYS
    }
    if sum(normalized_bins.values()) != valid_count:
        return None

    advance_count = _as_non_negative_int(payload.get("advance_count")) or 0
    decline_count = _as_non_negative_int(payload.get("decline_count")) or 0
    flat_count = _as_non_negative_int(payload.get("flat_count")) or 0
    if advance_count + decline_count + flat_count != valid_count:
        return None

    source_row_count = _as_non_negative_int(payload.get("source_row_count")) or valid_count
    missing_count = _as_non_negative_int(payload.get("missing_count")) or 0
    coverage_percent = _as_float(payload.get("coverage_percent"))
    return {
        "as_of_date": str(payload.get("as_of_date") or "")[:10] or None,
        "source_row_count": source_row_count,
        "valid_count": valid_count,
        "missing_count": missing_count,
        "coverage_percent": coverage_percent,
        "advance_count": advance_count,
        "decline_count": decline_count,
        "flat_count": flat_count,
        "bins": normalized_bins,
    }


def _build_distribution(
    *,
    cache_key: str,
    cache_ttl_seconds: float,
    fetch_fn,
    source_mode: str,
    source_name: str,
    universe_scope: str,
    stale_message: str,
    unavailable_message: str,
    force_refresh: bool,
) -> dict:
    """两条数据源（官方净值 / 盘中估值）共用的三级回退：
    服务端缓存命中 → 返回；拉取成功 → 写缓存返回；拉取失败 → 上一份 stale → 再失败 unavailable。
    """
    if not force_refresh:
        cached = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl_seconds)
        if cached is not None:
            return dict(cached)

    result = fetch_fn(timeout=_FETCH_TIMEOUT_SECONDS)
    if result is not None:
        payload = {
            "available": True,
            "stale": False,
            "source_mode": source_mode,
            "source_name": source_name,
            "universe_scope": universe_scope,
            "fetched_at": datetime.now(_CN_TZ).isoformat(),
            "as_of_datetime": result.get("as_of_date"),
            **result,
        }
        save_spot_snapshot(cache_key, payload)
        return payload

    stale = get_spot_snapshot_any_age(cache_key)
    if stale is not None:
        payload = dict(stale)
        payload.update({"stale": True, "message": stale_message})
        return payload

    return {
        "available": False,
        "stale": True,
        "source_mode": source_mode,
        "message": unavailable_message,
    }


def build_fund_return_distribution(*, force_refresh: bool = False) -> dict:
    """返回当前时段口径下的全量开放式基金涨跌分布。

    交易日连续交易时段（盘中、收盘前，排除午休）走东方财富实时估值按估算
    增长率分桶；其余时段（非交易日、盘前、午休、收盘后）走官方已结算净值。
    """
    session = build_trading_session()
    if session.get("is_continuous_trading"):
        return _build_distribution(
            cache_key=_INTRADAY_CACHE_KEY,
            cache_ttl_seconds=_INTRADAY_CACHE_TTL_SECONDS,
            fetch_fn=_fetch_intraday_estimate_distribution,
            source_mode="intraday_estimate",
            source_name=_INTRADAY_SOURCE_NAME,
            universe_scope=_INTRADAY_UNIVERSE_SCOPE,
            stale_message=_INTRADAY_STALE_MESSAGE,
            unavailable_message=_INTRADAY_UNAVAILABLE_MESSAGE,
            force_refresh=force_refresh,
        )
    return _build_distribution(
        cache_key=_CACHE_KEY,
        cache_ttl_seconds=_CACHE_TTL_SECONDS,
        fetch_fn=_fetch_official_distribution,
        source_mode="official_nav",
        source_name=_OFFICIAL_SOURCE_NAME,
        universe_scope=_OFFICIAL_UNIVERSE_SCOPE,
        stale_message=_OFFICIAL_STALE_MESSAGE,
        unavailable_message=_OFFICIAL_UNAVAILABLE_MESSAGE,
        force_refresh=force_refresh,
    )


def _fetch_official_distribution(*, timeout: float) -> dict | None:
    # 在 AkShare 子进程内直接聚合，避免把两万多行基金数据序列化回主进程。
    script = r'''
import json
import re
import akshare as ak

try:
    frame = ak.fund_open_fund_daily_em()
    if frame is None or frame.empty:
        print(json.dumps({"error": "empty"}))
    else:
        date_columns = []
        for column in frame.columns:
            match = re.match(r"^(\d{4}-\d{2}-\d{2})-\u5355\u4f4d\u51c0\u503c$", str(column))
            if match:
                date_columns.append(match.group(1))
        as_of_date = max(date_columns) if date_columns else None

        bins = {
            "le_neg5": 0,
            "neg5_neg3": 0,
            "neg3_neg1": 0,
            "neg1_zero": 0,
            "zero": 0,
            "zero_one": 0,
            "one_three": 0,
            "three_five": 0,
            "ge_five": 0,
        }
        valid_count = 0
        missing_count = 0
        advance_count = 0
        decline_count = 0
        flat_count = 0

        for raw in frame["\u65e5\u589e\u957f\u7387"]:
            try:
                if raw is None or str(raw).strip().lower() in ("", "nan", "--"):
                    raise ValueError("missing")
                value = float(raw)
            except (TypeError, ValueError):
                missing_count += 1
                continue

            valid_count += 1
            if value < 0:
                decline_count += 1
            elif value > 0:
                advance_count += 1
            else:
                flat_count += 1

            if value <= -5:
                bins["le_neg5"] += 1
            elif value <= -3:
                bins["neg5_neg3"] += 1
            elif value <= -1:
                bins["neg3_neg1"] += 1
            elif value < 0:
                bins["neg1_zero"] += 1
            elif value == 0:
                bins["zero"] += 1
            elif value < 1:
                bins["zero_one"] += 1
            elif value < 3:
                bins["one_three"] += 1
            elif value < 5:
                bins["three_five"] += 1
            else:
                bins["ge_five"] += 1

        source_row_count = int(len(frame))
        coverage_percent = (
            round(valid_count / source_row_count * 100, 2) if source_row_count else 0.0
        )
        print(json.dumps({
            "as_of_date": as_of_date,
            "source_row_count": source_row_count,
            "valid_count": valid_count,
            "missing_count": missing_count,
            "coverage_percent": coverage_percent,
            "advance_count": advance_count,
            "decline_count": decline_count,
            "flat_count": flat_count,
            "bins": bins,
        }, ensure_ascii=True))
except Exception as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=True))
'''
    payload = run_akshare_json_script(
        script,
        label="fund_return_distribution_official_nav",
        timeout=timeout,
    )
    if not isinstance(payload, dict) or payload.get("error"):
        return None

    return _normalize_distribution_counts(payload)


def _fetch_intraday_estimate_distribution(*, timeout: float) -> dict | None:
    # 盘中实时估值：ak.fund_value_estimation_em 返回的估算增长率列名形如
    # "YYYY-MM-DD-估算数据-估算增长率"（日期动态），子进程内按列名后缀定位。
    # 在子进程内聚合 2 万行，只回传小 JSON，主进程不接大表（对齐官方净值分支）。
    script = r'''
import json
import akshare as ak

try:
    frame = ak.fund_value_estimation_em(symbol="全部")
    if frame is None or frame.empty:
        print(json.dumps({"error": "empty"}))
    else:
        growth_col = None
        for col in frame.columns:
            if str(col).endswith("-估算数据-估算增长率"):
                growth_col = col
                break
        estimate_date = None
        for col in frame.columns:
            if str(col) == "估算日期":
                estimate_date = col
                break
        as_of_date = None
        if estimate_date is not None:
            for value in frame[estimate_date]:
                if value is not None and str(value).strip():
                    as_of_date = str(value)[:10]
                    break

        bins = {
            "le_neg5": 0,
            "neg5_neg3": 0,
            "neg3_neg1": 0,
            "neg1_zero": 0,
            "zero": 0,
            "zero_one": 0,
            "one_three": 0,
            "three_five": 0,
            "ge_five": 0,
        }
        valid_count = 0
        missing_count = 0
        advance_count = 0
        decline_count = 0
        flat_count = 0

        if growth_col is None:
            print(json.dumps({"error": "no estimate growth column"}))
        else:
            for raw in frame[growth_col]:
                if raw is None or str(raw).strip().lower() in ("", "nan", "--"):
                    missing_count += 1
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    missing_count += 1
                    continue
                valid_count += 1
                if value < 0:
                    decline_count += 1
                elif value > 0:
                    advance_count += 1
                else:
                    flat_count += 1
                if value <= -5:
                    bins["le_neg5"] += 1
                elif value <= -3:
                    bins["neg5_neg3"] += 1
                elif value <= -1:
                    bins["neg3_neg1"] += 1
                elif value < 0:
                    bins["neg1_zero"] += 1
                elif value == 0:
                    bins["zero"] += 1
                elif value < 1:
                    bins["zero_one"] += 1
                elif value < 3:
                    bins["one_three"] += 1
                elif value < 5:
                    bins["three_five"] += 1
                else:
                    bins["ge_five"] += 1
            source_row_count = int(len(frame))
            coverage_percent = (
                round(valid_count / source_row_count * 100, 2) if source_row_count else 0.0
            )
            print(json.dumps({
                "as_of_date": as_of_date,
                "source_row_count": source_row_count,
                "valid_count": valid_count,
                "missing_count": missing_count,
                "coverage_percent": coverage_percent,
                "advance_count": advance_count,
                "decline_count": decline_count,
                "flat_count": flat_count,
                "bins": bins,
            }, ensure_ascii=True))
except Exception as exc:
    print(json.dumps({"error": str(exc)}, ensure_ascii=True))
'''
    payload = run_akshare_json_script(
        script,
        label="fund_return_distribution_intraday_estimate",
        timeout=timeout,
    )
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    return _normalize_distribution_counts(payload)


def _as_non_negative_int(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
