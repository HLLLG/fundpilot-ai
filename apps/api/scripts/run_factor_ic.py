#!/usr/bin/env python3
"""离线回测模块2 因子的有效性（walk-forward Rank IC），产出人读报告 + 机读 summary.json。

用法（在 apps/api 下）：
    ./.venv/Scripts/python.exe scripts/run_factor_ic.py --universe-size 300 --nav-days 750

设计文档：docs/superpowers/specs/2026-06-24-factor-ic-backtest-design.md
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.factor_ic_backtest import (  # noqa: E402
    DEFAULT_FACTOR_LOOKBACK,
    DEFAULT_FORWARD_DAYS,
    DEFAULT_REBALANCE_STEP,
    FACTOR_ORDER,
    NavPoint,
    compute_factor_ic,
)

_DEFAULT_OUT_DIR = str(API_ROOT / "var" / "factor_ic")

_FACTOR_LABEL = {
    "momentum": "动量",
    "risk_adjusted": "风险调整",
    "drawdown": "回撤控制",
    "composite": "综合",
}

_CAVEATS = [
    "基金池为「当前在榜、业绩偏强」样本，存在幸存者/选择偏差，IC 偏乐观。",
    "结论仅用于因子之间的相对比较，不代表全市场真实预测力。",
    "单因子单期 Rank IC 在 0.03~0.05 即属可用；过高通常意味着前视偏差。",
]


def _default_fetch_rank(limit: int) -> list[dict]:
    from app.services.akshare_subprocess import fetch_open_fund_rank

    return fetch_open_fund_rank(limit=limit) or []


def _default_fetch_nav(code: str, name: str, trading_days: int) -> list[NavPoint]:
    from app.services.akshare_subprocess import fetch_fund_nav_history

    payload = fetch_fund_nav_history(code, trading_days=trading_days)
    if not payload or not payload.get("data"):
        return []
    points: list[NavPoint] = []
    for row in payload["data"]:
        nav = row.get("nav")
        day = str(row.get("date", ""))[:10]
        if nav is None or not day:
            continue
        try:
            nav_f = float(nav)
        except (TypeError, ValueError):
            continue
        if nav_f > 0:
            points.append(NavPoint(day, nav_f))
    return points


def _verdict(stats) -> str:
    if stats.mean_ic is None or stats.n_periods == 0:
        return "样本不足"
    if not stats.significant:
        return "不显著"
    if abs(stats.mean_ic) < 0.02:
        return "极弱"
    direction = "正向" if stats.mean_ic > 0 else "反向"
    return f"{direction}有效 ✓"


def _render_report(result, *, run_date: str, universe_effective: int) -> str:
    lines: list[str] = []
    lines.append(f"因子有效性回测 (Rank IC)  运行: {run_date}")
    lines.append(
        f"池: 排行榜 {universe_effective} 只 (有效)  "
        f"再平衡: 每{result.forward_days}日前瞻  "
        f"期数: {result.rebalance_count}"
    )
    for c in _CAVEATS:
        lines.append(f"⚠ {c}")
    lines.append("-" * 64)
    lines.append(
        f"{'因子':<8}{'mean IC':>10}{'ICIR':>8}{'t':>8}{'%>0':>8}{'n':>6}  结论"
    )
    for stats in result.factors:
        label = _FACTOR_LABEL.get(stats.factor, stats.factor)
        mean_ic = "—" if stats.mean_ic is None else f"{stats.mean_ic:+.4f}"
        icir = "—" if stats.icir is None else f"{stats.icir:+.2f}"
        t = "—" if stats.t_stat is None else f"{stats.t_stat:+.2f}"
        pos = "—" if stats.positive_ratio is None else f"{stats.positive_ratio:.2f}"
        lines.append(
            f"{label:<8}{mean_ic:>10}{icir:>8}{t:>8}{pos:>8}{stats.n_periods:>6}  {_verdict(stats)}"
        )
    return "\n".join(lines) + "\n"


def build_ic_report(
    *,
    fetch_rank=_default_fetch_rank,
    fetch_nav=_default_fetch_nav,
    out_dir: str = _DEFAULT_OUT_DIR,
    universe_size: int = 300,
    nav_days: int = 750,
    rebalance_step: int = DEFAULT_REBALANCE_STEP,
    forward_days: int = DEFAULT_FORWARD_DAYS,
    factor_lookback: int = DEFAULT_FACTOR_LOOKBACK,
    max_workers: int = 8,
    limit_funds: int | None = None,
) -> dict:
    """取数 → 组面板 → 跑引擎 → 落盘 report.txt + summary.json，返回结果 dict。"""
    rank_rows = fetch_rank(universe_size) or []
    codes = [
        (row["fund_code"], row.get("fund_name", ""))
        for row in rank_rows
        if row.get("fund_code")
    ]
    if limit_funds is not None:
        codes = codes[:limit_funds]

    def _one(item):
        code, name = item
        try:
            return code, fetch_nav(code, name, nav_days)
        except Exception:
            return code, []

    nav_panel: dict[str, list[NavPoint]] = {}
    if codes:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(codes)))) as pool:
            for code, points in pool.map(_one, codes):
                if points and len(points) >= 2:
                    nav_panel[code] = sorted(points, key=lambda p: p.date)

    calendar = sorted({p.date for pts in nav_panel.values() for p in pts})

    result = compute_factor_ic(
        nav_panel=nav_panel,
        calendar=calendar,
        rebalance_step=rebalance_step,
        forward_days=forward_days,
        factor_lookback=factor_lookback,
    )

    run_date = datetime.now(timezone.utc).date().isoformat()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    report = _render_report(result, run_date=run_date, universe_effective=len(nav_panel))
    (out_path / "report.txt").write_text(report, encoding="utf-8")

    summary = {
        "run_date": run_date,
        "params": {
            "universe_size": universe_size,
            "nav_days": nav_days,
            "rebalance_step": rebalance_step,
            "forward_days": forward_days,
            "factor_lookback": factor_lookback,
        },
        "available": result.available,
        "message": result.message,
        "universe_size": result.universe_size,
        "rebalance_count": result.rebalance_count,
        "forward_days": result.forward_days,
        "caveats": _CAVEATS,
        "factors": [
            {k: v for k, v in asdict(stats).items() if k != "ic_series"}
            for stats in result.factors
        ],
    }
    (out_path / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="因子有效性回测 (Rank IC)")
    parser.add_argument("--universe-size", type=int, default=300)
    parser.add_argument("--nav-days", type=int, default=750)
    parser.add_argument("--rebalance-step", type=int, default=DEFAULT_REBALANCE_STEP)
    parser.add_argument("--forward-days", type=int, default=DEFAULT_FORWARD_DAYS)
    parser.add_argument("--factor-lookback", type=int, default=DEFAULT_FACTOR_LOOKBACK)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--limit-funds", type=int, default=None, help="调试用，限制只数")
    parser.add_argument("--out-dir", type=str, default=_DEFAULT_OUT_DIR)
    args = parser.parse_args()

    summary = build_ic_report(
        out_dir=args.out_dir,
        universe_size=args.universe_size,
        nav_days=args.nav_days,
        rebalance_step=args.rebalance_step,
        forward_days=args.forward_days,
        factor_lookback=args.factor_lookback,
        max_workers=args.max_workers,
        limit_funds=args.limit_funds,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n报告已写入: {Path(args.out_dir) / 'report.txt'}")
    return 0 if summary["available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
