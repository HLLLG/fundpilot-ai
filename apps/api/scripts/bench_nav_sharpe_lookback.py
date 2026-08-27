"""对比荐基净值窗口 90 日 vs 252 日（一年）的冷拉取耗时，并估算年化夏普计算成本。

绕开小时 LRU 与持久缓存，直接跑与生产相同的 AkShare 适配脚本。
并发与荐基富化一致：最多 8 个 IO worker，AkShare 子进程池仍受配置限制。

用法：
    cd apps/api && ./.venv/Scripts/python.exe scripts/bench_nav_sharpe_lookback.py
"""
from __future__ import annotations

import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.akshare_subprocess import (
    _FUND_NAV_INDICATOR,
    _SUBPROCESS_TIMEOUT,
    _fund_nav_history_script,
    run_akshare_json_script,
)
from app.services.fund_data import _MAX_FETCH_WORKERS
from app.services.portfolio_risk_metrics import DEFAULT_RISK_FREE_RATE, _sharpe

# 覆盖股票/混合/指数联接，避免只测一只热门基。
FUNDS = [
    "000001",
    "110011",
    "161725",
    "005827",
    "163406",
    "001938",
    "260108",
    "000248",
    "110022",
    "519069",
    "001102",
    "000961",
    "161005",
    "001714",
    "003095",
    "001410",
]


def _fetch(code: str, trading_days: int) -> dict:
    started = time.perf_counter()
    payload = run_akshare_json_script(
        _fund_nav_history_script(code, trading_days, _FUND_NAV_INDICATOR),
        label=f"bench_nav:{code}:{trading_days}",
        timeout=_SUBPROCESS_TIMEOUT,
    )
    elapsed = time.perf_counter() - started
    rows = payload.get("data") if isinstance(payload, dict) else None
    error = None
    if not isinstance(payload, dict):
        error = "non_dict"
    elif payload.get("error"):
        error = str(payload.get("error"))
    elif not rows:
        error = "empty"
    returns: list[float] = []
    if isinstance(rows, list):
        for row in rows:
            growth = row.get("daily_growth")
            if growth is None:
                continue
            try:
                returns.append(float(growth) / 100.0)
            except (TypeError, ValueError):
                continue
    sharpe_started = time.perf_counter()
    sharpe = _sharpe(returns, DEFAULT_RISK_FREE_RATE) if len(returns) >= 2 else None
    sharpe_elapsed = time.perf_counter() - sharpe_started
    return {
        "code": code,
        "trading_days": trading_days,
        "elapsed": elapsed,
        "points": len(rows) if isinstance(rows, list) else 0,
        "return_points": len(returns),
        "sharpe": None if sharpe is None else round(sharpe, 4),
        "sharpe_elapsed": sharpe_elapsed,
        "error": error,
    }


def _summarize(rows: list[dict]) -> str:
    ok = [row for row in rows if not row["error"]]
    times = [row["elapsed"] for row in ok]
    points = [row["points"] for row in ok]
    sharpe_times = [row["sharpe_elapsed"] for row in ok]
    if not times:
        return "全部失败"
    return (
        f"成功 {len(ok)}/{len(rows)}  "
        f"点数中位 {statistics.median(points):.0f}  "
        f"拉取 {min(times):.2f}/{statistics.median(times):.2f}/{max(times):.2f}s "
        f"(min/中位/max)  "
        f"夏普计算中位 {statistics.median(sharpe_times)*1000:.3f}ms"
    )


def _print_rows(title: str, rows: list[dict], wall: float) -> None:
    print(f"\n== {title}  墙钟 {wall:.2f}s ==")
    print(_summarize(rows))
    for row in rows:
        sharpe = "n/a" if row["sharpe"] is None else f"{row['sharpe']:.2f}"
        status = row["error"] or "ok"
        print(
            f"  {row['code']}  {row['elapsed']:6.2f}s  "
            f"points={row['points']:4d}  sharpe={sharpe:>6}  {status}"
        )


def main() -> None:
    sequential = FUNDS[:4]
    batch = FUNDS[:16]
    print(
        f"AkShare 超时 {_SUBPROCESS_TIMEOUT}s，荐基并发上限 {_MAX_FETCH_WORKERS}，"
        f"顺序样本 {len(sequential)} 只，批量样本 {len(batch)} 只"
    )

    sequential_by_window: dict[int, list[dict]] = {}
    for window in (90, 252, 800):
        started = time.perf_counter()
        rows = [_fetch(code, window) for code in sequential]
        sequential_by_window[window] = rows
        _print_rows(f"顺序冷拉取 {window} 日", rows, time.perf_counter() - started)

    print("\n== 同一只基金 90 vs 252 vs 800 日差 ==")
    for index, code in enumerate(sequential):
        t90 = sequential_by_window[90][index]["elapsed"]
        t252 = sequential_by_window[252][index]["elapsed"]
        t800 = sequential_by_window[800][index]["elapsed"]
        print(
            f"  {code}  90={t90:.2f}s  252={t252:.2f}s ({t252-t90:+.2f}s)  "
            f"800={t800:.2f}s ({t800-t90:+.2f}s)"
        )

    batch_by_window: dict[int, list[dict]] = {}
    for window in (90, 252):
        started = time.perf_counter()
        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=_MAX_FETCH_WORKERS) as pool:
            futures = {pool.submit(_fetch, code, window): code for code in batch}
            for future in as_completed(futures):
                rows.append(future.result())
        rows.sort(key=lambda item: item["code"])
        batch_by_window[window] = rows
        _print_rows(
            f"并发冷拉取 {window} 日 × {len(batch)} 只 / {_MAX_FETCH_WORKERS} workers",
            rows,
            time.perf_counter() - started,
        )

    wall_90 = sum(row["elapsed"] for row in batch_by_window[90])
    wall_252 = sum(row["elapsed"] for row in batch_by_window[252])
    ok_90 = [row for row in batch_by_window[90] if not row["error"]]
    ok_252 = [row for row in batch_by_window[252] if not row["error"]]
    med_90 = statistics.median(row["elapsed"] for row in ok_90) if ok_90 else 0.0
    med_252 = statistics.median(row["elapsed"] for row in ok_252) if ok_252 else 0.0
    print("\n== 批量对照（按单只耗时加总，不是墙钟）==")
    print(f"  16 只单只耗时合计  90日 {wall_90:.2f}s  252日 {wall_252:.2f}s  差 {wall_252-wall_90:+.2f}s")
    print(f"  单只中位            90日 {med_90:.2f}s  252日 {med_252:.2f}s  差 {med_252-med_90:+.2f}s")
    print(
        "  荐基终选池约 28 只、8 并发时，墙钟大约是两波；"
        "若单只中位接近，一年窗口几乎不增加等待。"
    )


if __name__ == "__main__":
    main()
