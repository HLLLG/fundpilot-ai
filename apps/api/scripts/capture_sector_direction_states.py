#!/usr/bin/env python3
"""每交易日把全白名单的方向状态落库，供退出侧读「连续跌破退出线」的天数。

用法（在 apps/api 下）：
    python scripts/capture_sector_direction_states.py
    python scripts/capture_sector_direction_states.py --trade-date 2026-08-10
    python scripts/capture_sector_direction_states.py --json var/sector_direction_capture.json

为什么需要它：`sector_direction_states` 原来只在用户手动跑一次发现基金时才写，于是
「连续跌破 N 个交易日才升级为大幅减仓」在真实使用中攒不出数据。口径与取舍见
`app/services/sector_direction_capture.py` 的模块 docstring。

退出码：
    0 = 捕获成功且**确实产出了趋势证据**（`with_trend_evidence > 0`）
    1 = 捕获失败，或落库了但趋势证据为 0（表面成功、实际全是占位值，退出侧一行都用不上）

诚实前提：本仓库沙箱到东财 `push2his` 的出站被阻断（与 run_sector_direction_backtest.py
同一处划界），因此在受限环境下趋势证据会为 0 并以退出码 1 结束，而不是假装捕获成功。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.request_context import set_request_user_id  # noqa: E402
from app.services.sector_direction_capture import (  # noqa: E402
    capture_sector_direction_states,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _recent_trade_dates(count: int, *, before: str | None) -> list[str]:
    """取 `before`（默认当前有效交易日）之前的 count 个交易日，从远到近。"""
    from app.services.trading_session import (
        get_effective_trade_date,
        get_previous_trade_date,
    )

    cursor = before or get_effective_trade_date()
    dates: list[str] = []
    for _ in range(count):
        cursor = get_previous_trade_date(cursor)
        if not cursor:
            break
        dates.append(str(cursor))
    return sorted(dates)


def _run_backfill(args) -> int:  # noqa: ANN001
    from app.services.sector_direction_capture import backfill_sector_direction_trend

    dates = _recent_trade_dates(args.backfill_days, before=args.trade_date)
    if not dates:
        print("!! 交易日历不可用，无法确定要回填哪些日期", file=sys.stderr)
        return 1

    print(
        f"[{_now()}] 回填模式：{len(dates)} 个交易日 {dates[0]} … {dates[-1]}"
        "（只重算趋势轴；资金流不重算，行标记 source=backfilled）",
        flush=True,
    )
    summary = backfill_sector_direction_trend(
        trade_dates=dates,
        progress=lambda stage: print(f"[{_now()}]   {stage}", flush=True),
    )

    print()
    if not summary.get("ok"):
        print(f"!! 回填失败：{summary.get('reason')}", file=sys.stderr)
        return 1
    print(f"  目标板块数        {summary.get('target_labels')}")
    for day in summary.get("days") or []:
        print(
            f"    {day['trade_date']}  新写入={day['written']:<4} "
            f"跳过（已有）={day['skipped_existing']:<4} "
            f"有趋势证据={day['with_trend_evidence']}"
        )
    print(f"  合计新写入        {summary.get('written_total')}")
    print(f"  合计有趋势证据     {summary.get('with_trend_evidence_total')}")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  摘要已写入 {out_path}")

    if not summary.get("with_trend_evidence_total"):
        print(
            "\n!! 回填的行里趋势证据为 0：多半是上游历史行情不可达，"
            "这些行对退出侧没有用。",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="捕获当交易日全白名单的板块方向状态（无 LLM 调用）",
    )
    parser.add_argument(
        "--trade-date",
        default=None,
        help="目标交易日 YYYY-MM-DD；省略则用当前交易时段推导",
    )
    parser.add_argument(
        "--json",
        dest="json_out",
        default=None,
        help="把捕获摘要写到该路径（机读）",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=1,
        help="仅用于满足请求上下文；方向状态表本身没有 userId 维度",
    )
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=0,
        help=(
            "改为回填模式：按日线重算最近 N 个历史交易日的**趋势轴**并落库"
            "（标记 source=backfilled，绝不覆盖已有行）。资金流不重算，"
            "因此这些行的 participation/entry_state 不可当作历史入场判断"
        ),
    )
    parser.add_argument(
        "--with-divergence",
        action="store_true",
        help=(
            "同时跑量价背离回测。默认跳过：它只影响 confidence，"
            "而 confidence 不落库也不参与 entry_state 判定，却是最贵的一段"
            "（实测占 103.5s 里的 90s）"
        ),
    )
    args = parser.parse_args()

    # 方向状态是全局账本（表里没有 userId），但取数链路上有若干只读查询走用户上下文，
    # 因此这里仍要设一个。捕获结果与该 id 无关。
    set_request_user_id(args.user_id)

    if args.backfill_days > 0:
        return _run_backfill(args)

    print(f"[{_now()}] 开始捕获板块方向状态（全白名单，无 LLM）", flush=True)
    summary = capture_sector_direction_states(
        trade_date=args.trade_date,
        progress=lambda stage: print(f"[{_now()}]   {stage}", flush=True),
        include_divergence=args.with_divergence,
    )

    print()
    print(f"  交易日            {summary.get('trade_date')}")
    print(f"  白名单板块数       {summary.get('universe_size')}")
    print(f"  mainline 可用      {summary.get('mainline_available')}")
    print(f"  落库行数          {summary.get('persisted')}")
    print(f"  其中有趋势证据     {summary.get('with_trend_evidence')}")
    print(f"  证据不足（占位）   {summary.get('degraded')}")
    print(f"  总耗时            {summary.get('elapsed_seconds')}s")
    if summary.get("timings"):
        print("  分段耗时:")
        for stage, seconds in summary["timings"].items():
            print(f"    {stage:20s} {seconds:>7.2f}s")

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n  摘要已写入 {out_path}")

    if not summary.get("ok"):
        print(f"\n!! 捕获失败：{summary.get('reason')}", file=sys.stderr)
        return 1
    if not summary.get("with_trend_evidence"):
        print(
            "\n!! 落库了但趋势证据为 0：这些行的趋势分是证据不足时的占位值（≤45，"
            "低于退出线 52），退出侧一行都用不上。多半是上游行情不可达。",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
