from __future__ import annotations

"""量价背离信号回测（M1.3）。

设计文档：docs/superpowers/specs/2026-07-02-ai-decision-sharpening-design.md 第 M1.3 节。

把"当日资金流方向 vs 涨跌方向背离"（`sector_fund_flow_context._classify_flow_pattern`
判定出的 distribution/accumulation 模式）纳入 T→T+1 历史回测，复用
`signal_backtest_stats.py` 抽出的"自然基准 + edge + 触发数门槛"统计口径（与
`sector_signal_backtest.py` 同源，避免两套阈值/口径漂移）。

**M1.2（板块历史资金流窗口）落地方式：** `board_fund_flow_history.fetch_board_flow_series`
用 `lmt=0` 已返回东财允许的全部历史（`get_cached_board_flow_series` 直接可复用，无需新增
抓取逻辑）。本模块新增的是"K线与资金流按日期对齐"的工具函数 `_align_kline_and_flow`
（本节 M1.2 描述的"批量预热 + 对齐"里，"对齐"部分即此函数；"批量预热"沿用
`board_fund_flow_history.prefetch_board_flow_histories` 现有能力，未新增）。

**诚实划界：** 本项目沙箱环境出站网络到东财 `push2his.eastmoney.com` 全部 3 个 host
均被阻断（`Server disconnected`，与其他外网连通性对比排除是网络整体不可用），无法在
开发环境实测 `fetch_board_flow_series` 真实能回溯的交易日天数。M6 上线前必须在能访问
东财的环境（如生产/预发布）里跑一次 `debug_probe` 式验证，确认历史窗口是否达到本模块
建议的 ``lookback_days=100`` ——不足时代码会用 `sample_days` 诚实反映实际对齐后的样本量，
样本不足 30 次触发时 `significant` 会天然为 False（不会伪造显著性）。

**为何拆两条规则而非设计原稿建议的单一 `flow_price_divergence`：** distribution（涨但
流出，预测"次日下跌或走平"）与 accumulation（跌但流入，预测"次日上涨"）预测方向相反，
合并成一个 hit_rate 桶在统计上没有意义（正确率无法解读）。这里保持与
`sector_signal_backtest.py::by_rule` 完全一致的桶结构（`trigger_count/hit_rate_percent/
baseline_rate_percent/edge_percent/significant`），只是拆成 `flow_price_distribution` /
`flow_price_accumulation` 两个 rule_id，`signal_confidence.py::score_signal` 与前端
`SectorSignalBacktestPanel` 可直接复用、无需改动。
"""

import time
from typing import Any

from app.config import get_settings
from app.services.board_fund_flow_history import (
    get_cached_board_flow_series,
    resolve_board_flow_code_for_sector,
)
from app.services.eastmoney_trends_client import DailyKlineBar
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series
from app.services.sector_fund_flow_context import _classify_flow_pattern
from app.services.sector_labels import normalize_sector_label
from app.services.sector_signal_rules import prediction_matches
from app.services.signal_backtest_stats import (
    direction_fractions,
    finalize_bucket,
    new_bucket,
    record_trigger,
)

DIVERGENCE_RULE_IDS = ("flow_price_distribution", "flow_price_accumulation")

_RULE_LABELS = {
    "flow_price_distribution": "量价背离-高位出货（涨但资金流出）",
    "flow_price_accumulation": "量价背离-低位吸筹（跌但资金流入）",
}

_BACKTEST_RESPONSE_TTL_SECONDS = 86400
_BACKTEST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _align_kline_and_flow(
    kline_series: list[DailyKlineBar],
    flow_series: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按日期内连接（inner join）K 线涨跌幅与板块主力净流入，仅保留双方都有数据的交易日。"""
    flow_by_date: dict[str, dict[str, Any]] = {}
    for row in flow_series:
        day = str(row.get("date") or "")[:10]
        if day:
            flow_by_date[day] = row

    merged: list[dict[str, Any]] = []
    for bar in kline_series:
        day = str(bar.get("date") or "")[:10]
        if not day:
            continue
        flow_row = flow_by_date.get(day)
        if flow_row is None:
            continue
        change_percent = bar.get("change_percent")
        main_force = flow_row.get("main_force_net_yi")
        if change_percent is None or main_force is None:
            continue
        try:
            merged.append(
                {
                    "date": day,
                    "change_percent": float(change_percent),
                    "main_force_net_yi": float(main_force),
                }
            )
        except (TypeError, ValueError):
            continue

    merged.sort(key=lambda row: row["date"])
    return merged


def backtest_flow_price_divergence(
    board_code: str,
    kline_series: list[DailyKlineBar],
    flow_series: list[dict[str, Any]],
    *,
    lookback_days: int = 100,
) -> dict[str, Any]:
    """按日期对齐 K 线与资金流，对 distribution/accumulation 模式做 T→T+1 回测。

    纯函数：不做网络请求/缓存，便于单测注入已知序列验证统计口径。
    """
    aligned = _align_kline_and_flow(kline_series, flow_series)
    window = max(30, min(lookback_days, 400))
    # +1：需要保留窗口内最后一天的「下一天」用于验证 T+1 结果。
    if len(aligned) > window + 1:
        aligned = aligned[-(window + 1) :]

    if len(aligned) < 3:
        return {
            "board_code": board_code,
            "resolved": True,
            "sample_days": len(aligned),
            "message": "对齐后的有效交易日不足，无法回测。",
            "by_rule": {},
        }

    distribution_bucket = new_bucket(
        "flow_price_distribution", _RULE_LABELS["flow_price_distribution"]
    )
    accumulation_bucket = new_bucket(
        "flow_price_accumulation", _RULE_LABELS["flow_price_accumulation"]
    )

    next_changes = [aligned[i + 1]["change_percent"] for i in range(len(aligned) - 1)]
    fracs = direction_fractions(next_changes)

    for index in range(len(aligned) - 1):
        cur = aligned[index]
        nxt = aligned[index + 1]
        pattern = _classify_flow_pattern(
            sector_return_percent=cur["change_percent"],
            today_flow=cur["main_force_net_yi"],
            cumulative_5d=None,
            flow_tiers=None,
        )
        label = pattern.get("pattern_label")
        if label == "distribution":
            hit = prediction_matches("down_or_flat", nxt["change_percent"])
            record_trigger(distribution_bucket, prediction="down_or_flat", fracs=fracs, hit=hit)
        elif label == "accumulation":
            hit = prediction_matches("up", nxt["change_percent"])
            record_trigger(accumulation_bucket, prediction="up", fracs=fracs, hit=hit)

    finalize_bucket(distribution_bucket)
    finalize_bucket(accumulation_bucket)

    by_rule: dict[str, dict[str, Any]] = {}
    if distribution_bucket["trigger_count"] > 0:
        by_rule["flow_price_distribution"] = distribution_bucket
    if accumulation_bucket["trigger_count"] > 0:
        by_rule["flow_price_accumulation"] = accumulation_bucket

    return {
        "board_code": board_code,
        "resolved": True,
        "sample_days": len(aligned),
        "by_rule": by_rule,
    }


def _default_fetch_kline(sector_label: str) -> list[DailyKlineBar]:
    canon = get_canonical_sector(sector_label)
    if canon is None:
        return []
    return fetch_canonical_daily_kline_series(canon, max_days=400, timeout=10.0)


def _default_fetch_flow(sector_label: str) -> tuple[str | None, list[dict[str, Any]]]:
    board_code, _resolved_label = resolve_board_flow_code_for_sector(sector_label)
    if not board_code:
        return None, []
    return board_code, get_cached_board_flow_series(board_code)


def _cache_key(sector_label: str, lookback_days: int) -> str:
    return f"flow_divergence:{sector_label}:{lookback_days}"


def build_sector_flow_divergence_backtest(
    sector_label: str | None,
    *,
    lookback_days: int = 100,
    fetch_kline=None,
    fetch_flow=None,
) -> dict[str, Any]:
    """按板块名解析 canonical K 线 + 资金流历史，做 T→T+1 量价背离回测（带 24h 缓存）。

    `fetch_kline`/`fetch_flow` 可注入，便于离线测试；生产路径默认走
    `sector_canonical`/`board_fund_flow_history` 现有解析与缓存。
    """
    settings = get_settings()
    if not settings.flow_divergence_backtest_enabled:
        return {
            "enabled": False,
            "resolved": False,
            "by_rule": {},
            "message": "量价背离回测已关闭（FUND_AI_FLOW_DIVERGENCE_BACKTEST_ENABLED=false）。",
        }

    label = normalize_sector_label(sector_label)
    if not label:
        return {"enabled": True, "resolved": False, "by_rule": {}, "message": "板块名为空"}

    injected = fetch_kline is not None or fetch_flow is not None
    cache_key = _cache_key(label, lookback_days)
    if not injected:
        now = time.time()
        cached = _BACKTEST_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < _BACKTEST_RESPONSE_TTL_SECONDS:
            return cached[1]

    kline_fetcher = fetch_kline or _default_fetch_kline
    kline_series = kline_fetcher(label)
    if not kline_series:
        return {
            "enabled": True,
            "resolved": False,
            "by_rule": {},
            "message": "无 canonical K 线映射或拉取失败，已跳过背离回测。",
        }

    if fetch_flow is not None:
        board_code, flow_series = fetch_flow(label)
    else:
        board_code, flow_series = _default_fetch_flow(label)
    if not board_code or not flow_series:
        return {
            "enabled": True,
            "resolved": False,
            "by_rule": {},
            "message": "未解析到板块资金流代码或历史资金流为空，已跳过背离回测。",
        }

    result = backtest_flow_price_divergence(
        board_code,
        kline_series,
        flow_series,
        lookback_days=lookback_days,
    )
    result["enabled"] = True
    result["sector_label"] = label

    if not injected and result.get("by_rule"):
        _BACKTEST_CACHE[cache_key] = (time.time(), result)
    return result
