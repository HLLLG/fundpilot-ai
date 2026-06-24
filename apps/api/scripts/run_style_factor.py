#!/usr/bin/env python3
"""离线计算基金的价值/成长风格暴露（收益型风格分析），产出报告 + summary.json。

用法（在 apps/api 下）：
    ./.venv/Scripts/python.exe scripts/run_style_factor.py --universe-size 200 --nav-days 250

设计文档：docs/superpowers/specs/2026-06-24-factor-style-and-universe-design.md（3C）。
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

from app.services.fund_style_regression import (  # noqa: E402
    align_returns,
    compute_style_exposure,
)

_DEFAULT_OUT_DIR = str(API_ROOT / "var" / "style_factor")
_DEFAULT_VALUE_INDEX = "399371"  # 国证价值
_DEFAULT_GROWTH_INDEX = "399370"  # 国证成长

_CAVEATS = [
    "这是「风格暴露」(基金长得像价值/成长)，不是基本面便宜/质量。",
    "基金池来自排行榜，存在幸存者/选择偏差。",
    "需价值与成长指数走势有足够差异，否则风格无法分离。",
]


def _to_returns(series: list[tuple[str, float]]) -> dict[str, float]:
    """把 (日期, 价格/净值) 升序序列转成 {日期: 当日对数百分比收益}。"""
    s = sorted(series, key=lambda x: x[0])
    out: dict[str, float] = {}
    for i in range(1, len(s)):
        prev = s[i - 1][1]
        cur = s[i][1]
        if prev and prev > 0 and cur > 0:
            out[s[i][0]] = (cur / prev - 1.0) * 100.0
    return out


def _default_fetch_rank(limit: int) -> list[dict]:
    from app.services.akshare_subprocess import fetch_open_fund_rank

    return fetch_open_fund_rank(limit=limit) or []


def _default_fetch_nav(code: str, trading_days: int) -> list[tuple[str, float]]:
    from app.services.akshare_subprocess import fetch_fund_nav_history

    payload = fetch_fund_nav_history(code, trading_days=trading_days)
    if not payload or not payload.get("data"):
        return []
    out: list[tuple[str, float]] = []
    for row in payload["data"]:
        day = str(row.get("date", ""))[:10]
        nav = row.get("nav")
        if not day or nav is None:
            continue
        try:
            out.append((day, float(nav)))
        except (TypeError, ValueError):
            continue
    return out


def _default_fetch_index(symbol: str, trading_days: int) -> list[tuple[str, float]]:
    from app.services.akshare_subprocess import fetch_index_daily_history

    payload = fetch_index_daily_history(symbol, trading_days=trading_days)
    if not payload or not payload.get("data"):
        return []
    out: list[tuple[str, float]] = []
    for row in payload["data"]:
        day = str(row.get("date", ""))[:10]
        close = row.get("close")
        if not day or close is None:
            continue
        try:
            out.append((day, float(close)))
        except (TypeError, ValueError):
            continue
    return out


def _render_report(funds: list[dict], *, run_date: str, counts: dict) -> str:
    lines: list[str] = []
    lines.append(f"基金风格暴露 (价值/成长回归)  运行: {run_date}")
    lines.append(
        f"有效: {counts['available']} 只  "
        f"偏价值 {counts['value']} / 偏成长 {counts['growth']} / 中性 {counts['neutral']}"
    )
    for c in _CAVEATS:
        lines.append(f"* {c}")
    lines.append("-" * 64)
    lines.append(f"{'代码':<8}{'名称':<14}{'tilt':>8}{'bV':>7}{'bG':>7}{'R2':>6}  风格")
    for f in funds:
        if not f["available"]:
            continue
        name = (f["fund_name"] or "")[:12]
        r2 = "—" if f["r_squared"] is None else f"{f['r_squared']:.2f}"
        lines.append(
            f"{f['fund_code']:<8}{name:<14}{f['style_tilt']:>+8.2f}"
            f"{f['beta_value']:>+7.2f}{f['beta_growth']:>+7.2f}{r2:>6}  {f['label']}"
        )
    return "\n".join(lines) + "\n"


def build_style_report(
    *,
    fetch_rank=_default_fetch_rank,
    fetch_nav=_default_fetch_nav,
    fetch_index=_default_fetch_index,
    out_dir: str = _DEFAULT_OUT_DIR,
    universe_size: int = 200,
    nav_days: int = 250,
    value_index: str = _DEFAULT_VALUE_INDEX,
    growth_index: str = _DEFAULT_GROWTH_INDEX,
    max_workers: int = 8,
    limit_funds: int | None = None,
) -> dict:
    """取数 → 风格回归 → 落盘 report.txt + summary.json，返回 summary dict。"""
    value_ret = _to_returns(fetch_index(value_index, nav_days))
    growth_ret = _to_returns(fetch_index(growth_index, nav_days))

    rank_rows = fetch_rank(universe_size) or []
    items = [
        (row["fund_code"], row.get("fund_name", ""))
        for row in rank_rows
        if row.get("fund_code")
    ]
    if limit_funds is not None:
        items = items[:limit_funds]

    style_ok = bool(value_ret) and bool(growth_ret)

    def _one(item):
        code, name = item
        if not style_ok:
            return code, name, None
        try:
            fund_ret = _to_returns(fetch_nav(code, nav_days))
        except Exception:
            return code, name, None
        fr, vr, gr = align_returns(fund_ret, value_ret, growth_ret)
        return code, name, compute_style_exposure(fr, vr, gr)

    funds: list[dict] = []
    counts = {"available": 0, "value": 0, "growth": 0, "neutral": 0}
    if items:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(items)))) as pool:
            results = list(pool.map(_one, items))
    else:
        results = []

    for code, name, exp in results:
        if exp is None:
            funds.append({"fund_code": code, "fund_name": name, "available": False})
            continue
        row = {"fund_code": code, "fund_name": name, **asdict(exp)}
        funds.append(row)
        if exp.available:
            counts["available"] += 1
            if exp.label == "偏价值":
                counts["value"] += 1
            elif exp.label == "偏成长":
                counts["growth"] += 1
            else:
                counts["neutral"] += 1

    funds.sort(
        key=lambda f: (f.get("style_tilt") is None, -(f.get("style_tilt") or 0.0))
    )

    run_date = datetime.now(timezone.utc).date().isoformat()
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "report.txt").write_text(
        _render_report(funds, run_date=run_date, counts=counts), encoding="utf-8"
    )

    summary = {
        "run_date": run_date,
        "params": {
            "universe_size": universe_size,
            "nav_days": nav_days,
            "value_index": value_index,
            "growth_index": growth_index,
        },
        "style_data_available": style_ok,
        "counts": counts,
        "caveats": _CAVEATS,
        "funds": funds,
    }
    (out_path / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="基金价值/成长风格暴露 (收益型风格分析)")
    parser.add_argument("--universe-size", type=int, default=200)
    parser.add_argument("--nav-days", type=int, default=250)
    parser.add_argument("--value-index", type=str, default=_DEFAULT_VALUE_INDEX)
    parser.add_argument("--growth-index", type=str, default=_DEFAULT_GROWTH_INDEX)
    parser.add_argument("--max-workers", type=int, default=8)
    parser.add_argument("--limit-funds", type=int, default=None)
    parser.add_argument("--out-dir", type=str, default=_DEFAULT_OUT_DIR)
    args = parser.parse_args()

    summary = build_style_report(
        out_dir=args.out_dir,
        universe_size=args.universe_size,
        nav_days=args.nav_days,
        value_index=args.value_index,
        growth_index=args.growth_index,
        max_workers=args.max_workers,
        limit_funds=args.limit_funds,
    )
    print(json.dumps(summary["counts"], ensure_ascii=False, indent=2))
    print(f"\n报告已写入: {Path(args.out_dir) / 'report.txt'}")
    return 0 if summary["style_data_available"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
