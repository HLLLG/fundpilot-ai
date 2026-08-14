#!/usr/bin/env python3
"""方向退出侧两个未回测参数的回测入口（带数据充分性检查）。

用法（在 apps/api 下）：
    ./.venv/Scripts/python.exe scripts/run_direction_exit_backtest.py \
        --sqlite-cache ../../data/app.db

## 回测对象

`sector_direction_exit` 的两个新设参数（契约标注 `thresholds_validated=false`）：

* `PERSISTENT_BREAKDOWN_DAYS`（连续跌破退出线几天把 −25% 升到 −50%）——网格 {2,3,4,5}；
* `RELATIVE_TREND_DECAY_POINTS`（相对入场回落几分收回加仓资格）——网格 {8,12,16,不启用}。

另设两组对照回答更根本的问题：

* `no_exit_rules`：完全不减仓、持有到期——退出侧整体有没有价值；
* `immediate_full_exit`：首次跌破退出线就全退——分档递进 vs 一刀切。

## 模拟口径（与生产的对应关系）

* 入场：重放信号首次进入 ready 的次日收盘按 `--base-fraction` 建仓（T+1）；
* 加仓：生产「现状 + 浮亏封档」梯形（`run_position_sizing_backtest` 同款），并叠加
  decay gate——趋势相对入场回落 ≥X 分时当天禁加仓（对应生产的暂停追涨）；
* 减仓：按**状态升级**执行而不是逐日重复——进入「跌破首日」减 25%（浮盈 −1/3）、
  升级到「连续 ≥N 日」再减到位（对当前持仓再减 50%）、方向 invalid 且持续跌破则全退。
  生产的日报每天都会重复给同一档建议，但把"用户每天机械照做"当作模拟假设会把重复建议
  的措辞问题误算成参数收益，这里取更保守的"每档执行一次"。

## 数据充分性检查

episode 少于 `--min-episodes`（默认 30）或重放决策日少于 `--min-decision-days`
（默认 60）时**不产出任何结论**，输出 `status=insufficient_data` 并以退出码 2 结束。
线上 `sector_direction_states` 账本攒够真实逐日数据前，本脚本的重放数据源是唯一可用的
PIT 近似；两者结论如有分歧，以真实账本为准。

shadow_record_only：研究输出，不自动改动任何线上权重、阈值、Prompt、Guard 或仓位。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import run_position_sizing_backtest as sizing  # noqa: E402

from app.services.sector_direction_exit import (  # noqa: E402
    PERSISTENT_BREAKDOWN_DAYS,
    RELATIVE_TREND_DECAY_POINTS,
)
from app.services.sector_opportunity_scoring import (  # noqa: E402
    ENTRY_INVALID,
    EXIT_TREND_THRESHOLD,
)

EPISODE_BUDGET = sizing.EPISODE_BUDGET


@dataclass(frozen=True)
class ExitVariant:
    key: str
    label: str
    #: None 表示该变体不做方向退出减仓（对照组）。
    persistent_days: int | None
    #: None 表示不启用 decay gate。
    decay_points: float | None
    #: 首次跌破就全退（对照组），覆盖分档递进。
    immediate_full_exit: bool = False
    note: str = ""


def build_variants() -> tuple[ExitVariant, ...]:
    baseline = ExitVariant(
        key="production_3_12",
        label=f"生产（连续 {PERSISTENT_BREAKDOWN_DAYS} 日 / 回落 {RELATIVE_TREND_DECAY_POINTS:g} 分）",
        persistent_days=PERSISTENT_BREAKDOWN_DAYS,
        decay_points=RELATIVE_TREND_DECAY_POINTS,
        note="当前线上取值",
    )
    persistent_grid = tuple(
        ExitVariant(
            key=f"persistent_{days}",
            label=f"连续 {days} 日升级（decay 固定 {RELATIVE_TREND_DECAY_POINTS:g}）",
            persistent_days=days,
            decay_points=RELATIVE_TREND_DECAY_POINTS,
        )
        for days in (2, 3, 4, 5)
        if days != PERSISTENT_BREAKDOWN_DAYS
    )
    decay_grid = tuple(
        ExitVariant(
            key=f"decay_{'off' if points is None else int(points)}",
            label=(
                f"回落 {points:g} 分禁加（连续固定 {PERSISTENT_BREAKDOWN_DAYS} 日）"
                if points is not None
                else f"不启用回落禁加（连续固定 {PERSISTENT_BREAKDOWN_DAYS} 日）"
            ),
            persistent_days=PERSISTENT_BREAKDOWN_DAYS,
            decay_points=points,
        )
        for points in (8.0, 16.0, None)
    )
    controls = (
        ExitVariant(
            key="no_exit_rules",
            label="不减仓·持有到期（对照）",
            persistent_days=None,
            decay_points=None,
            note="回答退出侧整体有没有价值",
        ),
        ExitVariant(
            key="immediate_full_exit",
            label="首次跌破即全退（对照）",
            persistent_days=1,
            decay_points=None,
            immediate_full_exit=True,
            note="回答分档递进 vs 一刀切",
        ),
    )
    return (baseline, *persistent_grid, *decay_grid, *controls)


def simulate_exit_episode(
    *,
    variant: ExitVariant,
    prices: list[dict[str, Any]],
    signal_index: int,
    signals_by_date: dict[str, "sizing.Signal"],
    sector_label: str,
    costs: "sizing.CostModel",
    max_days: int,
    base_fraction: float,
) -> "sizing.EpisodeResult | None":
    """一个 episode：ready 次日建仓，按变体的退出档位部分减仓，到期全退。

    与 `sizing.simulate_episode` 的关键差异：退出不是二元的（止损→全退），而是生产的
    分档路径（−25% → −50% → 清仓）。移动止损刻意不启用——这里要单独度量方向退出档位
    的贡献，叠加止损会把两套规则的效果混在一起。
    """
    entry_index = signal_index + 1
    if entry_index >= len(prices):
        return None

    entry_signal = signals_by_date.get(str(prices[signal_index]["date"]))
    entry_trend = entry_signal.trend_strength if entry_signal else None

    position = sizing.Position()
    remaining = EPISODE_BUDGET
    entry_date = str(prices[entry_index]["date"])
    entry_price = float(prices[entry_index]["close"])
    initial_cash = min(EPISODE_BUDGET * base_fraction, remaining)
    position.buy(cash=initial_cash, price=entry_price, trade_date=entry_date, costs=costs)
    remaining -= initial_cash

    cash_realized = 0.0
    peak_value = position.market_value(entry_price) + remaining
    max_drawdown = 0.0
    peak_profit = 0.0
    consecutive_below = 0
    #: 已执行到的退出档位（0=未退，1=首日减仓，2=持续减仓，3=清仓）。按状态升级执行。
    executed_stage = 0
    pending_reduce_fraction = 0.0
    pending_add = 0.0
    exit_reason = "max_days"
    exit_index = min(entry_index + max_days, len(prices) - 1)
    censored = entry_index + max_days > len(prices) - 1

    cursor = entry_index
    while cursor < min(entry_index + max_days, len(prices) - 1):
        cursor += 1
        day = str(prices[cursor]["date"])
        price = float(prices[cursor]["close"])

        # 先执行上一收盘决定的动作（T+1）。
        if pending_reduce_fraction > 0:
            cash_realized += position.redeem_fraction(
                fraction=pending_reduce_fraction,
                price=price,
                trade_date=day,
                costs=costs,
            )
            pending_reduce_fraction = 0.0
            if executed_stage >= 3 or not position.tranches:
                exit_index = cursor
                exit_reason = "direction_exit"
                break
        if pending_add > 0:
            cash = min(pending_add, remaining)
            position.buy(cash=cash, price=price, trade_date=day, costs=costs)
            remaining -= cash
            pending_add = 0.0

        total_value = position.market_value(price) + remaining + cash_realized
        peak_value = max(peak_value, total_value)
        if peak_value > 0:
            max_drawdown = max(
                max_drawdown, (peak_value - total_value) / peak_value * 100.0
            )
        peak_profit = max(peak_profit, (total_value / EPISODE_BUDGET - 1.0) * 100.0)

        signal = signals_by_date.get(day)
        trend = signal.trend_strength if signal else None
        below = trend is not None and trend < EXIT_TREND_THRESHOLD
        consecutive_below = consecutive_below + 1 if below else 0

        # 退出判定（收盘触发、次日执行），按状态升级、每档只执行一次。
        if variant.persistent_days is not None and signal is not None:
            invalid = signal.entry_state == ENTRY_INVALID
            persistent = consecutive_below >= max(2, variant.persistent_days)
            in_gain = (position.return_percent(price) or 0.0) > 0
            if variant.immediate_full_exit:
                if below and executed_stage < 3:
                    executed_stage = 3
                    pending_reduce_fraction = 1.0
                    continue
            elif invalid and persistent and executed_stage < 3:
                executed_stage = 3
                pending_reduce_fraction = 1.0
                continue
            elif (invalid or persistent) and executed_stage < 2:
                executed_stage = 2
                pending_reduce_fraction = 0.5
                continue
            elif below and executed_stage < 1:
                executed_stage = 1
                pending_reduce_fraction = (1.0 / 3.0) if in_gain else 0.25
                continue

        # 加仓（生产梯形 + decay gate）。已进入任何退出档位后不再加仓——
        # 与生产「任何一档退出信号都同时取消加仓资格」一致。
        if remaining > 0 and executed_stage == 0:
            decayed = (
                variant.decay_points is not None
                and entry_trend is not None
                and trend is not None
                and (entry_trend - trend) >= variant.decay_points
            )
            signal_ready = bool(signal and signal.ready) and not decayed
            tier = signal.tier_percent() if signal else 0.0
            ret = position.return_percent(price)
            if ret is None or ret <= 0:
                tier = min(tier, sizing._LOWEST_ADD_TIER_PERCENT)
            if signal_ready:
                cash = (
                    position.market_value(price)
                    * tier
                    / 100.0
                    * (signal.tranche_scale() if signal else 0.0)
                )
                pending_add = min(cash, remaining)

    exit_date = str(prices[exit_index]["date"])
    exit_price = float(prices[exit_index]["close"])
    proceeds = position.liquidate(price=exit_price, trade_date=exit_date, costs=costs)
    final_value = proceeds + remaining + cash_realized
    deployed = position.spent
    return_on_budget = (final_value / EPISODE_BUDGET - 1.0) * 100.0
    return_on_deployed = (
        ((final_value - (EPISODE_BUDGET - deployed)) / deployed - 1.0) * 100.0
        if deployed > 0
        else None
    )
    giveback = (
        return_on_budget / peak_profit * 100.0
        if peak_profit >= sizing._MIN_PEAK_FOR_CAPTURE
        else None
    )
    return sizing.EpisodeResult(
        policy_key=variant.key,
        sector_label=sector_label,
        signal_date=str(prices[signal_index]["date"]),
        entry_date=entry_date,
        exit_date=exit_date,
        exit_reason=exit_reason,
        return_on_budget_percent=return_on_budget,
        return_on_deployed_percent=return_on_deployed,
        deployed_percent=deployed / EPISODE_BUDGET * 100.0,
        buy_count=position.buy_count,
        fees_percent_of_budget=position.fees_paid / EPISODE_BUDGET * 100.0,
        max_drawdown_percent=max_drawdown,
        peak_profit_percent=peak_profit,
        giveback_percent=giveback,
        hold_days=(date.fromisoformat(exit_date) - date.fromisoformat(entry_date)).days,
        censored=censored,
    )


def run(
    prepared: "sizing.Prepared",
    *,
    max_days: int,
    base_fraction: float,
    costs: "sizing.CostModel",
    min_episodes: int,
    min_decision_days: int,
    sqlite_cache: str,
) -> dict[str, Any]:
    variants = build_variants()
    results: dict[str, list[sizing.EpisodeResult]] = {v.key: [] for v in variants}
    episode_count = 0
    for label, by_date in sorted(prepared.signals.items()):
        ordered_dates = sorted(by_date)
        previous_ready = False
        for day in ordered_dates:
            signal = by_date[day]
            if signal.ready and not previous_ready:
                cursor = prepared.index_by_label[label].get(day)
                if cursor is not None:
                    episode_count += 1
                    for variant in variants:
                        outcome = simulate_exit_episode(
                            variant=variant,
                            prices=prepared.prices_by_label[label],
                            signal_index=cursor,
                            signals_by_date=by_date,
                            sector_label=label,
                            costs=costs,
                            max_days=max_days,
                            base_fraction=base_fraction,
                        )
                        if outcome is not None and not outcome.censored:
                            results[variant.key].append(outcome)
            previous_ready = signal.ready

    evaluated = len(results[variants[0].key])
    if evaluated < min_episodes or prepared.decision_day_count < min_decision_days:
        return {
            "schema_version": "direction_exit_backtest.v1",
            "status": "insufficient_data",
            "decision_policy": "shadow_record_only",
            "auto_tuning_eligible": False,
            "episode_count_evaluated": evaluated,
            "min_episodes_required": min_episodes,
            "decision_day_count": prepared.decision_day_count,
            "min_decision_days_required": min_decision_days,
            "note": (
                "样本不足，不产出任何参数结论。等 sector_direction_states 账本或"
                "资金流缓存积累更长历史后重跑。"
            ),
        }

    baseline_key = variants[0].key
    return {
        "schema_version": "direction_exit_backtest.v1",
        "status": "ok",
        "decision_policy": "shadow_record_only",
        "auto_tuning_eligible": False,
        "params": {
            "sqlite_cache": sqlite_cache,
            "max_episode_days": max_days,
            "base_fraction": base_fraction,
            "exit_trend_line": EXIT_TREND_THRESHOLD,
            "production_persistent_days": PERSISTENT_BREAKDOWN_DAYS,
            "production_decay_points": RELATIVE_TREND_DECAY_POINTS,
        },
        "market_context": prepared.market_context,
        "episode_count_total": episode_count,
        "episode_count_evaluated": evaluated,
        "variants": [
            {
                "key": variant.key,
                "label": variant.label,
                "note": variant.note,
                "persistent_days": variant.persistent_days,
                "decay_points": variant.decay_points,
                "summary": sizing.summarize(results[variant.key]),
                **(
                    {
                        "paired_vs_production": sizing.paired_stats(
                            results[variant.key], results[baseline_key]
                        )
                    }
                    if variant.key != baseline_key
                    else {}
                ),
            }
            for variant in variants
        ],
        "caveats": [
            *prepared.caveats,
            "标的是板块指数，不是可买到的基金：跟踪误差、净值滞后、赎回周期未建模。",
            "减仓按状态升级每档执行一次，不模拟“用户每天重复执行同一档建议”。",
            "移动止损刻意未启用：这里单独度量方向退出档位的贡献。",
            "重放信号是生产打分器的 PIT 重算，与线上逐日账本可能有差异；账本攒够后应以账本重跑。",
            "单一样本区间，结论不能外推；两个参数即便某取值占优也只取得人工评审资格。",
        ],
    }


def _render(payload: dict[str, Any]) -> str:
    if payload.get("status") == "insufficient_data":
        return (
            "方向退出参数回测：数据不足，不产出结论。\n"
            f"  episode {payload['episode_count_evaluated']} 个"
            f"（至少需要 {payload['min_episodes_required']}），"
            f"决策日 {payload['decision_day_count']} 个"
            f"（至少需要 {payload['min_decision_days_required']}）。\n"
            f"  {payload['note']}\n"
        )
    lines = [
        "方向退出参数回测（同一批 PIT 入场，只换退出参数）",
        f"episode {payload['episode_count_evaluated']} 个   "
        f"生产取值：连续 {payload['params']['production_persistent_days']} 日 / "
        f"回落 {payload['params']['production_decay_points']:g} 分",
        "⚠ shadow_record_only：占优也只取得人工评审资格，不自动改线上。",
        "",
    ]
    context = payload.get("market_context") or {}
    if context.get("available"):
        lines.append(
            f"  样本期（{context['start_date']} ~ {context['end_date']}）："
            f"基准累计 {context['benchmark_total_return_percent']:+.2f}%，"
            f"最大回撤 {context['benchmark_max_drawdown_percent']:.2f}%。"
        )
        lines.append("")
    for entry in payload.get("variants") or []:
        stats = entry["summary"]
        if not stats.get("available"):
            lines.append(f"  {entry['label']}: 样本不足")
            continue
        row = (
            f"  {entry['label']}: 均值 {stats['mean_return_on_budget_percent']:+.3f}%，"
            f"回撤 {stats['mean_max_drawdown_percent']:.2f}%，"
            f"费用 {stats['mean_fees_percent_of_budget']:.2f}%，"
            f"留存 {stats['median_capture_ratio_percent'] if stats['median_capture_ratio_percent'] is not None else '—'}%"
        )
        paired = entry.get("paired_vs_production")
        if paired and paired.get("available"):
            row += (
                f" ｜ vs 生产：均值差 {paired['mean_diff_percent']:+.3f}%，"
                f"t={paired['t_stat']}"
                f"（{'显著' if paired['significant'] else '不显著'}）"
            )
        lines.append(row)
    lines.append("")
    for caveat in payload.get("caveats") or []:
        lines.append(f"  ⚠ {caveat}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="方向退出参数回测入口（离线研究）")
    parser.add_argument("--sqlite-cache", required=True)
    parser.add_argument("--max-days", type=int, default=40)
    parser.add_argument("--base-fraction", type=float, default=0.20)
    parser.add_argument("--warmup-days", type=int, default=61)
    parser.add_argument("--buy-fee-percent", type=float, default=0.12)
    parser.add_argument("--redeem-fee-percent", type=float, default=0.5)
    parser.add_argument("--short-hold-redeem-fee-percent", type=float, default=1.5)
    parser.add_argument("--min-episodes", type=int, default=30)
    parser.add_argument("--min-decision-days", type=int, default=60)
    parser.add_argument(
        "--out-dir", type=str, default=str(API_ROOT / "var" / "direction_exit_backtest")
    )
    args = parser.parse_args()

    costs = sizing.CostModel(
        buy_fee_percent=args.buy_fee_percent,
        redeem_fee_percent=args.redeem_fee_percent,
        short_hold_redeem_fee_percent=args.short_hold_redeem_fee_percent,
    )
    prepared = sizing.prepare(
        sqlite_cache=args.sqlite_cache, warmup_days=args.warmup_days
    )
    payload = run(
        prepared,
        max_days=args.max_days,
        base_fraction=args.base_fraction,
        costs=costs,
        min_episodes=args.min_episodes,
        min_decision_days=args.min_decision_days,
        sqlite_cache=args.sqlite_cache,
    )
    out_path = Path(args.out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = _render(payload)
    (out_path / "report.txt").write_text(report, encoding="utf-8")
    print(report)
    print(f"报告已写入: {out_path / 'report.txt'}")
    return 2 if payload.get("status") == "insufficient_data" else 0


if __name__ == "__main__":
    raise SystemExit(main())
