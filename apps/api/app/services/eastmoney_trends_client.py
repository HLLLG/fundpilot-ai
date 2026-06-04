from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}

# 与 spot 一致走 push2；少 host 降低并发，避免 ERR_EMPTY_RESPONSE / 限流
_PUSH2_HOSTS = ("79", "88", "48")

_PUSH2HIS_HOSTS = ("push2his.eastmoney.com", "79.push2his.eastmoney.com")

_KLINE_UT = "7eea3edcaed734bea9cbfc24409ed989"
_TRENDS_UT = "bd1d9ddb04089700cf9c27f6f7426281"

IntradayPoint = dict[str, str | float]


def fetch_eastmoney_intraday_trends(
    secid: str,
    *,
    source_code: str | None = None,
    trade_date: str | None = None,
    timeout: float = 15.0,
    max_retries: int = 2,
) -> list[IntradayPoint]:
    """东财分时：优先 push2 分钟 K 线（收盘后仍可拉当日 09:30–15:00）。"""
    cleaned = str(secid).strip()
    if not cleaned and not source_code:
        return []

    proxies = {"http": None, "https": None}
    session = requests.Session()
    session.headers.update(_HEADERS)

    for candidate in _secid_candidates(cleaned, source_code):
        points = _fetch_kline_intraday(
            session,
            candidate,
            trade_date=trade_date,
            timeout=timeout,
            max_retries=max_retries,
            proxies=proxies,
        )
        if points:
            return points

    return _fetch_trends2_intraday(
        session,
        cleaned or _secid_candidates("", source_code)[0],
        trade_date=trade_date,
        timeout=timeout,
        max_retries=max_retries,
        proxies=proxies,
    )


def _secid_candidates(secid: str, source_code: str | None) -> list[str]:
    """中证等指数分钟 K 线常用 2.{code}，与 spot 用的 0.{code} 可能不同。"""
    ordered: list[str] = []
    code = (source_code or "").strip()
    if not code and secid and "." in secid:
        code = secid.split(".", 1)[1].strip()

    if code.upper().startswith("BK"):
        ordered.extend([f"90.{code}", secid])
    elif code.isdigit():
        # 中证指数分钟 K 优先 2.{code}，仅再试 0.{code}，避免对东财连打数十次请求
        for prefix in ("2", "0"):
            ordered.append(f"{prefix}.{code}")
    elif secid:
        ordered.append(secid)

    seen: set[str] = set()
    result: list[str] = []
    for item in ordered:
        token = item.strip()
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _fetch_kline_intraday(
    session: requests.Session,
    secid: str,
    *,
    trade_date: str | None,
    timeout: float,
    max_retries: int,
    proxies: dict[str, None],
) -> list[IntradayPoint]:
    params = {
        "secid": secid,
        "ut": _KLINE_UT,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "1",
        "fqt": "0",
        "beg": "0",
        "end": "20500000",
    }
    last_error: Exception | None = None
    for attempt in range(max_retries):
        for host in _PUSH2_HOSTS:
            url = f"https://{host}.push2.eastmoney.com/api/qt/stock/kline/get"
            try:
                response = session.get(url, params=params, timeout=timeout, proxies=proxies)
                response.raise_for_status()
                points = _parse_kline_payload(response.json(), trade_date=trade_date)
                if points:
                    return points
            except Exception as exc:
                last_error = exc
                logger.debug("eastmoney kline %s host=%s failed: %s", secid, host, exc)
        if attempt + 1 < max_retries:
            time.sleep(0.4 * (attempt + 1))
    if last_error:
        logger.debug("eastmoney kline %s exhausted: %s", secid, last_error)
    return []


def _fetch_trends2_intraday(
    session: requests.Session,
    secid: str,
    *,
    trade_date: str | None,
    timeout: float,
    max_retries: int,
    proxies: dict[str, None],
) -> list[IntradayPoint]:
    if not secid:
        return []
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": "5",
        "ut": _TRENDS_UT,
    }
    last_error: Exception | None = None
    for attempt in range(max_retries):
        for host in _PUSH2HIS_HOSTS:
            url = f"https://{host}/api/qt/stock/trends2/get"
            try:
                response = session.get(url, params=params, timeout=timeout, proxies=proxies)
                response.raise_for_status()
                points = _parse_trends_payload(response.json(), trade_date=trade_date)
                if points:
                    return points
            except Exception as exc:
                last_error = exc
                logger.debug("eastmoney trends2 %s host=%s failed: %s", secid, host, exc)
        if attempt + 1 < max_retries:
            time.sleep(0.35 * (attempt + 1))
    if last_error:
        logger.debug("eastmoney trends2 %s exhausted: %s", secid, last_error)
    return []


def _parse_kline_payload(payload: dict[str, Any], *, trade_date: str | None = None) -> list[IntradayPoint]:
    """养基宝分时语义：相对当日开盘价（首根 K 的 open）涨跌，不用昨收累计涨跌幅列。"""
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    rows: list[tuple[str, float]] = []
    session_open: float | None = None
    for raw in klines:
        if not isinstance(raw, str):
            continue
        parts = raw.split(",")
        if len(parts) < 3:
            continue
        dt_token = parts[0].strip().split(" ")
        if len(dt_token) < 2:
            continue
        day, clock = dt_token[0], dt_token[1][:5]
        if trade_date and day != trade_date:
            continue
        if not _in_trading_clock(clock):
            continue
        if session_open is None:
            session_open = _as_float(parts[1])
        close = _as_float(parts[2])
        if close is None or close <= 0:
            continue
        rows.append((clock, close))

    if len(rows) < 2:
        return []

    baseline = session_open if session_open and session_open > 0 else rows[0][1]
    points: list[IntradayPoint] = []
    for clock, close in rows:
        percent = round((close / baseline - 1) * 100, 4)
        points.append({"time": clock, "percent": percent})
    return points


def _parse_trends_payload(payload: dict[str, Any], *, trade_date: str | None = None) -> list[IntradayPoint]:
    data = payload.get("data") or {}
    trends = data.get("trends") or []
    if not trends:
        return []

    pre_close = _as_float(data.get("prePrice"))
    if pre_close is None:
        pre_close = _as_float(data.get("preClose"))
    if pre_close is None:
        pre_close = _as_float(data.get("yestclose"))

    points: list[IntradayPoint] = []
    for raw in trends:
        if not isinstance(raw, str):
            continue
        parts = raw.split(",")
        if len(parts) < 3:
            continue
        time_token = parts[0].strip().split(" ")
        if len(time_token) < 2:
            continue
        day, clock = time_token[0], time_token[1][:5]
        if trade_date and day != trade_date:
            continue
        if not _in_trading_clock(clock):
            continue
        price = _as_float(parts[2])
        if price is None:
            price = _as_float(parts[-1])
        if price is None:
            continue
        if pre_close is None or pre_close <= 0:
            if not points:
                pre_close = price
            percent = round((price / pre_close - 1) * 100, 4) if pre_close else 0.0
        else:
            percent = round((price / pre_close - 1) * 100, 4)
        points.append({"time": clock, "percent": percent})

    if len(points) >= 2:
        return _rebase_intraday_to_open(points)
    return []


def _rebase_intraday_to_open(points: list[IntradayPoint]) -> list[IntradayPoint]:
    if len(points) < 2:
        return points
    baseline = points[0]["percent"]
    return [
        {"time": point["time"], "percent": round(point["percent"] - baseline, 4)}
        for point in points
    ]


def _in_trading_clock(clock: str) -> bool:
    try:
        hour, minute = clock.split(":")
        total = int(hour) * 60 + int(minute)
    except ValueError:
        return False
    return (9 * 60 + 30) <= total <= (15 * 60)


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        cleaned = str(value).replace("%", "").replace(",", "").strip()
        if not cleaned or cleaned == "-":
            return None
        return round(float(cleaned), 4)
    except ValueError:
        return None
