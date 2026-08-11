"""中证指数官方日线：`www.csindex.com.cn` 的 index-perf 接口。

为什么需要这一路（2026-08-11 从生产服务器逐源实测）：

`index_daily_client` 原本只有东财 kline 与新浪两条路。东财 kline 从这台机器整体不可用
（`push2his` / `18.push2his` / `49.push2his` 一律 `RemoteDisconnected`），而新浪与腾讯
`gtimg` 只能报**交易所挂牌**的代码：`000xxx` `399xxx` 有数据，`930598`（中证稀土产业）
`931582`（中证数字经济主题）`930713` `931994` 一律返回 0 行——这些中证指数根本没有交易所
行情代码，不是被限流。

后果是所有以中证 9xxxxx / H3xxxx 为基准或板块标的的链路全线静默为空：

* `fund_benchmark_research` 的成分腿全部拿不到序列（当时的 reason 是
  `index:930598_snapshot_envelope_missing`，已改名为 `_provider_returned_no_series`），
  于是每只持仓的基准跟踪指标恒为不可用；
* canonical 日 K 对这些板块取不到真指数序列，只能退到板块资金流**代理**（成分篮子不同）
  或干脆为空。实测注册表 64 个指数类板块里只有 10 个拿得到日线。

中证指数公司自己的接口能报**全部**代码族（含此前判定"无源"的 931672 风电与 930792
港股银行），单次 0.1s 量级，且它是这些指数的**发布方**。补上这一路后同一批 64 个板块
实测 10/64 → 57/64（剩下的是 AU9999 现货与恒生系列，本来就不是中证指数）。

## 它是稀缺资源，必须省着用

同日三次实测：突发约 50 次后该站返回 **403**；把节奏放慢到 1.5s/次也只多撑到第 33 次；
第一次 403 后 3 分钟仍被拦、约 25 分钟后恢复，随后又只放行 33 次。看起来是**每小时
40~50 次的滚动配额**而不是每秒速率限制。所以这一路不能当普通 HTTP 源随便打，本模块围绕
三件事组织：

1. **持久缓存**：日线收盘每个交易日只变一次。一次取满长窗口（默认 800 个交易日）落
   `sector_quote_cache`，短窗口请求从同一份缓存里裁出来，同一交易日不再回源。
2. **限速**：全局串行 + 最小间隔，并对滚动窗口内的**实际回源次数**设上限。超预算时
   直接返回 None（等价于"这一路暂时没有"），由调用方继续兜底，绝不排队堆积。
3. **熔断**：一旦拿到 403/429 就在较长冷却期内完全不再发请求。被限流后继续打既不会
   更快恢复，还会把每个调用方拖上一次 RTT。

因此它在 `index_daily_client` 里排在**最后**：新浪能报的 `000xxx`/`399xxx` 走新浪，
不消耗中证配额；只有别的源都拿不到时才动用它。

接口约定（实测）：
* `GET /csindex-home/perf/index-perf?indexCode=930598&startDate=20250101&endDate=20260811`
* 返回 `{"code": "200", "msg": "Success", "data": [{"tradeDate": "20260811",
  "close": 2734.87, "open": ..., "high": ..., "low": ..., ...}]}`
* 未知代码返回 `code=200` 且 `data=[]`（干净的空，不抛错）。
* 不需要 Referer。
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from threading import RLock

import requests

logger = logging.getLogger(__name__)

CSINDEX_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
CSINDEX_TIMEOUT_SECONDS = 8.0
CSINDEX_CACHE_KEY_PREFIX = "csindex:daily:v1"

# 一次取满这么多个交易日并落缓存，短窗口请求从同一份里裁。请求成本与窗口长度基本无关
# （两年 485 行实测 0.22s），所以一次就取长的，把稀缺的请求次数换成尽量多的数据。
CSINDEX_CAPTURE_TRADING_DAYS = 800
# 全局最小回源间隔。
CSINDEX_MIN_INTERVAL_SECONDS = 2.0
# 滚动窗口内允许的**实际回源**次数上限，超过就当这一路暂时没有。
#
# 这个数字是实测标定的，不是拍的。2026-08-11 三次实验：
#   * 突发（~10 次/秒）：约第 50 次后 403；
#   * 1.5s/次（~0.67 次/秒）：第 33 次后 403 —— 放慢节奏并没换来更多次数；
#   * 第一次 403 后 3 分钟仍被拦，约 25 分钟后单次请求恢复，随后又只放行了 33 次。
# 三点拼起来更像**每小时 40~50 次的滚动配额**，而不是每秒速率限制。所以按 6 次 / 10 分钟
# （36 次/小时）设限，留出余量：宁可让缓存分多轮慢慢焐热，也不要一轮打满换来半小时全黑。
#
# 代价要说清楚：冷缓存下一份持仓 6 只的日报最多需要 12 个中证代码（板块 + 基准腿），
# 会跨两个窗口才补齐；但日线收盘一个交易日只变一次，缓存一旦焐热当天后续全部零请求。
# 真正的解法是每个交易日跑一次预热任务（仓库已有 GitHub Actions 的 capture 工作流可挂），
# 让请求路径永远命中缓存——那属于调度层改动，不在本次修复范围内。
CSINDEX_LIVE_WINDOW_SECONDS = 600.0
CSINDEX_MAX_LIVE_PER_WINDOW = 6
# 被限流后的冷却期。403 实测是粘性的（每 20s 探一次、连续 3 分钟仍是 403；再测时已在
# 25 分钟内恢复），继续打不会更快恢复。取 30 分钟稳稳越过观察到的窗口——多等的代价只是
# 继续用缓存里的旧序列，而抢着重试的代价是把整个 IP 继续按在限流里。
CSINDEX_BLOCK_COOLDOWN_SECONDS = 1800.0
# 缓存里还没有当日收盘时，两次回源尝试之间至少间隔这么久（收盘数据发布有延迟，
# 否则收盘前每次调用都会白打一次）。
CSINDEX_NEGATIVE_TTL_SECONDS = 900.0
# 熔断状态跨进程共享的 key。限流是**按 IP** 的，而生产上 api 与 worker 是两个容器、
# 共用同一出口 IP：只在进程内记熔断，等于让另一个容器接着打，把冷却期无限续上。
CSINDEX_BREAKER_CACHE_KEY = f"{CSINDEX_CACHE_KEY_PREFIX}:__breaker__"
# 共享熔断/日线快照读持久行的复验间隔。这两处只在"要发请求"或"缓存不是最新"时才读，
# 频率本来就低（每进程每窗口最多 6 次），所以窗口取小一点换取尽量新的共享状态。
CSINDEX_BREAKER_REVALIDATE_SECONDS = 10.0
CSINDEX_SNAPSHOT_REVALIDATE_SECONDS = 30.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.csindex.com.cn/",
}

# 只认「中证指数代码」形态：6 位数字（000300 / 399989 / 930598 / 931672）或 H + 4~5 位
# （H30202 / H11043）。带交易所前缀的符号（`sh600519`、`sz000001`）与东财 secid
# （`90.BK0727`、`118.AU9999`）都不匹配——这很重要：`sector_constituent_proxy` 传进来的
# 是**个股** `sh`/`sz` 前缀符号，裸 6 位数字在那条路上不会出现，所以不存在把 000001
# （平安银行）错当上证指数的可能。
_CSINDEX_CODE_RE = re.compile(r"^(?:\d{6}|H\d{4,5})$")

_RATE_LOCK = RLock()
_LIVE_CALL_TIMES: deque[float] = deque()
_LAST_CALL_AT: float = 0.0
_BLOCKED_UNTIL: float = 0.0
_LAST_ATTEMPT_BY_CODE: dict[str, float] = {}


def is_csindex_code(index_symbol: str) -> bool:
    """该符号是否可能是中证指数代码（据此决定要不要走官方接口）。"""
    return bool(_CSINDEX_CODE_RE.match(str(index_symbol or "").strip().upper()))


def reset_csindex_rate_state() -> None:
    """清空限速/熔断状态（测试用）。"""
    global _LAST_CALL_AT, _BLOCKED_UNTIL
    with _RATE_LOCK:
        _LIVE_CALL_TIMES.clear()
        _LAST_ATTEMPT_BY_CODE.clear()
        _LAST_CALL_AT = 0.0
        _BLOCKED_UNTIL = 0.0


def csindex_blocked_seconds_remaining() -> float:
    """熔断剩余秒数（0 表示未熔断）。"""
    with _RATE_LOCK:
        return max(0.0, _BLOCKED_UNTIL - time.monotonic())


def fetch_csindex_daily_history(
    index_symbol: str,
    trading_days: int = 252,
) -> dict | None:
    """取中证指数官方日线收盘；不是中证代码、被限流或没有数据时返回 ``None``。

    返回结构与 `index_daily_client.fetch_index_daily_history` 一致
    （``{"data": [{"date", "close", ...}], "source": "csindex"}``），额外带上
    ``open``/``high``/``low``，方便将来需要当日振幅的消费方。
    """

    code = str(index_symbol or "").strip().upper()
    if not is_csindex_code(code):
        return None
    days = max(20, min(int(trading_days), CSINDEX_CAPTURE_TRADING_DAYS))

    cached = _load_cached(code)
    if cached is not None and _cached_is_current(cached):
        return _view(cached, days)

    if not _should_attempt_live(code):
        # 缓存不是最新，但这一路现在不该回源（限流/熔断/负缓存）。有旧数据就先给旧的，
        # 它的 `date` 自己说明了截止到哪天；没有就如实为空。
        return _view(cached, days) if cached is not None else None

    fetched = _fetch_live(code)
    if fetched is None:
        return _view(cached, days) if cached is not None else None
    _save_cached(code, fetched)
    return _view(fetched, days)


# --- 限速与熔断 --------------------------------------------------------------


def _should_attempt_live(code: str) -> bool:
    now = time.monotonic()
    with _RATE_LOCK:
        if _BLOCKED_UNTIL > now:
            return False
        while _LIVE_CALL_TIMES and now - _LIVE_CALL_TIMES[0] > CSINDEX_LIVE_WINDOW_SECONDS:
            _LIVE_CALL_TIMES.popleft()
        if len(_LIVE_CALL_TIMES) >= CSINDEX_MAX_LIVE_PER_WINDOW:
            logger.debug("csindex live budget exhausted, skipping %s", code)
            return False
        last_attempt = _LAST_ATTEMPT_BY_CODE.get(code)
        if last_attempt is not None and now - last_attempt < CSINDEX_NEGATIVE_TTL_SECONDS:
            return False
    # 本进程的限速都放行了，再看别的容器有没有已经把这个 IP 打进限流。只在真要发请求前
    # 查一次（因此每进程每窗口最多 8 次单行主键查询），代价可以忽略。
    return not _durable_breaker_is_open()


def _throttle_and_register(code: str) -> None:
    global _LAST_CALL_AT
    while True:
        with _RATE_LOCK:
            now = time.monotonic()
            wait = CSINDEX_MIN_INTERVAL_SECONDS - (now - _LAST_CALL_AT)
            if wait <= 0:
                _LAST_CALL_AT = now
                _LIVE_CALL_TIMES.append(now)
                _LAST_ATTEMPT_BY_CODE[code] = now
                while len(_LAST_ATTEMPT_BY_CODE) > 512:
                    _LAST_ATTEMPT_BY_CODE.pop(next(iter(_LAST_ATTEMPT_BY_CODE)))
                return
        time.sleep(min(wait, CSINDEX_MIN_INTERVAL_SECONDS))


def _open_breaker(reason: str) -> None:
    global _BLOCKED_UNTIL
    with _RATE_LOCK:
        _BLOCKED_UNTIL = time.monotonic() + CSINDEX_BLOCK_COOLDOWN_SECONDS
    logger.warning(
        "csindex rate limited (%s); backing off for %.0fs",
        reason,
        CSINDEX_BLOCK_COOLDOWN_SECONDS,
    )
    _publish_durable_breaker()


def _publish_durable_breaker() -> None:
    try:
        from app.services.sector_quote_cache import save_spot_snapshot

        save_spot_snapshot(
            CSINDEX_BREAKER_CACHE_KEY,
            {
                "blocked_until_epoch": time.time() + CSINDEX_BLOCK_COOLDOWN_SECONDS,
                "opened_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:  # noqa: BLE001 - 共享熔断只是加固，落不下去不该影响本次判定
        logger.debug("csindex breaker publish failed", exc_info=True)


def _durable_breaker_is_open() -> bool:
    global _BLOCKED_UNTIL
    try:
        from app.services.sector_quote_cache import get_spot_snapshot_revalidated

        # 必须是**复验**读：`get_spot_snapshot_any_age` 命中进程内存后就再也不回持久行，
        # 于是本进程一旦读到某一版熔断记录就把它钉死——等它过期后，别的容器后来新开的
        # 熔断永远看不见，共享熔断退化成"每进程一次性"，等于没有。
        payload = get_spot_snapshot_revalidated(
            CSINDEX_BREAKER_CACHE_KEY,
            memory_ttl_seconds=CSINDEX_BREAKER_REVALIDATE_SECONDS,
        )
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(payload, dict):
        return False
    try:
        blocked_until = float(payload.get("blocked_until_epoch") or 0.0)
    except (TypeError, ValueError):
        return False
    remaining = blocked_until - time.time()
    if remaining <= 0:
        return False
    # 把别的容器发现的限流搬进本进程，之后连这一次查询都省了。
    with _RATE_LOCK:
        _BLOCKED_UNTIL = max(_BLOCKED_UNTIL, time.monotonic() + remaining)
    logger.debug("csindex breaker open elsewhere, %.0fs remaining", remaining)
    return True


def _fetch_live(code: str) -> dict | None:
    _throttle_and_register(code)
    span_days = int(CSINDEX_CAPTURE_TRADING_DAYS * 1.6) + 30
    end = date.today() + timedelta(days=5)
    start = date.today() - timedelta(days=span_days)
    try:
        response = requests.get(
            CSINDEX_URL,
            params={
                "indexCode": code,
                "startDate": start.strftime("%Y%m%d"),
                "endDate": end.strftime("%Y%m%d"),
            },
            headers=_HEADERS,
            timeout=CSINDEX_TIMEOUT_SECONDS,
            proxies={"http": None, "https": None},
        )
    except Exception as exc:  # noqa: BLE001 - 源失败是证据状态，不该冒泡
        logger.warning("csindex index daily failed for %s: %s", code, exc)
        return None

    if response.status_code in {403, 429}:
        _open_breaker(f"http {response.status_code}")
        return None
    try:
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("csindex index daily failed for %s: %s", code, exc)
        return None

    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        return None

    data: list[dict[str, float | str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        day = _iso_day(str(row.get("tradeDate") or ""))
        close = _as_float(row.get("close"))
        if not day or close is None or close <= 0:
            continue
        bar: dict[str, float | str] = {"date": day, "close": round(close, 4)}
        for key in ("open", "high", "low"):
            value = _as_float(row.get(key))
            if value is not None and value > 0:
                bar[key] = round(value, 4)
        data.append(bar)

    if len(data) < 2:
        return None
    data.sort(key=lambda item: str(item["date"]))
    data = data[-CSINDEX_CAPTURE_TRADING_DAYS:]
    return {
        "data": data,
        "source": "csindex",
        "data_end_date": str(data[-1]["date"]),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


# --- 持久缓存 ----------------------------------------------------------------


def _cache_key(code: str) -> str:
    return f"{CSINDEX_CACHE_KEY_PREFIX}:{code}"


def _load_cached(code: str) -> dict | None:
    try:
        from app.services.sector_quote_cache import get_spot_snapshot_revalidated

        # 复验读而不是 `any_age`：请求配额本来就稀缺（6 次/10 分钟），如果本进程把昨天的
        # 序列钉死在内存里，就看不到别的容器/采集任务今天已经写好的行，白白再花一次配额
        # 去取同一份数据——甚至因为熔断而彻底取不到。
        payload = get_spot_snapshot_revalidated(
            _cache_key(code),
            memory_ttl_seconds=CSINDEX_SNAPSHOT_REVALIDATE_SECONDS,
        )
    except Exception:  # noqa: BLE001 - 缓存只是优化，坏了不该拦住回源
        return None
    if not isinstance(payload, dict):
        return None
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    return payload


def _save_cached(code: str, payload: dict) -> None:
    try:
        from app.services.sector_quote_cache import save_spot_snapshot

        save_spot_snapshot(_cache_key(code), payload)
    except Exception:  # noqa: BLE001 - 落盘失败不该影响本次结果
        logger.debug("csindex snapshot persist failed for %s", code, exc_info=True)


def _cached_is_current(payload: dict) -> bool:
    try:
        from app.services.trading_session import get_effective_trade_date

        expected = get_effective_trade_date()
    except Exception:  # noqa: BLE001
        return False
    end_date = str(payload.get("data_end_date") or "")[:10]
    if not end_date:
        rows = payload.get("data") or []
        end_date = str((rows[-1] if rows else {}).get("date") or "")[:10]
    return bool(end_date) and end_date >= str(expected)[:10]


def _view(payload: dict, days: int) -> dict | None:
    rows = [row for row in (payload.get("data") or []) if isinstance(row, dict)]
    if len(rows) < 2:
        return None
    return {"data": rows[-days:], "source": str(payload.get("source") or "csindex")}


# --- 小工具 ------------------------------------------------------------------


def _iso_day(value: str) -> str | None:
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
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
    "CSINDEX_BLOCK_COOLDOWN_SECONDS",
    "CSINDEX_CACHE_KEY_PREFIX",
    "CSINDEX_MAX_LIVE_PER_WINDOW",
    "CSINDEX_MIN_INTERVAL_SECONDS",
    "CSINDEX_TIMEOUT_SECONDS",
    "CSINDEX_URL",
    "csindex_blocked_seconds_remaining",
    "fetch_csindex_daily_history",
    "is_csindex_code",
    "reset_csindex_rate_state",
]
