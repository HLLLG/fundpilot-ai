from __future__ import annotations

"""开放式基金涨跌分布（盘中估算 + 日终官方净值）。

请求路径只读共享缓存；批量聚合由后台市场刷新线程完成，避免首位访问者承担两万多
只基金代码的同步等待。交易日开盘后优先展示同一交易日的盘中估算，并在午休、收盘后
保留最后一份当日估算；只有当日官方净值覆盖率达标后才切换为正式净值。上一交易日
数据不会被冒充为当日分布。

统计粒度是基金份额代码（A/C/E 等分别计数），不能与股票上涨/下跌家数直接比较。
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

_CACHE_KEY = "fund:return-distribution:v2"
_CACHE_TTL_SECONDS = 30 * 60.0
_FETCH_TIMEOUT_SECONDS = 60.0
_CN_TZ = ZoneInfo("Asia/Shanghai")

_INTRADAY_CACHE_KEY = "fund:return-distribution:intraday:v3"
_INTRADAY_CACHE_TTL_SECONDS = 15 * 60.0
_INTRADAY_SOURCE_NAME = "新浪基金盘中估值"
_INTRADAY_UNIVERSE_SCOPE = (
    "开放式基金份额代码（A/C/E 等分别计数；仅纳入新浪返回当日估值的份额）"
)
_INTRADAY_STALE_MESSAGE = "新浪盘中估值源本次更新失败，正在展示当日上次成功统计。"
_INTRADAY_UNAVAILABLE_MESSAGE = "暂未取得达到质量门槛的当日基金估值分布。"
_INTRADAY_PARTIAL_MESSAGE = (
    "盘中为估算参考，新浪不覆盖的债券、QDII、FOF 等份额会计入缺失数；"
    "请结合覆盖率使用，不能与全市场官方净值分布直接横向比较。"
)

_OFFICIAL_SOURCE_NAME = "东方财富开放式基金净值"
_OFFICIAL_UNIVERSE_SCOPE = "开放式基金份额代码（A/C/E 等分别计数）"
_OFFICIAL_STALE_MESSAGE = "官方净值源本次更新失败，正在展示上次成功统计。"
_OFFICIAL_UNAVAILABLE_MESSAGE = "暂未取得可核验的开放式基金官方净值分布。"

# 官方净值逐只公布；新浪估值则系统性不覆盖部分债券、QDII、FOF 等品类，所以采用
# 两套明确门槛，并在盘中响应里保留全开放式基金分母和缺失提示，不伪装成全量覆盖。
_MIN_OFFICIAL_COVERAGE_PERCENT = 70.0
_MIN_INTRADAY_COVERAGE_PERCENT = 50.0
_MIN_INTRADAY_VALID_COUNT = 10_000
_CURRENT_DAY_UNAVAILABLE_MESSAGE = (
    "当日基金涨跌分布尚未准备好；为避免误导，不使用上一交易日净值替代今日数据。"
)

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
    if source_row_count < valid_count:
        return None
    missing_count = source_row_count - valid_count
    supplied_missing = _as_non_negative_int(payload.get("missing_count"))
    if supplied_missing is not None and supplied_missing != missing_count:
        return None
    coverage_percent = round(valid_count / source_row_count * 100, 2)
    supplied_coverage = _as_float(payload.get("coverage_percent"))
    if supplied_coverage is not None and abs(supplied_coverage - coverage_percent) > 0.01:
        return None
    return {
        "as_of_date": str(payload.get("as_of_date") or "")[:10] or None,
        "as_of_datetime": str(payload.get("as_of_datetime") or "").strip() or None,
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
    expected_trade_date: str | None = None,
    min_coverage_percent: float,
    min_valid_count: int = 1,
    success_message: str | None = None,
) -> dict:
    """两条数据源共用的后台刷新 / 请求只读缓存策略。

    ``force_refresh=False`` 是 API 请求路径，只读新鲜或 stale 缓存，不同步打外源；
    ``force_refresh=True`` 仅供后台线程和显式维护调用。指定 ``expected_trade_date``
    时，旧交易日快照会被拒绝，避免把昨日分布冒充今日数据。
    """
    if not force_refresh:
        cached = _load_cached_distribution(
            cache_key=cache_key,
            cache_ttl_seconds=cache_ttl_seconds,
            expected_trade_date=expected_trade_date,
            stale_message=stale_message,
            min_coverage_percent=min_coverage_percent,
            min_valid_count=min_valid_count,
        )
        if cached is not None:
            return cached
        return _unavailable_distribution(
            source_mode=source_mode,
            message=unavailable_message,
            expected_trade_date=expected_trade_date,
        )

    result = fetch_fn(timeout=_FETCH_TIMEOUT_SECONDS)
    if result is not None:
        payload = {
            "available": True,
            "stale": False,
            "source_mode": source_mode,
            "source_name": source_name,
            "universe_scope": universe_scope,
            "fetched_at": datetime.now(_CN_TZ).isoformat(),
            "message": success_message,
            **result,
            "as_of_datetime": result.get("as_of_datetime") or result.get("as_of_date"),
        }
        if _distribution_payload_is_usable(
            payload,
            expected_trade_date=expected_trade_date,
            min_coverage_percent=min_coverage_percent,
            min_valid_count=min_valid_count,
        ):
            save_spot_snapshot(cache_key, payload)
            return payload

    stale = _load_cached_distribution(
        cache_key=cache_key,
        cache_ttl_seconds=cache_ttl_seconds,
        expected_trade_date=expected_trade_date,
        stale_message=stale_message,
        fresh_first=False,
        min_coverage_percent=min_coverage_percent,
        min_valid_count=min_valid_count,
    )
    if stale is not None:
        return stale

    return _unavailable_distribution(
        source_mode=source_mode,
        message=unavailable_message,
        expected_trade_date=expected_trade_date,
    )


def _unavailable_distribution(
    *,
    source_mode: str,
    message: str,
    expected_trade_date: str | None,
) -> dict:
    return {
        "available": False,
        "stale": True,
        "source_mode": source_mode,
        "as_of_date": expected_trade_date,
        "message": message,
    }


def _distribution_payload_is_usable(
    payload: dict,
    *,
    expected_trade_date: str | None,
    min_coverage_percent: float,
    min_valid_count: int,
) -> bool:
    if payload.get("available") is not True:
        return False
    if expected_trade_date:
        actual_date = str(
            payload.get("as_of_date") or payload.get("as_of_datetime") or ""
        )[:10]
        if actual_date != expected_trade_date:
            return False

    valid_count = _as_non_negative_int(payload.get("valid_count")) or 0
    if valid_count < min_valid_count:
        return False
    coverage = _as_float(payload.get("coverage_percent"))
    if coverage is None:
        source_count = _as_non_negative_int(payload.get("source_row_count")) or valid_count
        coverage = valid_count / source_count * 100 if source_count else 0.0
    return coverage >= min_coverage_percent


def _load_cached_distribution(
    *,
    cache_key: str,
    cache_ttl_seconds: float,
    expected_trade_date: str | None,
    stale_message: str,
    min_coverage_percent: float,
    min_valid_count: int,
    fresh_first: bool = True,
) -> dict | None:
    if fresh_first:
        fresh = get_spot_snapshot(cache_key, ttl_seconds=cache_ttl_seconds)
        if fresh is not None and _distribution_payload_is_usable(
            fresh,
            expected_trade_date=expected_trade_date,
            min_coverage_percent=min_coverage_percent,
            min_valid_count=min_valid_count,
        ):
            return dict(fresh)

    stale = get_spot_snapshot_any_age(cache_key)
    if stale is None or not _distribution_payload_is_usable(
        stale,
        expected_trade_date=expected_trade_date,
        min_coverage_percent=min_coverage_percent,
        min_valid_count=min_valid_count,
    ):
        return None
    payload = dict(stale)
    payload.update({"stale": True, "message": stale_message})
    return payload


def _official_distribution(
    *,
    force_refresh: bool,
    expected_trade_date: str | None = None,
) -> dict:
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
        expected_trade_date=expected_trade_date,
        min_coverage_percent=_MIN_OFFICIAL_COVERAGE_PERCENT,
    )


def _intraday_distribution(
    *,
    force_refresh: bool,
    expected_trade_date: str | None,
) -> dict:
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
        expected_trade_date=expected_trade_date,
        min_coverage_percent=_MIN_INTRADAY_COVERAGE_PERCENT,
        min_valid_count=_MIN_INTRADAY_VALID_COUNT,
        success_message=_INTRADAY_PARTIAL_MESSAGE,
    )


def build_fund_return_distribution(*, force_refresh: bool = False) -> dict:
    """返回当前交易日优先、缓存优先的开放式基金涨跌分布。

    交易日开盘后（含午休与收盘后）只接受同一交易日数据：先看当日官方净值是否
    已达到覆盖率门槛，否则使用当日盘中估算。收盘后的后台刷新会继续检查官方净值，
    达标后自动切换。盘前和非交易日使用最近官方净值。
    """
    session = build_trading_session()
    expected_trade_date = str(session.get("effective_trade_date") or "")[:10] or None
    calendar_date = str(session.get("calendar_date") or "")[:10] or None
    session_kind = str(session.get("session_kind") or "")
    market_phase = str(session.get("market_phase") or "")
    current_trade_day_after_open = bool(
        session.get("is_continuous_trading")
        or (
            session.get("is_trading_day")
            and expected_trade_date
            and expected_trade_date == calendar_date
            and session_kind != "trading_day_pre_open"
        )
    )

    if not current_trade_day_after_open:
        return _official_distribution(force_refresh=force_refresh)

    # 请求路径先读缓存；后台在收盘后强制检查官方源。只有日期和覆盖率均达标，
    # 才允许正式净值替换当日估算。
    official = _official_distribution(
        force_refresh=bool(force_refresh and market_phase == "after_close"),
        expected_trade_date=expected_trade_date,
    )
    if official.get("available"):
        return official

    refresh_intraday = bool(
        force_refresh
        and market_phase in {"continuous", "lunch_break", "after_close", ""}
    )
    intraday = _intraday_distribution(
        force_refresh=refresh_intraday,
        expected_trade_date=expected_trade_date,
    )
    if intraday.get("available"):
        return intraday

    return _unavailable_distribution(
        source_mode="intraday_estimate",
        message=_CURRENT_DAY_UNAVAILABLE_MESSAGE,
        expected_trade_date=expected_trade_date,
    )


def fund_return_distribution_is_settled(
    payload: dict | None,
    session: dict | None = None,
) -> bool:
    """官方净值已对齐当前有效交易日时，再打源只会拿到同一份收盘分布。"""
    if not payload or payload.get("available") is not True:
        return False
    if payload.get("source_mode") != "official_nav":
        return False
    resolved = session or build_trading_session()
    expected = str(resolved.get("effective_trade_date") or "")[:10]
    actual = str(payload.get("as_of_date") or "")[:10]
    return bool(expected and actual == expected)


def refresh_fund_return_distribution_snapshot() -> dict:
    """后台刷新入口：同步打源并持久化，API 请求本身不承担该开销。"""
    return build_fund_return_distribution(force_refresh=True)


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
        dated_nav_columns = []
        for column in frame.columns:
            match = re.match(r"^(\d{4}-\d{2}-\d{2})-\u5355\u4f4d\u51c0\u503c$", str(column))
            if match:
                dated_nav_columns.append((match.group(1), column))

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
        advance_count = 0
        decline_count = 0
        flat_count = 0

        source_row_count = int(len(frame))
        dated_nav_columns.sort(reverse=True)
        nav_values_by_date = [
            (nav_date, list(frame[column]))
            for nav_date, column in dated_nav_columns
        ]
        dated_growth_values = []
        date_counts = {}
        for row_index, raw in enumerate(frame["\u65e5\u589e\u957f\u7387"]):
            try:
                if raw is None or str(raw).strip().lower() in ("", "nan", "--"):
                    raise ValueError("missing growth")
                value = float(raw)
            except (TypeError, ValueError):
                continue

            # 东财晚间会逐只切换到当日净值。日增长率属于该行最新已公布净值日，
            # 因此必须逐行确认日期；不能拿全表最常见的昨日净值列给少量今日增长率贴标签。
            latest_nav_date = None
            for nav_date, nav_values in nav_values_by_date:
                raw_nav = nav_values[row_index]
                try:
                    if raw_nav is None or str(raw_nav).strip().lower() in ("", "nan", "--"):
                        continue
                    float(raw_nav)
                    latest_nav_date = nav_date
                    break
                except (TypeError, ValueError):
                    continue
            if latest_nav_date is None:
                continue
            dated_growth_values.append((latest_nav_date, value))
            date_counts[latest_nav_date] = date_counts.get(latest_nav_date, 0) + 1

        as_of_date = (
            max(date_counts, key=lambda item: (date_counts[item], item))
            if date_counts
            else None
        )
        for nav_date, value in dated_growth_values:
            if nav_date != as_of_date:
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

        missing_count = source_row_count - valid_count
        coverage_percent = (
            round(valid_count / source_row_count * 100, 2) if source_row_count else 0.0
        )
        print(json.dumps({
            "as_of_date": as_of_date,
            "as_of_datetime": as_of_date,
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
    # 东财公开估值接口已下线；新浪 ``fu_<基金代码>`` 仍返回盘中估算增长率。
    # 先用东财开放式基金净值表固定活跃份额代码全集，再分批并发查询新浪。只有
    # 主导日期对应的数值进入分布，旧日期行和空行一律算缺失，避免混入昨日估值。
    script = r'''
import concurrent.futures
import json
import re

import akshare as ak
import requests

URL = "https://hq.sinajs.cn/list="
BATCH_SIZE = 450
MAX_WORKERS = 8
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}

def fetch_batch(codes):
    symbols = ",".join("fu_" + code for code in codes)
    response = requests.get(URL + symbols, headers=HEADERS, timeout=(5, 20))
    response.raise_for_status()
    return response.content.decode("gbk", errors="replace")

try:
    frame = ak.fund_open_fund_daily_em()
    if frame is None or frame.empty:
        raise ValueError("empty open-fund universe")
    codes = []
    seen_codes = set()
    for raw in frame.iloc[:, 0]:
        code = str(raw or "").strip().zfill(6)
        if len(code) != 6 or not code.isdigit() or code in seen_codes:
            continue
        seen_codes.add(code)
        codes.append(code)
    if len(codes) < 15000:
        raise ValueError(f"incomplete open-fund universe: {len(codes)}")

    batches = [codes[index:index + BATCH_SIZE] for index in range(0, len(codes), BATCH_SIZE)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        texts = list(executor.map(fetch_batch, batches))

    rows_by_code = {}
    pattern = re.compile(r'var hq_str_fu_(\d{6})="(.*)";')
    for text in texts:
        for line in text.splitlines():
            match = pattern.fullmatch(line.strip())
            if match:
                rows_by_code[match.group(1)] = match.group(2).split(",")
    if len(rows_by_code) != len(codes):
        raise ValueError(
            f"incomplete sina response: rows={len(rows_by_code)} expected={len(codes)}"
        )

    # 每行字段：名称、时间、估值、昨净值、累计净值、五分钟涨速、估算增长率、日期。
    # 先按有数值的行确定主导日期，再仅统计该日期，防止少量停更基金混入旧值。
    dated_values = []
    date_counts = {}
    for code in codes:
        fields = rows_by_code.get(code) or []
        if len(fields) < 8:
            continue
        date_text = str(fields[7] or "").strip()[:10]
        value_text = str(fields[6] or "").strip().replace("%", "").replace(",", "")
        try:
            value = float(value_text)
        except (TypeError, ValueError):
            continue
        if not date_text:
            continue
        time_text = str(fields[1] or "").strip()
        dated_values.append((date_text, time_text, value))
        date_counts[date_text] = date_counts.get(date_text, 0) + 1
    if not date_counts:
        raise ValueError("no dated Sina estimates")
    as_of_date = max(date_counts, key=lambda item: (date_counts[item], item))
    current_values = [row for row in dated_values if row[0] == as_of_date]
    if not current_values:
        raise ValueError("empty dominant-date estimates")

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

    for _, _, value in current_values:
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

    source_row_count = len(codes)
    missing_count = source_row_count - valid_count
    coverage_percent = (
        round(valid_count / source_row_count * 100, 2) if source_row_count else 0.0
    )
    latest_time = max((row[1] for row in current_values if row[1]), default="")
    as_of_datetime = f"{as_of_date} {latest_time}".strip()
    print(json.dumps({
        "as_of_date": as_of_date,
        "as_of_datetime": as_of_datetime,
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
