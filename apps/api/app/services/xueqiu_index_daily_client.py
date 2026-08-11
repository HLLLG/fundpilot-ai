"""雪球指数日线：中证 9xxxxx / H3xxxx 的主源。

## 为什么是它

2026-08-11 从生产服务器逐源实测，能报**没有交易所行情代码**的中证指数（930598 中证稀土、
931582 中证数字经济、H30202 中证全指软件……）的只有两家：中证指数公司官网，和雪球。
东财 kline 全系主机 TCP 断连；新浪与腾讯 gtimg 只认交易所挂牌代码，对 9xxxxx 一律 0 行；
同花顺 404；网易 502；和讯 DNS 不通。

两家对比：

| | 中证官网 | 雪球 |
|---|---|---|
| 配额 | **约 40~50 次/小时**，超了 403 且粘性（实测 25 分钟才恢复） | 64 个代码背靠背 2.5s 全部 200，无限流迹象 |
| 深度 | 按日期范围，实测 800 行 0.22s | `count=-1200` 实测给满 1200 行 |
| 字段 | OHLC + 成交量 | 带 `column` 名字数组的 OHLCV |
| 握手 | 无 | 需要先访问 xueqiu.com 拿 cookie |
| 身份 | 指数**发布方** | 聚合站 |

数值一致性已逐位核对：930598 收盘 `2734.867` vs 官方 `2734.87`；931582 `1771.5793` vs
`1771.58`；成交量 `2413138701` 与官方 `tradingVol` 完全相同。也就是说雪球转发的就是中证
的数据，用它当主源不是拿精度换配额。

因此分层：**便宜且宽松的雪球在前，权威但稀缺的中证官网在后**。雪球需要 cookie 握手、
是聚合站，随时可能变；中证官网作为兜底保证它变了以后链路不会整条断掉。

## 两个必须踩准的细节

1. **时间戳是北京时间零点**的毫秒数。`1786377600000` 按 UTC 解析得到 2026-08-10，按
   UTC+8 才是 2026-08-11。搞错会让每根 bar 整体偏一天，而下游是拿日期做对齐的。
2. **符号前缀按代码族分**：中证 9xxxxx 与 H 系列用 `CSI` 前缀（`CSI930598`、`CSIH30202`），
   深证 399xxx 用 `SZ`，上证 000xxx 用 `SH`。实测 `CSI399989` 返回 0 行、`SH930598` 也返回
   0 行——前缀弄错了不会报错，只会静默给空，所以这里按族显式映射，不猜。
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from threading import RLock

import requests

logger = logging.getLogger(__name__)

XUEQIU_KLINE_URL = "https://stock.xueqiu.com/v5/stock/chart/kline.json"
XUEQIU_BOOTSTRAP_URL = "https://xueqiu.com/hq"
XUEQIU_TIMEOUT_SECONDS = 10.0
# cookie 有效期未知，到期表现为返回空或 4xx。按这个间隔主动重建会话，另外失败时也会重建。
XUEQIU_SESSION_TTL_SECONDS = 1800.0
# 连续失败到这个次数就短暂熔断，避免对方开始拦我们之后每个调用方都白等一次 RTT。
XUEQIU_FAILURE_THRESHOLD = 5
XUEQIU_COOLDOWN_SECONDS = 300.0

#: 北京时间。雪球的 K 线时间戳是当日北京时间零点。
_CST = timezone(timedelta(hours=8))

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://xueqiu.com/",
}

_CSI_NUMERIC_RE = re.compile(r"^9\d{5}$")
_CSI_H_RE = re.compile(r"^H\d{4,5}$")
_SZ_RE = re.compile(r"^(?:39|15|16)\d{4}$")
_SH_RE = re.compile(r"^(?:000|880|950)\d{3}$")

_SESSION_LOCK = RLock()
_SESSION: requests.Session | None = None
_SESSION_AT: float = 0.0
_FAILURES: int = 0
_BLOCKED_UNTIL: float = 0.0


def xueqiu_index_symbol(index_symbol: str) -> str | None:
    """把指数代码映射成雪球符号；无法确定就返回 None（不猜）。"""
    code = str(index_symbol or "").strip().upper()
    if not code:
        return None
    if _CSI_NUMERIC_RE.match(code) or _CSI_H_RE.match(code):
        return f"CSI{code}"
    if _SZ_RE.match(code):
        return f"SZ{code}"
    if _SH_RE.match(code):
        return f"SH{code}"
    return None


def reset_xueqiu_session_state() -> None:
    """清空会话与熔断状态（测试用）。"""
    global _SESSION, _SESSION_AT, _FAILURES, _BLOCKED_UNTIL
    with _SESSION_LOCK:
        _SESSION = None
        _SESSION_AT = 0.0
        _FAILURES = 0
        _BLOCKED_UNTIL = 0.0


def xueqiu_blocked_seconds_remaining() -> float:
    with _SESSION_LOCK:
        return max(0.0, _BLOCKED_UNTIL - time.monotonic())


def fetch_xueqiu_index_daily_history(
    index_symbol: str,
    trading_days: int = 252,
) -> dict | None:
    """取指数日线；符号无法映射、熔断中或没有数据时返回 ``None``。

    返回结构与 `index_daily_client.fetch_index_daily_history` 一致
    （``{"data": [{"date", "close", ...}], "source": "xueqiu"}``）。
    """

    symbol = xueqiu_index_symbol(index_symbol)
    if symbol is None:
        return None
    days = max(20, min(int(trading_days), 1200))
    if xueqiu_blocked_seconds_remaining() > 0:
        return None

    payload = _request(symbol, days)
    if payload is None:
        # cookie 可能过期了：重建一次会话再试，仍失败就记一次失败并交给下一路。
        _drop_session()
        payload = _request(symbol, days)
    if payload is None:
        _record_failure()
        return None

    data = _rows_from_payload(payload)
    if data is None:
        # 这不是"源坏了"，多半是这个代码雪球没有（前缀族判断兜不住的少数情况）。
        # 不计入熔断，否则一个冷门代码就能把整条源关掉。
        return None
    _record_success()
    return {"data": data[-days:], "source": "xueqiu"}


# --- HTTP 与会话 -------------------------------------------------------------


def _session() -> requests.Session:
    global _SESSION, _SESSION_AT
    with _SESSION_LOCK:
        now = time.monotonic()
        if _SESSION is not None and now - _SESSION_AT < XUEQIU_SESSION_TTL_SECONDS:
            return _SESSION
        session = requests.Session()
        session.headers.update(_HEADERS)
        try:
            session.get(
                XUEQIU_BOOTSTRAP_URL,
                timeout=XUEQIU_TIMEOUT_SECONDS,
                proxies={"http": None, "https": None},
            )
        except Exception as exc:  # noqa: BLE001 - 握手失败仍返回 session，让请求自己报错
            logger.debug("xueqiu session bootstrap failed: %s", exc)
        _SESSION = session
        _SESSION_AT = now
        return session


def _drop_session() -> None:
    global _SESSION, _SESSION_AT
    with _SESSION_LOCK:
        _SESSION = None
        _SESSION_AT = 0.0


def _record_failure() -> None:
    global _FAILURES, _BLOCKED_UNTIL
    with _SESSION_LOCK:
        _FAILURES += 1
        if _FAILURES >= XUEQIU_FAILURE_THRESHOLD:
            _BLOCKED_UNTIL = time.monotonic() + XUEQIU_COOLDOWN_SECONDS
            _FAILURES = 0
            logger.warning(
                "xueqiu index daily failing repeatedly; backing off for %.0fs",
                XUEQIU_COOLDOWN_SECONDS,
            )


def _record_success() -> None:
    global _FAILURES
    with _SESSION_LOCK:
        _FAILURES = 0


def _request(symbol: str, days: int) -> dict | None:
    try:
        response = _session().get(
            XUEQIU_KLINE_URL,
            params={
                "symbol": symbol,
                "begin": int(time.time() * 1000),
                "period": "day",
                "type": "before",
                "count": -days,
                "indicator": "kline",
            },
            timeout=XUEQIU_TIMEOUT_SECONDS,
            proxies={"http": None, "https": None},
        )
    except Exception as exc:  # noqa: BLE001 - 源失败是证据状态，不该冒泡
        logger.warning("xueqiu index daily failed for %s: %s", symbol, exc)
        return None
    if response.status_code != 200:
        logger.warning(
            "xueqiu index daily http %s for %s", response.status_code, symbol
        )
        return None
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("xueqiu index daily payload invalid for %s: %s", symbol, exc)
        return None
    return payload if isinstance(payload, dict) else None


# --- 解析 --------------------------------------------------------------------


def _rows_from_payload(payload: dict) -> list[dict[str, float | str]] | None:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    columns = data.get("column")
    items = data.get("item")
    if not isinstance(columns, list) or not isinstance(items, list) or not items:
        return None
    # 按**列名**取值，不按位置。位置在对方加一列时就会整体错位，而错位的收盘价是
    # 静默的错数据，比取不到更糟。
    index_by_name = {
        str(name): position for position, name in enumerate(columns) if name is not None
    }
    if "timestamp" not in index_by_name or "close" not in index_by_name:
        return None

    rows: list[dict[str, float | str]] = []
    for item in items:
        if not isinstance(item, list):
            continue
        day = _beijing_day(_pick(item, index_by_name, "timestamp"))
        close = _as_float(_pick(item, index_by_name, "close"))
        if not day or close is None or close <= 0:
            continue
        bar: dict[str, float | str] = {"date": day, "close": round(close, 4)}
        for name in ("open", "high", "low"):
            value = _as_float(_pick(item, index_by_name, name))
            if value is not None and value > 0:
                bar[name] = round(value, 4)
        rows.append(bar)

    if len(rows) < 2:
        return None
    rows.sort(key=lambda row: str(row["date"]))
    return rows


def _pick(item: list, index_by_name: dict[str, int], name: str) -> object:
    position = index_by_name.get(name)
    if position is None or position >= len(item):
        return None
    return item[position]


def _beijing_day(value: object) -> str | None:
    """雪球时间戳 → ``YYYY-MM-DD``（**北京时间**，不是 UTC）。"""
    milliseconds = _as_float(value)
    if milliseconds is None:
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000.0, tz=_CST).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


__all__ = [
    "XUEQIU_COOLDOWN_SECONDS",
    "XUEQIU_FAILURE_THRESHOLD",
    "XUEQIU_KLINE_URL",
    "XUEQIU_SESSION_TTL_SECONDS",
    "fetch_xueqiu_index_daily_history",
    "reset_xueqiu_session_state",
    "xueqiu_blocked_seconds_remaining",
    "xueqiu_index_symbol",
]
