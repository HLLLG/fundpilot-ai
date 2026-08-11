from __future__ import annotations

import logging

from app.services.akshare_subprocess import (
    fetch_board_daily_kline_series,
    fetch_index_daily_history as fetch_index_daily_via_akshare,
)
from app.services.index_daily_client import (
    fetch_index_daily_history as fetch_index_daily_via_index_client,
)
from app.services.eastmoney_trends_client import (
    DailyKlineBar,
    fetch_eastmoney_daily_kline_series,
)
from app.services.sector_canonical import CanonicalSector
from app.services.sector_quote_relay_provider import fetch_daily_kline_via_relay

logger = logging.getLogger(__name__)


def daily_bars_from_board_flow_series(
    flow_series: list[dict] | None,
    *,
    max_days: int | None = None,
) -> list[DailyKlineBar]:
    """把板块资金流日线转成日 K bar（只需要 `date` 与 `change_percent`）。

    这是**唯一实现**，`sector_flow_divergence_backtest` 复用同一份，避免两处各写一遍再漂移。

    资金流日线每行都带 `change_percent` 与 `close_price`，而且它是零网络的既有缓存
    （`board_fund_flow_history`）。缺 `high_change_percent`——但新浪指数那条兜底
    （`_index_history_to_daily_bars`）同样给 None，所以依赖当日最高价的规则本来就取不到，
    这里不构成新的退化。
    """
    bars: list[DailyKlineBar] = []
    for row in flow_series or []:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date") or "")[:10]
        change = row.get("change_percent")
        if not day or change is None:
            continue
        try:
            change_percent = float(change)
        except (TypeError, ValueError):
            continue
        close = row.get("close_price")
        try:
            close_value = float(close) if close is not None else None
        except (TypeError, ValueError):
            close_value = None
        bars.append(
            {
                "date": day,
                "change_percent": change_percent,
                "high_change_percent": None,
                "close": close_value,
                "source": "eastmoney_board_fund_flow_daily",
            }
        )
    bars.sort(key=lambda bar: str(bar.get("date") or ""))
    if max_days is not None and len(bars) > max_days:
        bars = bars[-max_days:]
    return bars


def _hk_index_daily_bars(
    canon: CanonicalSector,
    *,
    max_days: int,
) -> list[DailyKlineBar]:
    """恒生系列日线：只用板块自己的 `source_code` 当 symbol，取不到就空。"""
    try:
        from app.services.akshare_subprocess import fetch_hk_index_daily_history

        payload = fetch_hk_index_daily_history(
            str(canon.source_code), trading_days=max_days + 5
        )
    except Exception:  # noqa: BLE001 - 兜底源失败不该冒泡
        logger.debug("hk index daily failed for %s", canon.label, exc_info=True)
        return []
    if not payload:
        return []
    return _index_history_to_daily_bars(payload, max_days=max_days)


def _board_flow_daily_bars(
    canon: CanonicalSector,
    *,
    max_days: int,
) -> list[DailyKlineBar]:
    """按板块名解析 BK 码并取资金流日线，转成日 K。取不到就空。"""
    try:
        from app.services.board_fund_flow_history import (
            get_cached_board_flow_series,
            resolve_board_flow_code_for_sector,
        )

        board_code, _resolved = resolve_board_flow_code_for_sector(canon.label)
        if not board_code:
            return []
        return daily_bars_from_board_flow_series(
            get_cached_board_flow_series(board_code),
            max_days=max_days,
        )
    except Exception:  # noqa: BLE001 - 兜底源失败不该冒泡
        logger.debug("board flow daily bars failed for %s", canon.label, exc_info=True)
        return []


def fetch_canonical_daily_kline_series(
    canon: CanonicalSector,
    *,
    max_days: int = 20,
    timeout: float = 4.0,
    allow_akshare: bool = True,
) -> list[DailyKlineBar]:
    """板块日 K：概念/行业走同源资金流日线 → 中证官方/东财/新浪指数 → 东财 → relay → AkShare → 资金流代理。

    ``allow_akshare=False`` 时跳过 AkShare 子进程兜底（每板块一次子进程，主题板块
    批量刷新 ~100 板块时会非常慢），只走快速 HTTP 源（东财 push2delay + relay + 新浪指数）。

    **为什么把资金流日线放进来**（2026-08-11 线上实测）：东财 kline 端点从生产服务器整体不可用
    （push2his / 79.push2 / push2 直接 TCP 断连，push2delay 返回 200 但 klines=0，48 种
    主机×ut×参数组合全部失败）；relay 未配置返回空；akshare 指数与板块日 K 双双超时返回空；
    新浪只覆盖沪深挂牌的 000xxx/399xxx 指数。结果是 `canonical 日K` 对「软件(H30202)」
    「煤炭」「黄金(AU9999)」等**全部返回空**，只有「医疗(399989)」靠新浪活着。

    连带后果：板块信号回测 6 个持仓板块里 5 个 `rules=0 sample=0`，量价背离回测 1/6，
    而它们都是 `confidence` 升级判定的输入。资金流日线实测覆盖白名单 77 个板块中的 71 个
    （≥30 根），且零网络。

    **中证官方接口补位后**（同日实测）：`www.csindex.com.cn` 的 index-perf 能报全部中证
    代码族，包括此前判定"确实无源"的 931672（风电）与 930792（港股银行）。指数类主题因此
    重新拿到**真指数**序列，资金流日线退回它本来的角色——只给概念/行业当同源首选，以及给
    实在拿不到真指数的板块当明确标注的代理。
    """
    days = max(8, min(max_days, 400))

    # 概念/行业板块：canonical 本身就是那个 BK 板块，资金流日线是**同源**首选，不是代理。
    if canon.source_type in {"concept", "industry"}:
        same_source = _board_flow_daily_bars(canon, max_days=days)
        if len(same_source) >= 8:
            return same_source

    # 恒生系列：secid 前缀 124 表示港股指数，新浪港股日线可用（实测 HSI/HSTECH/HSCEI 均能取到）。
    # 这里**只认板块自己的 source_code**，不拿 HSI 当港股各子板块的替身——那正是
    # 「BK0727 冒充中证医疗」那类错配，宁可这一路为空并如实缺席。
    if str(canon.eastmoney_secid or "").startswith("124.") and canon.source_code:
        hk_bars = _hk_index_daily_bars(canon, max_days=days)
        if len(hk_bars) >= 8:
            return hk_bars

    # 指数类主题：先走指数客户端（中证官方 → 东财 → 新浪）。中证官方接口是这些指数的
    # 发布方，也是唯一能报没有交易所行情代码的 9xxxxx / H3xxxx 的源，0.1s 量级。
    # 它排在 akshare 之前是为了不再为已死的 akshare 指数白等 ~5s。
    if canon.source_type == "index" and canon.source_code:
        index_hist = fetch_index_daily_via_index_client(
            canon.source_code, trading_days=days + 5
        )
        if index_hist:
            converted = _index_history_to_daily_bars(index_hist, max_days=days)
            if converted:
                logger.debug(
                    "canonical daily kline via index client (%s) for %s",
                    index_hist.get("source"),
                    canon.label,
                )
                return converted

        if allow_akshare:
            index_hist = fetch_index_daily_via_akshare(
                canon.source_code, trading_days=days + 5
            )
            if index_hist:
                converted = _index_history_to_daily_bars(index_hist, max_days=days)
                if converted:
                    logger.debug(
                        "canonical daily kline via akshare index for %s",
                        canon.label,
                    )
                    return converted

    series = fetch_eastmoney_daily_kline_series(
        canon.eastmoney_secid,
        source_code=canon.source_code,
        max_days=days,
        timeout=timeout,
        max_retries=1,
    )
    if series:
        return series

    relay_series = fetch_daily_kline_via_relay(
        canon.eastmoney_secid,
        source_code=canon.source_code,
        max_days=days,
        timeout_seconds=max(timeout * 2, 8.0),
    )
    if relay_series:
        return relay_series

    if allow_akshare and canon.source_type in {"concept", "industry"}:
        fallback = fetch_board_daily_kline_series(
            canon.source_type,
            canon.source_name,
            source_code=canon.source_code,
            max_days=days,
        )
        if fallback:
            return fallback

    # 最后兜底：指数类主题的真指数源全都拿不到时，用同名板块的资金流日线做**代理**。
    # 成分篮子与中证指数不同（口径差异已由 bar 上的 `source` 标出），但"有一个可比的日线
    # 序列"对回测类消费方远好于"整条空"——空了那些规则连样本都没有，等于这一路证据消失。
    proxy = _board_flow_daily_bars(canon, max_days=days)
    if len(proxy) >= 8:
        logger.info(
            "canonical daily kline falling back to board flow proxy for %s "
            "(index sources unavailable)",
            canon.label,
        )
        return proxy
    return []


def _index_history_to_daily_bars(
    index_hist: dict,
    *,
    max_days: int,
) -> list[DailyKlineBar]:
    rows = index_hist.get("data") or []
    bars: list[DailyKlineBar] = []
    prior_close: float | None = None
    for row in rows:
        day = str(row.get("date", ""))[:10]
        close = _as_float(row.get("close"))
        if not day or close is None or close <= 0:
            continue
        if prior_close is None or prior_close <= 0:
            prior_close = close
            continue
        change = round((close / prior_close - 1) * 100, 4)
        bars.append(
            {
                "date": day,
                "change_percent": change,
                "high_change_percent": None,
                "close": close,
            }
        )
        prior_close = close

    if len(bars) > max_days:
        bars = bars[-max_days:]
    return bars


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
