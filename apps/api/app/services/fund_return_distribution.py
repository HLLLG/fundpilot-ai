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

_CACHE_KEY = "fund:return-distribution:v1"
_CACHE_TTL_SECONDS = 30 * 60.0
_FETCH_TIMEOUT_SECONDS = 30.0
_CN_TZ = ZoneInfo("Asia/Shanghai")


def build_fund_return_distribution(*, force_refresh: bool = False) -> dict:
    """返回最近一个已公布净值日的全量开放式基金涨跌分布。"""

    if not force_refresh:
        cached = get_spot_snapshot(_CACHE_KEY, ttl_seconds=_CACHE_TTL_SECONDS)
        if cached is not None:
            return dict(cached)

    result = _fetch_official_distribution(timeout=_FETCH_TIMEOUT_SECONDS)
    if result is not None:
        payload = {
            "available": True,
            "stale": False,
            "source_mode": "official_nav",
            "source_name": "东方财富开放式基金净值",
            "universe_scope": "开放式基金份额代码（A/C/E 等分别计数）",
            "fetched_at": datetime.now(_CN_TZ).isoformat(),
            **result,
        }
        save_spot_snapshot(_CACHE_KEY, payload)
        return payload

    stale = get_spot_snapshot_any_age(_CACHE_KEY)
    if stale is not None:
        payload = dict(stale)
        payload.update(
            {
                "stale": True,
                "message": "官方净值源本次更新失败，正在展示上次成功统计。",
            }
        )
        return payload

    return {
        "available": False,
        "stale": True,
        "source_mode": "official_nav",
        "message": "暂未取得可核验的开放式基金官方净值分布。",
    }


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

    bins = payload.get("bins")
    valid_count = _as_non_negative_int(payload.get("valid_count"))
    if not isinstance(bins, dict) or valid_count is None or valid_count <= 0:
        return None

    normalized_bins = {
        key: _as_non_negative_int(bins.get(key)) or 0
        for key in (
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
