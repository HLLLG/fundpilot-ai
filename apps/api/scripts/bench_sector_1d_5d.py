"""板块 1d / 5d 涨跌数据链路耗时调研（本地一次性脚本）。"""
from __future__ import annotations

import statistics
import time

from app.services.discovery_sector_heat import (
    _fetch_sector_5d_change,
    _labels_for_5d_kline_fetch,
    _merge_5d_kline_into_rows,
    _rows_from_theme_board_snapshot,
    build_sector_heat_ranking,
)
from app.services.eastmoney_trends_client import (
    fetch_eastmoney_daily_kline_series,
    fetch_eastmoney_kline_close_percent,
)
from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series
from app.services.sector_registry import list_theme_board_labels
from app.services.theme_board_snapshot import (
    get_theme_board_snapshot,
    list_theme_board_universe,
    refresh_theme_board_snapshot,
)
from app.services.trading_session import build_trading_session


def _ms(seconds: float) -> float:
    return round(seconds * 1000, 1)


def _pct(n: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{round(n / total * 100, 1)}%"


def bench_1d_sources() -> None:
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    universe = list_theme_board_universe()
    sample = universe[:5]

    print("\n=== 1d 数据源探测（东财 push2delay K 线 / 现货榜） ===")
    print(f"effective_trade_date: {trade_date}")
    print(f"主题板块白名单: {len(list_theme_board_labels())} 个，已解析 universe: {len(universe)} 个")

    # 单板块 1d：东财 kline close percent
    singles: list[float] = []
    for entry in sample:
        t0 = time.perf_counter()
        change = fetch_eastmoney_kline_close_percent(
            entry["secid"],
            source_code=entry.get("source_code"),
            trade_date=trade_date,
            timeout=8.0,
        )
        elapsed = time.perf_counter() - t0
        singles.append(elapsed)
        print(
            f"  [1d API] {entry['sector_label']:8s} secid={entry['secid']:12s} "
            f"change={change!s:>8}  {_ms(elapsed):>7} ms"
        )
    print(
        f"  单板块 1d (fetch_eastmoney_kline_close_percent) "
        f"median={_ms(statistics.median(singles))} ms  n={len(singles)}"
    )

    # 主题快照读缓存（荐基实际路径）
    t0 = time.perf_counter()
    snap = get_theme_board_snapshot(force_refresh=False, sort="change")
    cached_ms = _ms(time.perf_counter() - t0)
    items = snap.get("items") or []
    with_1d = sum(1 for i in items if i.get("change_1d_percent") is not None)
    print(
        f"\n  [荐基 1d 路径] get_theme_board_snapshot(cache) "
        f"{cached_ms} ms | items={len(items)} 有1d={with_1d} ({_pct(with_1d, len(items))})"
    )

    # 冷刷新 67 板块 1d（后台 refresh_theme_board_snapshot 同源）
    t0 = time.perf_counter()
    refreshed = refresh_theme_board_snapshot(trade_date=trade_date)
    cold_ms = _ms(time.perf_counter() - t0)
    ref_items = refreshed.get("items") or []
    ref_1d = sum(1 for i in ref_items if i.get("change_1d_percent") is not None)
    print(
        f"  [冷刷新 1d] refresh_theme_board_snapshot(67板块并行) "
        f"{cold_ms} ms | 有1d={ref_1d}/{len(ref_items)}"
    )


def bench_5d_sources() -> None:
    session = build_trading_session()
    trade_date = session.get("effective_trade_date")
    rows = _rows_from_theme_board_snapshot()
    if not rows:
        print("\n=== 5d：无 1d 快照，跳过 ===")
        return

    labels_15 = _labels_for_5d_kline_fetch(rows, limit=15)
    labels_all = _labels_for_5d_kline_fetch(rows, limit=None)

    print("\n=== 5d 数据源探测（日 K 序列 → 累加近5日涨跌幅） ===")
    print("接口: fetch_canonical_daily_kline_series → 东财 push2delay kline/get (klt=101)")
    print("      失败时: sector-relay → AkShare stock_board_*_hist_em / 新浪指数")
    print(f"可拉 5d 的板块数（有1d）: {len(labels_all)}；dip_swing 当前只拉 Top15: {len(labels_15)}")

    # 单板块日 K（5d 计算所需）
    from app.services.discovery_sector_heat import _resolve_kline_canon

    sample_labels = labels_all[:5]
    singles: list[float] = []
    for label in sample_labels:
        canon = _resolve_kline_canon(label)
        if canon is None:
            continue
        t0 = time.perf_counter()
        series = fetch_canonical_daily_kline_series(canon, max_days=12, timeout=12.0)
        elapsed = time.perf_counter() - t0
        singles.append(elapsed)
        bars = len(series)
        print(
            f"  [5d K线] {label:8s} bars={bars:2d}  {_ms(elapsed):>7} ms  "
            f"secid={canon.eastmoney_secid}"
        )

    if singles:
        print(
            f"  单板块日K (fetch_canonical_daily_kline_series) "
            f"median={_ms(statistics.median(singles))} ms"
        )

    # 直接测东财 raw API（不含 relay/akshare 兜底链）
    universe = {e["sector_label"]: e for e in list_theme_board_universe()}
    raw_times: list[float] = []
    for label in sample_labels:
        entry = universe.get(label)
        if not entry:
            continue
        t0 = time.perf_counter()
        series = fetch_eastmoney_daily_kline_series(
            entry["secid"],
            source_code=entry.get("source_code"),
            max_days=12,
            timeout=8.0,
        )
        elapsed = time.perf_counter() - t0
        raw_times.append(elapsed)
        print(
            f"  [东财raw] {label:8s} bars={len(series):2d}  {_ms(elapsed):>7} ms"
        )
    if raw_times:
        print(
            f"  单板块东财日K (fetch_eastmoney_daily_kline_series) "
            f"median={_ms(statistics.median(raw_times))} ms"
        )

    # 15 板块并发 merge（dip_swing 现行逻辑）
    import copy

    t0 = time.perf_counter()
    merged_15 = _merge_5d_kline_into_rows(
        copy.deepcopy(rows),
        trade_date=trade_date,
        fetch_canon_series=None,
        network_timeout=12.0,
        budget_seconds=45.0,
        max_labels=15,
    )
    ms_15 = _ms(time.perf_counter() - t0)
    got_15 = sum(1 for r in merged_15 if r.get("change_5d_percent") is not None)
    print(
        f"\n  [5d 批量] _merge_5d_kline_into_rows Top15 "
        f"budget=45s → {ms_15} ms | 有5d={got_15}/15"
    )

    # 67 板块全量（force，看预算内能完成多少）
    t0 = time.perf_counter()
    merged_all = _merge_5d_kline_into_rows(
        copy.deepcopy(rows),
        trade_date=trade_date,
        fetch_canon_series=None,
        network_timeout=12.0,
        budget_seconds=120.0,
        max_labels=None,
    )
    ms_all = _ms(time.perf_counter() - t0)
    got_all = sum(1 for r in merged_all if r.get("change_5d_percent") is not None)
    print(
        f"  [5d 批量] _merge_5d_kline_into_rows 全量({len(labels_all)}) "
        f"budget=120s → {ms_all} ms | 有5d={got_all}/{len(labels_all)}"
    )

    # build_sector_heat_ranking 端到端
    t0 = time.perf_counter()
    heat_1d = build_sector_heat_ranking(include_5d=False, force_refresh=False)
    ms_heat_1d = _ms(time.perf_counter() - t0)
    t0 = time.perf_counter()
    heat_5d = build_sector_heat_ranking(
        include_5d=True,
        force_refresh=True,
        budget_seconds=45.0,
    )
    ms_heat_5d = _ms(time.perf_counter() - t0)
    got_heat_5d = sum(1 for r in heat_5d if r.get("change_5d_percent") is not None)
    print(
        f"\n  [端到端] build_sector_heat_ranking(1d only, cache) → {ms_heat_1d} ms"
    )
    print(
        f"  [端到端] build_sector_heat_ranking(1d+5d Top15, force) → {ms_heat_5d} ms | 有5d={got_heat_5d}"
    )


if __name__ == "__main__":
    print("板块涨跌数据链路 benchmark")
    bench_1d_sources()
    bench_5d_sources()
    print("\n完成。")
