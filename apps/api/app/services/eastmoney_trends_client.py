from __future__ import annotations

import json
import logging
import re
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
    "Connection": "close",
}

_EM_COMMON_PARAMS = {"invt": "2", "fltt": "2"}

# 指数页 K 线走 push2his（与 zz/2.931994 浏览器一致）；push2 在部分网络下 ERR_EMPTY_RESPONSE
_KLINE_URLS = (
    "https://push2delay.eastmoney.com/api/qt/stock/kline/get",
    "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://79.push2his.eastmoney.com/api/qt/stock/kline/get",
    "https://79.push2.eastmoney.com/api/qt/stock/kline/get",
    "https://88.push2.eastmoney.com/api/qt/stock/kline/get",
    "https://91.push2his.eastmoney.com/api/qt/stock/kline/get",
)

_TRENDS2_URLS = (
    "https://push2delay.eastmoney.com/api/qt/stock/trends2/get",
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
    "https://79.push2his.eastmoney.com/api/qt/stock/trends2/get",
    "https://push2.eastmoney.com/api/qt/stock/trends2/get",
    "https://79.push2.eastmoney.com/api/qt/stock/trends2/get",
    "https://88.push2.eastmoney.com/api/qt/stock/trends2/get",
    "https://91.push2his.eastmoney.com/api/qt/stock/trends2/get",
)

# 浏览器 zz 页 kline 用 fa5fd…；旧 push2 分钟接口用 7eea…
_KLINE_UTS = ("fa5fd1943c7b386f172d6893dbfba10b", "7eea3edcaed734bea9cbfc24409ed989")
_TRENDS_UT = "bd1d9ddb04089700cf9c27f6f7426281"

# kline 返回点数低于此阈值时视为稀疏骨架，继续尝试 trends2
_MIN_RICH_INTRADAY_POINTS = 30
# A 股板块/指数单日涨跌幅极少超过此值；超出视为东财 preKPrice 占位等脏数据
_MAX_PLAUSIBLE_DAILY_CHANGE = 15.0

IntradayPoint = dict[str, str | float]


def fetch_eastmoney_kline_close_percent(
    secid: str,
    *,
    source_code: str | None = None,
    trade_date: str | None = None,
    timeout: float = 12.0,
    max_retries: int = 2,
) -> float | None:
    """东财 K 线收盘涨跌幅（相对昨收），与分时 15:00 一致；优先日 K，再回落分钟末点。"""
    cleaned = str(secid).strip()
    if not cleaned and not source_code:
        return None

    proxies = {"http": None, "https": None}
    session = requests.Session()
    session.headers.update(_HEADERS)
    _apply_referer(session, cleaned, source_code)

    for candidate in _secid_candidates(cleaned, source_code):
        _apply_referer(session, candidate, source_code)
        change = _fetch_kline_close_percent(
            session,
            candidate,
            trade_date=trade_date,
            klt="101",
            timeout=timeout,
            max_retries=max_retries,
            proxies=proxies,
        )
        if change is not None:
            return change

    points = fetch_eastmoney_intraday_trends(
        secid,
        source_code=source_code,
        trade_date=trade_date,
        timeout=timeout,
        max_retries=max_retries,
    )
    if points:
        last = points[-1].get("percent")
        return float(last) if last is not None else None
    return None


def fetch_eastmoney_intraday_trends(
    secid: str,
    *,
    source_code: str | None = None,
    trade_date: str | None = None,
    timeout: float = 10.0,
    max_retries: int = 1,
) -> list[IntradayPoint]:
    """东财分时：trends2 与分钟 K 线（zz/2.{code} 指数页同源）。"""
    cleaned = str(secid).strip()
    if not cleaned and not source_code:
        return []

    proxies = {"http": None, "https": None}
    session = requests.Session()
    session.headers.update(_HEADERS)
    _apply_referer(session, cleaned, source_code)

    best_sparse: list[IntradayPoint] = []

    for candidate in _secid_candidates(cleaned, source_code):
        _apply_referer(session, candidate, source_code)
        # 中证 zz/2.{code} 分钟 K 更稳；trends2 作备用
        if candidate.startswith("2."):
            primary, secondary = _fetch_kline_intraday, _fetch_trends2_intraday
        else:
            primary, secondary = _fetch_trends2_intraday, _fetch_kline_intraday

        shared = dict(
            trade_date=trade_date,
            timeout=timeout,
            max_retries=max_retries,
            proxies=proxies,
        )
        primary_points = primary(session, candidate, **shared)

        if len(primary_points) >= _MIN_RICH_INTRADAY_POINTS:
            return primary_points

        secondary_points = secondary(session, candidate, **shared)

        best = (
            primary_points
            if len(primary_points) >= len(secondary_points)
            else secondary_points
        )
        if len(best) >= _MIN_RICH_INTRADAY_POINTS:
            return best

        if len(best) > len(best_sparse):
            best_sparse = best

    return best_sparse


def _is_connection_drop(exc: Exception) -> bool:
    name = type(exc).__name__
    if name in {"ConnectionError", "RemoteDisconnected", "ProtocolError", "ChunkedEncodingError"}:
        return True
    message = str(exc).lower()
    return "connection aborted" in message or "remote end closed" in message


def _secid_candidates(secid: str, source_code: str | None) -> list[str]:
    """中证等指数分钟 K 线常用 2.{code}，与 spot 用的 0.{code} 可能不同。"""
    ordered: list[str] = []
    code = (source_code or "").strip()
    if not code and secid and "." in secid:
        code = secid.split(".", 1)[1].strip()

    if code.upper().startswith("BK"):
        ordered.extend([f"90.{code}", secid])
    elif code.isdigit():
        # 页面 zz/2.{code} 与 unify/r/2.{code}；canonical 配置的 secid 优先
        if secid and "." in secid:
            ordered.append(secid)
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


def _apply_referer(
    session: requests.Session, secid: str, source_code: str | None
) -> None:
    code = (source_code or "").strip()
    market = "2"
    if secid and "." in secid:
        market, maybe_code = secid.split(".", 1)
        if not code and maybe_code.strip().isdigit():
            code = maybe_code.strip()
    if code.isdigit():
        session.headers["Referer"] = (
            f"https://quote.eastmoney.com/zz/{market}.{code}.html"
        )
    else:
        session.headers["Referer"] = _HEADERS["Referer"]


def _kline_beg_end(trade_date: str | None) -> tuple[str, str]:
    if trade_date:
        ymd = trade_date.replace("-", "")
        # 往前多取 7 天，确保 klines 包含昨收行（_prior_close_from_klines 需要）
        from datetime import date, timedelta
        try:
            d = date.fromisoformat(trade_date)
            beg = (d - timedelta(days=7)).strftime("%Y%m%d")
        except Exception:
            beg = ymd
        return beg, ymd
    return "0", "20500000"


def _read_em_json(response: requests.Response) -> dict[str, Any]:
    text = (response.text or "").strip()
    if not text:
        try:
            payload = response.json()
        except Exception:
            return {}
    elif text.startswith("{"):
        payload = json.loads(text)
    else:
        match = re.search(r"\((\{.*\})\)\s*;?\s*$", text, re.DOTALL)
        if match:
            payload = json.loads(match.group(1))
        else:
            payload = response.json()
    if not isinstance(payload, dict):
        return {}
    return payload


def _fetch_kline_close_percent(
    session: requests.Session,
    secid: str,
    *,
    trade_date: str | None,
    klt: str,
    timeout: float,
    max_retries: int,
    proxies: dict[str, None],
) -> float | None:
    beg, end = _kline_beg_end(trade_date)
    fqt = "1" if klt == "101" and secid.startswith("2.") else "0"
    base_params = {
        **_EM_COMMON_PARAMS,
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": klt,
        "fqt": fqt,
        "beg": beg,
        "end": end,
    }
    last_error: Exception | None = None
    for attempt in range(max_retries):
        for ut in _KLINE_UTS:
            params = {**base_params, "ut": ut}
            for url in _KLINE_URLS:
                try:
                    response = session.get(url, params=params, timeout=timeout, proxies=proxies)
                    response.raise_for_status()
                    payload = _read_em_json(response)
                    if klt == "1":
                        points = _parse_kline_payload(payload, trade_date=trade_date)
                        if points:
                            last = points[-1].get("percent")
                            return float(last) if last is not None else None
                    else:
                        change = _parse_kline_day_close_percent(
                            payload, trade_date=trade_date
                        )
                        if change is not None:
                            return change
                except Exception as exc:
                    last_error = exc
                    logger.debug("eastmoney kline %s klt=%s failed: %s", secid, klt, exc)
                    if _is_connection_drop(exc):
                        break
        if attempt + 1 < max_retries:
            time.sleep(0.4 * (attempt + 1))
    if last_error:
        logger.debug("eastmoney kline close %s exhausted: %s", secid, last_error)
    return None


def _fetch_kline_intraday(
    session: requests.Session,
    secid: str,
    *,
    trade_date: str | None,
    timeout: float,
    max_retries: int,
    proxies: dict[str, None],
) -> list[IntradayPoint]:
    """分钟 K：klt=1（勿用页面日 K 的 klt=101）。"""
    beg_end_variants = [_kline_beg_end(trade_date)]
    if trade_date:
        beg_end_variants.append(("0", "20500000"))

    last_error: Exception | None = None
    for beg, end in beg_end_variants:
        base_params = {
            **_EM_COMMON_PARAMS,
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "1",
            "fqt": "0",
            "beg": beg,
            "end": end,
            "lmt": "1000000",
        }
        for attempt in range(max_retries):
            for ut in _KLINE_UTS:
                params = {**base_params, "ut": ut}
                for url in _KLINE_URLS:
                    try:
                        response = session.get(
                            url, params=params, timeout=timeout, proxies=proxies
                        )
                        response.raise_for_status()
                        points = _parse_kline_payload(
                            _read_em_json(response), trade_date=trade_date
                        )
                        if points:
                            return points
                    except Exception as exc:
                        last_error = exc
                        logger.debug(
                            "eastmoney kline %s url=%s failed: %s", secid, url, exc
                        )
                        if _is_connection_drop(exc):
                            break
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
        **_EM_COMMON_PARAMS,
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": "1" if trade_date else "5",
        "ut": _TRENDS_UT,
    }
    last_error: Exception | None = None
    for attempt in range(max_retries):
        for url in _TRENDS2_URLS:
            try:
                response = session.get(url, params=params, timeout=timeout, proxies=proxies)
                response.raise_for_status()
                points = _parse_trends_payload(
                    _read_em_json(response), trade_date=trade_date
                )
                if points:
                    return points
            except Exception as exc:
                last_error = exc
                logger.debug("eastmoney trends2 %s url=%s failed: %s", secid, url, exc)
                if _is_connection_drop(exc):
                    break
        if attempt + 1 < max_retries:
            time.sleep(0.35 * (attempt + 1))
    if last_error:
        logger.debug("eastmoney trends2 %s exhausted: %s", secid, last_error)
    return []


def is_plausible_daily_change(value: float | None) -> bool:
    return value is not None and abs(value) <= _MAX_PLAUSIBLE_DAILY_CHANGE


def _pick_day_change_percent(
    *,
    close: float,
    pre_close: float | None,
    change_pct: float | None,
    klines: list[Any],
    trade_date: str | None,
    day_token: str,
) -> float | None:
    """日 K 涨跌幅：昨收锚点 > preKPrice 比值 > 行内涨跌列（概念板块 preKPrice=1000 占位）。"""
    from_prior: float | None = None
    prior_date = trade_date or day_token
    if prior_date:
        prior = _prior_close_from_klines(klines, trade_date=prior_date)
        if prior and prior > 0:
            from_prior = round((close / prior - 1) * 100, 4)

    computed: float | None = None
    if pre_close and pre_close > 0:
        computed = round((close / pre_close - 1) * 100, 4)

    if not pre_close or pre_close <= 0:
        if is_plausible_daily_change(change_pct):
            return change_pct
        return from_prior or change_pct

    if computed is not None and not is_plausible_daily_change(computed):
        if is_plausible_daily_change(change_pct):
            return change_pct
        return from_prior

    if from_prior is not None and is_plausible_daily_change(from_prior):
        if change_pct is not None and abs(from_prior - change_pct) <= 0.3:
            return change_pct
        if computed is not None and abs(from_prior - computed) <= 0.3:
            return computed
        return from_prior

    if computed is not None and is_plausible_daily_change(computed):
        if change_pct is not None and abs(computed - change_pct) > 1.0:
            return computed
        if change_pct is None or abs(computed - change_pct) <= 0.5:
            return computed

    if is_plausible_daily_change(change_pct):
        return change_pct
    return computed or from_prior


def _parse_kline_day_close_percent(
    payload: dict[str, Any], *, trade_date: str | None = None
) -> float | None:
    """日 K 单行：收盘相对昨收涨跌幅，与分时 15:00 末点一致。"""
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    pre_close = _resolve_pre_close(data, klines, trade_date=trade_date)

    target: list[str] | None = None
    day_token = ""
    for raw in reversed(klines):
        if not isinstance(raw, str):
            continue
        parts = raw.split(",")
        if len(parts) < 3:
            continue
        day_token = parts[0].strip().split(" ")[0]
        if trade_date and day_token != trade_date:
            continue
        target = parts
        break

    if target is None:
        return None

    close = _as_float(target[2])
    change_pct = _as_float(target[8]) if len(target) > 8 else None
    if close is None or close <= 0:
        return change_pct
    return _pick_day_change_percent(
        close=close,
        pre_close=pre_close,
        change_pct=change_pct,
        klines=klines,
        trade_date=trade_date,
        day_token=day_token,
    )


def _parse_kline_payload(payload: dict[str, Any], *, trade_date: str | None = None) -> list[IntradayPoint]:
    """养基宝分时语义：相对昨收（preKPrice）涨跌，开盘约 -0.8%、收盘与右上角日涨跌一致。"""
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    pre_close = _resolve_pre_close(data, klines, trade_date=trade_date)
    if (pre_close is None or pre_close <= 0) and trade_date:
        pre_close = _prior_close_from_klines(klines, trade_date=trade_date)

    rows: list[tuple[str, float, float | None]] = []
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
        close = _as_float(parts[2])
        if close is None or close <= 0:
            continue
        change_pct = _as_float(parts[8]) if len(parts) > 8 else None
        rows.append((clock, close, change_pct))

    if len(rows) < 2:
        return []

    points: list[IntradayPoint] = []
    for clock, close, change_pct in rows:
        if pre_close and pre_close > 0:
            percent = round((close / pre_close - 1) * 100, 4)
        elif change_pct is not None:
            percent = change_pct
        else:
            continue
        points.append({"time": clock, "percent": percent})
    return points


def _prior_close_from_klines(klines: list[Any], *, trade_date: str) -> float | None:
    prior_close: float | None = None
    prior_day: str | None = None
    for raw in klines:
        if not isinstance(raw, str):
            continue
        parts = raw.split(",")
        if len(parts) < 3:
            continue
        day = parts[0].strip().split(" ")[0]
        if day >= trade_date:
            continue
        close = _as_float(parts[2])
        if close is None or close <= 0:
            continue
        if prior_day is None or day > prior_day:
            prior_day = day
            prior_close = close
    return prior_close


def _resolve_pre_close(
    data: dict[str, Any],
    klines: list[Any],
    *,
    trade_date: str | None,
) -> float | None:
    if "preKPrice" in data:
        pre_k = _as_float(data.get("preKPrice"))
        if pre_k is not None and pre_k > 0:
            return pre_k
        if pre_k is not None and pre_k == 0:
            return None

    for key in ("preClose", "yestclose"):
        value = _as_float(data.get(key))
        if value is not None and value > 0:
            return value

    if trade_date:
        prior = _prior_close_from_klines(klines, trade_date=trade_date)
        if prior is not None:
            return prior

    prior_close: float | None = None
    prior_day: str | None = None
    for raw in klines:
        if not isinstance(raw, str):
            continue
        parts = raw.split(",")
        if len(parts) < 3:
            continue
        day = parts[0].strip().split(" ")[0]
        if trade_date and day >= trade_date:
            continue
        close = _as_float(parts[2])
        if close is None or close <= 0:
            continue
        if prior_day is None or day > prior_day:
            prior_day = day
            prior_close = close
    return prior_close


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
        return points
    return []


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
