#!/usr/bin/env python3
"""比较**加仓梯形**（不是入场信号）的离线研究脚本。

用法（在 apps/api 下）：
    ./.venv/Scripts/python.exe scripts/run_position_sizing_backtest.py \
        --sqlite-cache ../../data/app.db

## 这个脚本补的是哪个缺口

`sector_direction_backtest` 评的是**信号质量**：每个观测都是"D+1 收盘买 1 单位、持有 h
天"，因此它对"同一个信号下，资金该怎么分批投进去"完全不敏感——所有梯形在它的标尺上
得分一模一样。而 `recommendation_guard._resolve_deterministic_position_change` 的四档
（20/15/10/5，分母是**当前持仓市值**）与任何金字塔式建仓法的差别恰恰只在资金路径上。

本脚本因此在同一批 point-in-time 观测上加一层**仓位路径模拟**：固定入场信号与退出规则，
只替换加仓梯形，看终值、回撤与利润回吐。

## 复用而不复制

入场信号来自 `replay_sector_direction`（它自己直接调生产打分器）；加仓档位直接调生产的
`_resolve_sector_add_tier`；试仓系数用生产的 `V3_TREND_TRANCHE_SCALES`；退出线用生产的
`EXIT_TREND_THRESHOLD`。任何一处若改成副本，结论就不再描述线上行为。

## 已建模的基金现实

* **T+1 成交**：所有买卖都在信号日的**下一个收盘**执行（与重放层的建仓价约定一致）。
* **申购费**按笔收取；**赎回费**按先进先出，持有不足 7 天的份额收惩罚性费率。
* 止损按"收盘触发、次日收盘执行"，因为基金拿不到盘中价。

## 刻意没有建模（结论必须带着这些读）

* 标的是**板块指数**，不是可买到的基金：跟踪误差、净值滞后、QDII 更长的赎回周期都不在内。
* 过热标记（`overheat_flags`）不在 `DirectionObservation` 里，因此试仓系数只按趋势强度分档，
  没有过热缩放；实际线上会更保守。
* 基金自身证据降档、被动载体质量降档、集中度上限、新闻门禁、交易状态门禁都不在内。
* 每个 episode 用独立的 100 单位预算，不模拟组合层的资金竞争。

shadow_record_only：研究输出，不自动改动任何线上权重、阈值、Prompt、Guard 或仓位。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import fmean, median
from typing import Any, Callable

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.services.recommendation_guard import (  # noqa: E402
    _ADD_TIER_PERCENTS,
    _resolve_sector_add_tier,
    _v3_add_tier_thresholds,
    _v3_gate_direction_score,
)
from app.services.sector_direction_backtest import (  # noqa: E402
    _clean_price_rows,
    load_direction_backtest_inputs_from_flow_cache,
    replay_sector_direction,
)
from app.services.sector_opportunity_scoring import (  # noqa: E402
    ENTRY_POLICY_VERSION_V3,
    ENTRY_READY_TO_START,
    EXIT_TREND_THRESHOLD,
    V3_TREND_TRANCHE_SCALES,
)

EPISODE_BUDGET = 100.0
#: 惩罚性赎回费的持有天数门槛（自然日），与证监会对开放式基金的规定一致。
SHORT_HOLD_DAYS = 7
#: 计算利润留存率所需的最小路径峰值收益（百分点）。低于它时比值全是噪声。
_MIN_PEAK_FOR_CAPTURE = 3.0
#: 生产阶梯的最低档，供"浮亏降档"口径复用，不另写数字。
_LOWEST_ADD_TIER_PERCENT = _ADD_TIER_PERCENTS[-1]


# --------------------------------------------------------------------------
# 费用与执行
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CostModel:
    buy_fee_percent: float = 0.12
    redeem_fee_percent: float = 0.5
    short_hold_redeem_fee_percent: float = 1.5

    def redeem_fee_for(self, hold_days: int) -> float:
        return (
            self.short_hold_redeem_fee_percent
            if hold_days < SHORT_HOLD_DAYS
            else self.redeem_fee_percent
        )


@dataclass
class Tranche:
    """一笔已成交的买入。份额按成交价折算，用于先进先出计费与成本核算。"""

    units: float
    price: float
    trade_date: str
    cash_in: float


@dataclass
class Position:
    tranches: list[Tranche] = field(default_factory=list)
    spent: float = 0.0
    fees_paid: float = 0.0
    buy_count: int = 0

    @property
    def units(self) -> float:
        return sum(item.units for item in self.tranches)

    @property
    def cost_basis(self) -> float:
        """已投入本金（不含费），用于算持有收益率。"""
        return sum(item.cash_in for item in self.tranches)

    def market_value(self, price: float) -> float:
        return self.units * price

    def last_add_price(self) -> float | None:
        return self.tranches[-1].price if self.tranches else None

    def return_percent(self, price: float) -> float | None:
        basis = self.cost_basis
        if basis <= 0:
            return None
        return (self.market_value(price) / basis - 1.0) * 100.0

    def buy(self, *, cash: float, price: float, trade_date: str, costs: CostModel) -> None:
        if cash <= 0 or price <= 0:
            return
        fee = cash * costs.buy_fee_percent / 100.0
        invested = cash - fee
        self.tranches.append(
            Tranche(
                units=invested / price,
                price=price,
                trade_date=trade_date,
                cash_in=invested,
            )
        )
        self.spent += cash
        self.fees_paid += fee
        self.buy_count += 1

    def liquidate(self, *, price: float, trade_date: str, costs: CostModel) -> float:
        """全部赎回，返回到手现金。赎回费按每笔份额自己的持有天数分别计。"""
        proceeds = 0.0
        exit_day = date.fromisoformat(trade_date)
        for item in self.tranches:
            hold_days = (exit_day - date.fromisoformat(item.trade_date)).days
            gross = item.units * price
            fee = gross * costs.redeem_fee_for(hold_days) / 100.0
            self.fees_paid += fee
            proceeds += gross - fee
        self.tranches = []
        return proceeds

    def redeem_fraction(
        self, *, fraction: float, price: float, trade_date: str, costs: CostModel
    ) -> float:
        """按当前份额比例赎回（先进先出），返回到手现金。供方向退出档位的部分减仓用。"""
        fraction = max(0.0, min(1.0, fraction))
        if fraction <= 0 or not self.tranches:
            return 0.0
        if fraction >= 1.0:
            return self.liquidate(price=price, trade_date=trade_date, costs=costs)
        to_redeem = self.units * fraction
        exit_day = date.fromisoformat(trade_date)
        proceeds = 0.0
        remaining: list[Tranche] = []
        for item in self.tranches:
            if to_redeem <= 0:
                remaining.append(item)
                continue
            take = min(item.units, to_redeem)
            to_redeem -= take
            hold_days = (exit_day - date.fromisoformat(item.trade_date)).days
            gross = take * price
            fee = gross * costs.redeem_fee_for(hold_days) / 100.0
            self.fees_paid += fee
            proceeds += gross - fee
            kept_units = item.units - take
            if kept_units > 1e-12:
                kept_ratio = kept_units / item.units
                remaining.append(
                    Tranche(
                        units=kept_units,
                        price=item.price,
                        trade_date=item.trade_date,
                        cash_in=item.cash_in * kept_ratio,
                    )
                )
        self.tranches = remaining
        return proceeds


# --------------------------------------------------------------------------
# 加仓梯形
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SizingPolicy:
    key: str
    label: str
    #: 首笔占 episode 预算的比例。
    initial_fraction: float
    #: 返回本次应投入的现金；返回 0 表示今天不加。
    add: Callable[["AddContext"], float]
    note: str = ""
    #: 忽略止损与趋势退出，只在最长持有期到点时平仓。用作"退出规则有没有帮上忙"的对照。
    ignore_exits: bool = False


@dataclass(frozen=True)
class AddContext:
    position: Position
    price: float
    remaining_budget: float
    #: 该方向今天是否仍是 ready_to_start（即线上"够格加仓"的必要条件）。
    signal_ready: bool
    #: 生产档位（百分比），仅在 signal_ready 时有意义。
    tier_percent: float
    #: 生产试仓系数。
    tranche_scale: float
    #: 阶梯式梯形用：已投出几笔。
    tranche_index: int


def _ladder_add(steps: tuple[float, ...], step_up: float) -> Callable[[AddContext], float]:
    """价格每上涨 `step_up`% 就投出下一笔；比例序列由 `steps` 给定（占总预算）。"""

    def add(ctx: AddContext) -> float:
        if ctx.tranche_index >= len(steps):
            return 0.0
        last = ctx.position.last_add_price()
        if last is None or ctx.price < last * (1.0 + step_up / 100.0):
            return 0.0
        return min(EPISODE_BUDGET * steps[ctx.tranche_index], ctx.remaining_budget)

    return add


def _current_of_holding(
    *, loss_behaviour: str = "none"
) -> Callable[[AddContext], float]:
    """线上现状：比例乘在**当前持仓市值**上。

    ``loss_behaviour`` 决定持有收益 <= 0 时怎么处理，三个取值对应三种候选口径：

    * ``none``   —— 不特殊处理（= 现状）；
    * ``block``  —— 不加（"只在浮盈上加仓"的强口径）；
    * ``floor``  —— 降到既有阶梯最低档（弱口径，与滞回试探同一档）。
    """

    def add(ctx: AddContext) -> float:
        if not ctx.signal_ready:
            return 0.0
        tier = ctx.tier_percent
        if loss_behaviour != "none":
            ret = ctx.position.return_percent(ctx.price)
            in_loss = ret is None or ret <= 0
            if in_loss:
                if loss_behaviour == "block":
                    return 0.0
                tier = min(tier, _LOWEST_ADD_TIER_PERCENT)
        cash = ctx.position.market_value(ctx.price) * tier / 100.0 * ctx.tranche_scale
        return min(cash, ctx.remaining_budget)

    return add


def _current_of_budget(ctx: AddContext) -> float:
    """建议改法：比例乘在**尚未投出的预算**上（= 计划仓位 − 当前持仓）。"""
    if not ctx.signal_ready:
        return 0.0
    cash = ctx.remaining_budget * ctx.tier_percent / 100.0 * ctx.tranche_scale
    return min(cash, ctx.remaining_budget)


def _no_add(_ctx: AddContext) -> float:
    return 0.0


def build_policies(*, base_fraction: float) -> tuple[SizingPolicy, ...]:
    return (
        SizingPolicy(
            key="single_shot_no_exit",
            label="一次买满·不设退出（对照）",
            initial_fraction=1.0,
            add=_no_add,
            note="只到期平仓，用来判断止损与趋势退出到底是加分还是减分",
            ignore_exits=True,
        ),
        SizingPolicy(
            key="single_shot",
            label="一次买满（对照）",
            initial_fraction=1.0,
            add=_no_add,
            note="信号日一次投满预算，衡量「完全暴露」的上界",
        ),
        SizingPolicy(
            key="livermore_20_20_20_40",
            label="利弗莫尔 20/20/20/40",
            initial_fraction=0.20,
            add=_ladder_add((0.20, 0.20, 0.40), step_up=10.0),
            note="倒金字塔：每涨 10% 加一笔，最后一笔最大",
        ),
        SizingPolicy(
            key="pyramid_40_30_20_10",
            label="递减金字塔 40/30/20/10",
            initial_fraction=0.40,
            add=_ladder_add((0.30, 0.20, 0.10), step_up=10.0),
            note="同样的触发条件，首笔最大",
        ),
        SizingPolicy(
            key="current_of_holding",
            label="线上现状（比例乘当前持仓）",
            initial_fraction=base_fraction,
            add=_current_of_holding(loss_behaviour="none"),
            note="档位由生产 _resolve_sector_add_tier 给出，再乘趋势试仓系数",
        ),
        SizingPolicy(
            key="current_of_holding_profit_gate",
            label="现状 + 浮亏不加（强口径）",
            initial_fraction=base_fraction,
            add=_current_of_holding(loss_behaviour="block"),
            note="唯一改动：持有收益 <= 0 时完全不加",
        ),
        SizingPolicy(
            key="current_of_holding_loss_floor",
            label="现状 + 浮亏降到最低档（弱口径）",
            initial_fraction=base_fraction,
            add=_current_of_holding(loss_behaviour="floor"),
            note=f"唯一改动：持有收益 <= 0 时档位封到 {_LOWEST_ADD_TIER_PERCENT:g}%",
        ),
        SizingPolicy(
            key="current_of_budget",
            label="改分母（比例乘剩余预算）",
            initial_fraction=base_fraction,
            add=_current_of_budget,
            note="同档位同系数，只把分母换成尚未投出的预算",
        ),
    )


# --------------------------------------------------------------------------
# 信号表
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    entry_state: str
    direction_score: float | None
    trend_strength: float | None

    @property
    def ready(self) -> bool:
        return self.entry_state == ENTRY_READY_TO_START

    def tier_percent(self) -> float:
        percent, _basis = _resolve_sector_add_tier(
            {
                "score_policy_version": ENTRY_POLICY_VERSION_V3,
                "direction_score": self.direction_score,
            }
        )
        return percent

    def tranche_scale(self) -> float:
        trend = self.trend_strength
        if trend is None:
            return 0.0
        for threshold, scale in V3_TREND_TRANCHE_SCALES:
            if trend >= threshold:
                return scale
        return 0.0

    def trend_below_exit(self) -> bool:
        return self.trend_strength is not None and self.trend_strength < EXIT_TREND_THRESHOLD


# --------------------------------------------------------------------------
# 加仓档位边界变体（tier threshold sweep）
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TierVariant:
    """一组档位边界。档位百分比集合（20/15/10/5）是产品策略、固定不动；变体只改
    「direction_score 落到哪一档」的分界线——生产取法是 (入场合成分 → 85) 等分，这个
    等分本身没有回测依据，本 sweep 就是给它补的。"""

    key: str
    label: str
    #: 高→低档的下界；最后一档兜底（-inf）。长度与 `_ADD_TIER_PERCENTS` 一致。
    thresholds: tuple[float, ...]
    #: 非 None 时忽略边界，所有分数一律该档——回答"分档本身有没有贡献"。
    flat_percent: float | None = None

    def tier_percent(self, direction_score: float | None) -> float:
        if self.flat_percent is not None:
            return self.flat_percent
        if direction_score is None:
            # 分数缺失落最低档；变体间保持同一兜底才可比。
            return _ADD_TIER_PERCENTS[-1]
        for threshold, percent in zip(self.thresholds, _ADD_TIER_PERCENTS):
            if direction_score >= threshold:
                return percent
        return _ADD_TIER_PERCENTS[-1]


def _equal_split_thresholds(top: float) -> tuple[float, ...]:
    """按生产同一取法（入场合成分 → `top` 等分）生成边界，只是允许换上锚点。"""
    gate = _v3_gate_direction_score()
    rungs = len(_ADD_TIER_PERCENTS)
    span = top - gate
    return tuple(
        gate + span * (rungs - 1 - index) / rungs for index in range(rungs - 1)
    ) + (float("-inf"),)


def build_tier_variants() -> tuple[TierVariant, ...]:
    production = _v3_add_tier_thresholds()
    shift = 2.0
    return (
        TierVariant("tier_production", "生产（等分 gate→85）", production),
        TierVariant("tier_top_80", "上锚 80（更易拿满档）", _equal_split_thresholds(80.0)),
        TierVariant("tier_top_90", "上锚 90（更难拿满档）", _equal_split_thresholds(90.0)),
        TierVariant(
            "tier_shift_up_2",
            "边界整体 +2（更保守）",
            tuple(value + shift for value in production[:-1]) + (float("-inf"),),
        ),
        TierVariant(
            "tier_shift_down_2",
            "边界整体 −2（更宽松）",
            tuple(value - shift for value in production[:-1]) + (float("-inf"),),
        ),
        # 分档 vs 不分档的根本问题：全部固定中间档，看分档本身有没有贡献。
        TierVariant(
            "tier_flat_mid",
            f"不分档（固定 {_ADD_TIER_PERCENTS[2]:g}%）",
            production,
            flat_percent=_ADD_TIER_PERCENTS[2],
        ),
    )


# --------------------------------------------------------------------------
# Episode 模拟
# --------------------------------------------------------------------------


@dataclass
class EpisodeResult:
    policy_key: str
    sector_label: str
    signal_date: str
    entry_date: str
    exit_date: str
    exit_reason: str
    return_on_budget_percent: float
    return_on_deployed_percent: float | None
    deployed_percent: float
    buy_count: int
    fees_percent_of_budget: float
    max_drawdown_percent: float
    peak_profit_percent: float
    giveback_percent: float | None
    hold_days: int
    censored: bool


def simulate_episode(
    *,
    policy: SizingPolicy,
    prices: list[dict[str, Any]],
    signal_index: int,
    signals_by_date: dict[str, Signal],
    sector_label: str,
    costs: CostModel,
    max_days: int,
    stop_percent: float,
    use_trend_exit: bool,
    tier_variant: TierVariant | None = None,
) -> EpisodeResult | None:
    """信号在 `signal_index` 日收盘产生；首笔在下一收盘成交。"""
    entry_index = signal_index + 1
    if entry_index >= len(prices):
        return None

    position = Position()
    remaining = EPISODE_BUDGET
    entry_date = str(prices[entry_index]["date"])
    entry_price = float(prices[entry_index]["close"])
    initial_cash = min(EPISODE_BUDGET * policy.initial_fraction, remaining)
    position.buy(
        cash=initial_cash, price=entry_price, trade_date=entry_date, costs=costs
    )
    remaining -= initial_cash

    peak_price = entry_price
    peak_value = position.market_value(entry_price) + remaining
    max_drawdown = 0.0
    peak_profit = 0.0
    pending_exit = False
    pending_add = 0.0
    exit_reason = "max_days"
    exit_index = min(entry_index + max_days, len(prices) - 1)
    censored = entry_index + max_days > len(prices) - 1

    cursor = entry_index
    while cursor < min(entry_index + max_days, len(prices) - 1):
        cursor += 1
        day = str(prices[cursor]["date"])
        price = float(prices[cursor]["close"])

        # 先执行上一交易日收盘决定的动作（T+1）。
        if pending_exit:
            exit_index = cursor
            break
        if pending_add > 0:
            cash = min(pending_add, remaining)
            position.buy(cash=cash, price=price, trade_date=day, costs=costs)
            remaining -= cash
            pending_add = 0.0

        peak_price = max(peak_price, price)
        total_value = position.market_value(price) + remaining
        peak_value = max(peak_value, total_value)
        if peak_value > 0:
            max_drawdown = max(max_drawdown, (peak_value - total_value) / peak_value * 100.0)
        peak_profit = max(peak_profit, (total_value / EPISODE_BUDGET - 1.0) * 100.0)

        signal = signals_by_date.get(day)

        # 退出判定（收盘触发、次日执行）。
        if not policy.ignore_exits:
            if price <= peak_price * (1.0 - stop_percent / 100.0):
                pending_exit = True
                exit_reason = "trailing_stop"
                continue
            if use_trend_exit and signal is not None and signal.trend_below_exit():
                pending_exit = True
                exit_reason = "trend_exit"
                continue

        if remaining > 0:
            if signal is None:
                tier_percent = 0.0
            elif tier_variant is not None:
                tier_percent = tier_variant.tier_percent(signal.direction_score)
            else:
                tier_percent = signal.tier_percent()
            ctx = AddContext(
                position=position,
                price=price,
                remaining_budget=remaining,
                signal_ready=bool(signal and signal.ready),
                tier_percent=tier_percent,
                tranche_scale=signal.tranche_scale() if signal else 0.0,
                tranche_index=max(position.buy_count - 1, 0),
            )
            pending_add = policy.add(ctx)

    exit_date = str(prices[exit_index]["date"])
    exit_price = float(prices[exit_index]["close"])
    proceeds = position.liquidate(price=exit_price, trade_date=exit_date, costs=costs)
    final_value = proceeds + remaining
    deployed = position.spent
    return_on_budget = (final_value / EPISODE_BUDGET - 1.0) * 100.0
    return_on_deployed = (
        (final_value - (EPISODE_BUDGET - deployed)) / deployed - 1.0
    ) * 100.0 if deployed > 0 else None
    # 利润留存率 = 终值收益 / 路径峰值收益。峰值太小时这个比值会被噪声放大成任意数字，
    # 因此只在峰值至少 `_MIN_PEAK_FOR_CAPTURE` 时才计算，并在汇总里取中位数。
    giveback = (
        return_on_budget / peak_profit * 100.0
        if peak_profit >= _MIN_PEAK_FOR_CAPTURE
        else None
    )
    return EpisodeResult(
        policy_key=policy.key,
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


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------


def summarize(results: list[EpisodeResult]) -> dict[str, Any]:
    if not results:
        return {"available": False, "episode_count": 0}
    budget = [item.return_on_budget_percent for item in results]
    deployed = [
        item.return_on_deployed_percent
        for item in results
        if item.return_on_deployed_percent is not None
    ]
    givebacks = [
        item.giveback_percent for item in results if item.giveback_percent is not None
    ]
    reasons: dict[str, int] = {}
    for item in results:
        reasons[item.exit_reason] = reasons.get(item.exit_reason, 0) + 1
    return {
        "available": True,
        "episode_count": len(results),
        "mean_return_on_budget_percent": round(fmean(budget), 3),
        "median_return_on_budget_percent": round(median(budget), 3),
        "total_return_on_budget_percent": round(sum(budget), 1),
        "hit_rate_percent": round(
            sum(value > 0 for value in budget) / len(budget) * 100.0, 1
        ),
        "mean_return_on_deployed_percent": (
            round(fmean(deployed), 3) if deployed else None
        ),
        "mean_deployed_percent": round(
            fmean(item.deployed_percent for item in results), 1
        ),
        "mean_buy_count": round(fmean(item.buy_count for item in results), 2),
        "mean_fees_percent_of_budget": round(
            fmean(item.fees_percent_of_budget for item in results), 3
        ),
        "mean_max_drawdown_percent": round(
            fmean(item.max_drawdown_percent for item in results), 2
        ),
        "worst_episode_percent": round(min(budget), 2),
        "p10_return_on_budget_percent": round(_percentile(budget, 10.0), 2),
        "mean_peak_profit_percent": round(
            fmean(item.peak_profit_percent for item in results), 2
        ),
        "median_capture_ratio_percent": round(median(givebacks), 1) if givebacks else None,
        "capture_ratio_sample": len(givebacks),
        "mean_hold_days": round(fmean(item.hold_days for item in results), 1),
        "exit_reasons": reasons,
    }


#: 需要配对检验的几组对比。每组只差一个设计选择，配对后共用同一批 episode，
#: 因此可以直接对逐 episode 差值做检验——这比比较两个均值敏感得多。
_PAIRED_COMPARISONS: tuple[tuple[str, str, str], ...] = (
    (
        "current_of_holding_profit_gate",
        "current_of_holding",
        "浮亏不加（强）vs 现状",
    ),
    (
        "current_of_holding_loss_floor",
        "current_of_holding",
        "浮亏降到最低档（弱）vs 现状",
    ),
    (
        "current_of_holding_profit_gate",
        "current_of_holding_loss_floor",
        "强口径 vs 弱口径",
    ),
    (
        "pyramid_40_30_20_10",
        "livermore_20_20_20_40",
        "递减金字塔 vs 倒金字塔（利弗莫尔）",
    ),
    ("current_of_budget", "current_of_holding", "改分母 vs 现状"),
    ("single_shot", "single_shot_no_exit", "有退出规则 vs 无退出规则"),
)


def paired_stats(
    left: list[EpisodeResult], right: list[EpisodeResult]
) -> dict[str, Any]:
    """逐 episode 差值的配对检验（左 − 右）。两边 episode 必须一一对应。"""
    by_key = {(item.sector_label, item.signal_date): item for item in right}
    diffs: list[float] = []
    for item in left:
        counterpart = by_key.get((item.sector_label, item.signal_date))
        if counterpart is None:
            continue
        diffs.append(
            item.return_on_budget_percent - counterpart.return_on_budget_percent
        )
    if len(diffs) < 3:
        return {"available": False, "n": len(diffs)}
    mean = fmean(diffs)
    variance = sum((value - mean) ** 2 for value in diffs) / (len(diffs) - 1)
    std = variance**0.5
    t_stat = mean / (std / len(diffs) ** 0.5) if std > 0 else None
    non_zero = [value for value in diffs if abs(value) > 1e-9]
    return {
        "available": True,
        "n": len(diffs),
        "mean_diff_percent": round(mean, 3),
        "median_diff_percent": round(median(diffs), 3),
        "win_ratio_percent": (
            round(sum(value > 0 for value in non_zero) / len(non_zero) * 100.0, 1)
            if non_zero
            else None
        ),
        "n_differing": len(non_zero),
        "t_stat": round(t_stat, 2) if t_stat is not None else None,
        "significant": bool(t_stat is not None and abs(t_stat) >= 2.0),
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * max(0.0, min(100.0, percentile)) / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


@dataclass
class Prepared:
    """一次装载 + 一次重放的产物，供多组参数复用（重放是全流程里最慢的一步）。"""

    signals: dict[str, dict[str, Signal]]
    prices_by_label: dict[str, list[dict[str, Any]]]
    index_by_label: dict[str, dict[str, int]]
    benchmark_key: str
    decision_day_count: int
    caveats: list[str]
    #: 样本期的市场环境。"加仓要不要更大"这个问题在涨市与跌市里答案本来就不同，
    #: 所以行情基调必须和结论放在一起，否则读者会把单一区间的结果当普适结论。
    market_context: dict[str, Any]


def prepare(*, sqlite_cache: str, warmup_days: int) -> Prepared:
    loaded = load_direction_backtest_inputs_from_flow_cache(
        sqlite_cache, min_history_days=warmup_days
    )
    price_series_by_label = loaded["price_series_by_label"]
    if not price_series_by_label:
        raise SystemExit("缓存里没有任何板块达到最小历史长度，无法模拟")

    benchmark_key = next(iter(loaded["benchmark_series_by_key"]))
    replay = replay_sector_direction(
        price_series_by_label=price_series_by_label,
        benchmark_series=loaded["benchmark_series_by_key"][benchmark_key],
        flow_series_by_label=loaded.get("flow_series_by_label") or {},
        # 只用重放层拿 PIT 信号；路径由本脚本自己按原始日线走，所以取最短前瞻窗口
        # 以换取最多的决策日。
        forward_horizons=(5,),
        warmup_days=warmup_days,
        step=1,
        price_source=loaded.get("price_source") or "backtest_daily_kline",
        benchmark_label=benchmark_key,
        entry_policy_version=ENTRY_POLICY_VERSION_V3,
    )

    signals: dict[str, dict[str, Signal]] = {}
    for item in replay.observations:
        signals.setdefault(item.sector_label, {})[item.decision_date] = Signal(
            entry_state=item.entry_state,
            direction_score=item.factors.get("direction_score"),
            trend_strength=item.factors.get("trend_strength_score"),
        )

    prices_by_label = {
        label: _clean_price_rows(rows) for label, rows in price_series_by_label.items()
    }
    index_by_label = {
        label: {str(row["date"]): position for position, row in enumerate(rows)}
        for label, rows in prices_by_label.items()
    }
    return Prepared(
        signals=signals,
        prices_by_label=prices_by_label,
        index_by_label=index_by_label,
        benchmark_key=benchmark_key,
        decision_day_count=len(replay.decision_dates),
        caveats=list(replay.caveats),
        market_context=_market_context(
            _clean_price_rows(loaded["benchmark_series_by_key"][benchmark_key]),
            prices_by_label,
        ),
    )


def _market_context(
    benchmark: list[dict[str, Any]],
    prices_by_label: dict[str, list[dict[str, Any]]],
    *,
    horizon: int = 20,
) -> dict[str, Any]:
    """样本期基调 + 无条件持有 `horizon` 天的收益分布（策略的"什么都不做"底线）。"""
    if len(benchmark) < 2:
        return {"available": False}
    levels = [float(row["close"]) for row in benchmark]
    peak = levels[0]
    max_drawdown = 0.0
    for value in levels:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak * 100.0)
    unconditional: list[float] = []
    for rows in prices_by_label.values():
        for index in range(len(rows) - horizon):
            start = float(rows[index]["close"])
            end = float(rows[index + horizon]["close"])
            if start > 0:
                unconditional.append((end / start - 1.0) * 100.0)
    return {
        "available": True,
        "start_date": str(benchmark[0]["date"]),
        "end_date": str(benchmark[-1]["date"]),
        "benchmark_total_return_percent": round((levels[-1] / levels[0] - 1.0) * 100.0, 2),
        "benchmark_max_drawdown_percent": round(-max_drawdown, 2),
        "unconditional_horizon_days": horizon,
        "unconditional_mean_percent": (
            round(fmean(unconditional), 2) if unconditional else None
        ),
        "unconditional_median_percent": (
            round(median(unconditional), 2) if unconditional else None
        ),
        "unconditional_positive_share_percent": (
            round(sum(value > 0 for value in unconditional) / len(unconditional) * 100.0, 1)
            if unconditional
            else None
        ),
    }


def simulate_all(
    prepared: Prepared,
    *,
    max_days: int,
    stop_percent: float,
    use_trend_exit: bool,
    base_fraction: float,
    warmup_days: int,
    costs: CostModel,
    sqlite_cache: str,
) -> dict[str, Any]:
    signals = prepared.signals
    prices_by_label = prepared.prices_by_label
    index_by_label = prepared.index_by_label
    benchmark_key = prepared.benchmark_key

    policies = build_policies(base_fraction=base_fraction)
    results: dict[str, list[EpisodeResult]] = {policy.key: [] for policy in policies}
    censored_count = 0
    episode_count = 0

    for label, by_date in sorted(signals.items()):
        ordered_dates = sorted(by_date)
        previous_ready = False
        for day in ordered_dates:
            signal = by_date[day]
            # episode = 方向**新进入**可布局状态的那一天（连续 ready 只开一次）。
            if signal.ready and not previous_ready:
                cursor = index_by_label[label].get(day)
                if cursor is not None:
                    episode_count += 1
                    for policy in policies:
                        outcome = simulate_episode(
                            policy=policy,
                            prices=prices_by_label[label],
                            signal_index=cursor,
                            signals_by_date=by_date,
                            sector_label=label,
                            costs=costs,
                            max_days=max_days,
                            stop_percent=stop_percent,
                            use_trend_exit=use_trend_exit,
                        )
                        if outcome is None:
                            continue
                        if outcome.censored:
                            continue
                        results[policy.key].append(outcome)
            previous_ready = signal.ready

    complete = len(results[policies[0].key])
    censored_count = episode_count - complete

    payload = {
        "schema_version": "position_sizing_backtest.v1",
        "decision_policy": "shadow_record_only",
        "auto_tuning_eligible": False,
        "params": {
            "sqlite_cache": sqlite_cache,
            "benchmark_label": benchmark_key,
            "max_episode_days": max_days,
            "trailing_stop_percent": stop_percent,
            "trend_exit_enabled": use_trend_exit,
            "trend_exit_line": EXIT_TREND_THRESHOLD,
            "base_fraction_for_current_policies": base_fraction,
            "warmup_days": warmup_days,
            "costs": {
                "buy_fee_percent": costs.buy_fee_percent,
                "redeem_fee_percent": costs.redeem_fee_percent,
                "short_hold_redeem_fee_percent": costs.short_hold_redeem_fee_percent,
                "short_hold_days": SHORT_HOLD_DAYS,
            },
        },
        "label_count": len(prices_by_label),
        "decision_day_count": prepared.decision_day_count,
        "market_context": prepared.market_context,
        "episode_count_total": episode_count,
        "episode_count_evaluated": complete,
        "episode_count_censored": censored_count,
        "policies": [
            {
                "key": policy.key,
                "label": policy.label,
                "note": policy.note,
                "initial_fraction": policy.initial_fraction,
                "summary": summarize(results[policy.key]),
            }
            for policy in policies
        ],
        "paired_comparisons": [
            {
                "label": label,
                "left": left,
                "right": right,
                **paired_stats(results.get(left) or [], results.get(right) or []),
            }
            for left, right, label in _PAIRED_COMPARISONS
        ],
        "caveats": [
            *prepared.caveats,
            "标的是板块指数，不是可买到的基金：跟踪误差、净值滞后、QDII 赎回周期未建模。",
            "过热标记不在重放观测里，试仓系数只按趋势强度分档，线上实际更保守。",
            "基金证据降档、载体质量降档、集中度上限、新闻与交易门禁均未建模。",
            "每个 episode 独立 100 单位预算，不模拟组合层资金竞争。",
            "被数据末端截断的 episode 已从统计中剔除，剔除数见 episode_count_censored。",
        ],
    }

    return payload


def simulate_tier_threshold_sweep(
    prepared: Prepared,
    *,
    max_days: int,
    stop_percent: float,
    use_trend_exit: bool,
    base_fraction: float,
    costs: CostModel,
) -> dict[str, Any]:
    """档位**边界**的敏感性：固定当前生产梯形（现状 + 浮亏封档），只换分界线。

    与主表同一批 episode、同一套退出规则，因此每个变体都能与生产边界做逐 episode 配对。
    输出 `shadow_record_only`：边界与档位数值都不会被自动改动；等分取法若被证明不如某个
    变体，也只取得人工评审资格。
    """
    variants = build_tier_variants()
    # 用当前生产口径的加仓行为（浮亏封档已上线）作载体，让"换边界"是唯一变量。
    policy = next(
        item
        for item in build_policies(base_fraction=base_fraction)
        if item.key == "current_of_holding_loss_floor"
    )
    results: dict[str, list[EpisodeResult]] = {variant.key: [] for variant in variants}
    for label, by_date in sorted(prepared.signals.items()):
        ordered_dates = sorted(by_date)
        previous_ready = False
        for day in ordered_dates:
            signal = by_date[day]
            if signal.ready and not previous_ready:
                cursor = prepared.index_by_label[label].get(day)
                if cursor is not None:
                    for variant in variants:
                        outcome = simulate_episode(
                            policy=policy,
                            prices=prepared.prices_by_label[label],
                            signal_index=cursor,
                            signals_by_date=by_date,
                            sector_label=label,
                            costs=costs,
                            max_days=max_days,
                            stop_percent=stop_percent,
                            use_trend_exit=use_trend_exit,
                            tier_variant=variant,
                        )
                        if outcome is not None and not outcome.censored:
                            results[variant.key].append(outcome)
            previous_ready = signal.ready

    baseline_key = variants[0].key
    return {
        "schema_version": "add_tier_threshold_sweep.v1",
        "decision_policy": "shadow_record_only",
        "auto_tuning_eligible": False,
        "carrier_policy": policy.key,
        "params": {
            "max_episode_days": max_days,
            "trailing_stop_percent": stop_percent,
            "trend_exit_enabled": use_trend_exit,
            "tier_percents": list(_ADD_TIER_PERCENTS),
        },
        "variants": [
            {
                "key": variant.key,
                "label": variant.label,
                "thresholds": [
                    round(value, 2) for value in variant.thresholds if value != float("-inf")
                ],
                "flat_percent": variant.flat_percent,
                "summary": summarize(results[variant.key]),
                **(
                    {
                        "paired_vs_production": paired_stats(
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
            "档位百分比集合（20/15/10/5）固定不动，本 sweep 只评估分界线的取法。",
            "载体是「现状 + 浮亏封档」梯形；换载体可能改变排序，结论不能跨载体外推。",
            "样本期与主表相同（见 market_context），单一区间结论不能外推。",
        ],
    }


_COLUMNS = ("梯形", "样本", "均值%", "中位%", "胜率%", "投出%", "笔数", "费用%", "回撤%", "峰值%", "留存%", "最差%")


def _render(payload: dict[str, Any]) -> str:
    lines = [
        "加仓梯形对比（同一批 PIT 入场信号，只换资金路径）",
        f"schema: {payload['schema_version']}   基准: {payload['params']['benchmark_label']}",
        f"板块 {payload['label_count']} 个   决策日 {payload['decision_day_count']}   "
        f"episode {payload['episode_count_evaluated']} 个"
        f"（另有 {payload['episode_count_censored']} 个被数据末端截断，已剔除）",
        f"退出规则: 从最高收盘回撤 {payload['params']['trailing_stop_percent']:g}% 止损"
        + (
            f" 或趋势跌破 {payload['params']['trend_exit_line']:g}"
            if payload["params"]["trend_exit_enabled"]
            else ""
        )
        + f"，最长持有 {payload['params']['max_episode_days']} 个交易日；均为收盘触发、次日收盘执行",
        "⚠ 研究输出，shadow_record_only：不自动改动任何线上参数。",
        "",
    ]
    context = payload.get("market_context") or {}
    if context.get("available"):
        lines.extend(
            [
                f"  样本期行情基调（{context['start_date']} ~ {context['end_date']}）："
                f"基准累计 {context['benchmark_total_return_percent']:+.2f}%，"
                f"最大回撤 {context['benchmark_max_drawdown_percent']:.2f}%；"
                f"全板块无条件持有 {context['unconditional_horizon_days']} 日的收益"
                f"均值 {context['unconditional_mean_percent']:+.2f}%、"
                f"中位 {context['unconditional_median_percent']:+.2f}%、"
                f"为正占 {context['unconditional_positive_share_percent']}%。",
                "  ⚠ 这是一个下行区间。「加仓该不该更大」在涨市与跌市里答案本来不同，"
                "本表只能说明在这段行情里谁更好，不能外推。",
                "",
            ]
        )
    lines.extend(
        [
            "  收益均以 episode 预算（100 单位）为分母，未投出的现金按 0 收益计——"
            "这样「少投入」就不会因为分母小而显得更划算。",
            "",
        ]
    )
    header = "  " + f"{_COLUMNS[0]:<28}" + "".join(f"{name:>9}" for name in _COLUMNS[1:])
    lines.append(header)
    for entry in payload["policies"]:
        stats = entry["summary"]
        if not stats.get("available"):
            lines.append(f"  {entry['label']:<28}" + "样本不足".rjust(9))
            continue
        cells = [
            f"{stats['episode_count']}",
            f"{stats['mean_return_on_budget_percent']:+.2f}",
            f"{stats['median_return_on_budget_percent']:+.2f}",
            f"{stats['hit_rate_percent']:.1f}",
            f"{stats['mean_deployed_percent']:.1f}",
            f"{stats['mean_buy_count']:.2f}",
            f"{stats['mean_fees_percent_of_budget']:.2f}",
            f"{stats['mean_max_drawdown_percent']:.2f}",
            f"{stats['mean_peak_profit_percent']:+.2f}",
            (
                f"{stats['median_capture_ratio_percent']:.0f}"
                if stats["median_capture_ratio_percent"] is not None
                else "—"
            ),
            f"{stats['worst_episode_percent']:+.2f}",
        ]
        label = entry["label"]
        padding = max(0, 28 - sum(2 if ord(ch) > 0x2E80 else 1 for ch in label))
        lines.append("  " + label + " " * padding + "".join(f"{c:>9}" for c in cells))
    lines.append("")
    lines.append("  配对检验（同一批 episode 上逐个相减，左 − 右）：")
    for row in payload.get("paired_comparisons") or []:
        if not row.get("available"):
            lines.append(f"    {row['label']}: 样本不足")
            continue
        lines.append(
            f"    {row['label']}: 均值差 {row['mean_diff_percent']:+.3f}%，"
            f"中位差 {row['median_diff_percent']:+.3f}%，"
            f"占优 {row['win_ratio_percent']}%（{row['n_differing']}/{row['n']} 个有差异），"
            f"t={row['t_stat']} → {'显著' if row['significant'] else '不显著'}"
        )
    lines.append("")
    lines.append("  退出原因分布：")
    for entry in payload["policies"]:
        stats = entry["summary"]
        if stats.get("available"):
            lines.append(f"    {entry['label']}: {stats['exit_reasons']}")
    lines.append("")
    lines.append("-" * 100)
    lines.append("已知缺口与口径限制：")
    for caveat in payload["caveats"]:
        lines.append(f"  ⚠ {caveat}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="加仓梯形对比（离线研究）")
    parser.add_argument("--sqlite-cache", required=True)
    parser.add_argument("--max-days", type=int, default=40)
    parser.add_argument("--stop-percent", type=float, default=10.0)
    parser.add_argument(
        "--no-trend-exit",
        action="store_true",
        help="只用移动止损退出，不叠加生产的趋势退出线",
    )
    parser.add_argument(
        "--base-fraction",
        type=float,
        default=0.20,
        help="现状类梯形的首仓占预算比例（默认 0.20，取自 discovery 的首仓上限）",
    )
    parser.add_argument("--warmup-days", type=int, default=61)
    parser.add_argument("--buy-fee-percent", type=float, default=0.12)
    parser.add_argument("--redeem-fee-percent", type=float, default=0.5)
    parser.add_argument("--short-hold-redeem-fee-percent", type=float, default=1.5)
    parser.add_argument(
        "--out-dir", type=str, default=str(API_ROOT / "var" / "position_sizing")
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="在 (最长持有期 x 止损幅度) 网格上重跑，用来判断结论对参数有多敏感",
    )
    parser.add_argument(
        "--sweep-tier-thresholds",
        action="store_true",
        help="固定加仓梯形，只扫描档位分界线（生产等分 vs 换锚/平移/不分档）",
    )
    args = parser.parse_args()

    costs = CostModel(
        buy_fee_percent=args.buy_fee_percent,
        redeem_fee_percent=args.redeem_fee_percent,
        short_hold_redeem_fee_percent=args.short_hold_redeem_fee_percent,
    )
    prepared = prepare(sqlite_cache=args.sqlite_cache, warmup_days=args.warmup_days)
    out_path = Path(args.out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    configs = (
        [
            (days, stop)
            for days in (10, 20, 40)
            for stop in (8.0, 10.0, 15.0)
        ]
        if args.sweep
        else [(args.max_days, args.stop_percent)]
    )

    runs: list[dict[str, Any]] = []
    for days, stop in configs:
        runs.append(
            simulate_all(
                prepared,
                max_days=days,
                stop_percent=stop,
                use_trend_exit=not args.no_trend_exit,
                base_fraction=args.base_fraction,
                warmup_days=args.warmup_days,
                costs=costs,
                sqlite_cache=args.sqlite_cache,
            )
        )

    (out_path / "summary.json").write_text(
        json.dumps(runs if args.sweep else runs[0], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = "\n".join(_render(item) for item in runs)
    if args.sweep:
        report += "\n" + _render_sweep(runs)
    if args.sweep_tier_thresholds:
        tier_sweep = simulate_tier_threshold_sweep(
            prepared,
            max_days=args.max_days,
            stop_percent=args.stop_percent,
            use_trend_exit=not args.no_trend_exit,
            base_fraction=args.base_fraction,
            costs=costs,
        )
        (out_path / "tier_threshold_sweep.json").write_text(
            json.dumps(tier_sweep, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report += "\n" + _render_tier_sweep(tier_sweep)
    (out_path / "report.txt").write_text(report, encoding="utf-8")
    print(f"报告已写入: {out_path / 'report.txt'}")
    return 0


def _render_tier_sweep(payload: dict[str, Any]) -> str:
    lines = [
        "=" * 100,
        "加仓档位分界线敏感性（载体固定为「现状 + 浮亏封档」，只换分界线）",
        "  档位百分比集合不动；等分取法没有回测依据，本表就是给它补的。",
        "  ⚠ shadow_record_only：排序更好也只取得人工评审资格，不自动改线上。",
        "",
    ]
    for entry in payload.get("variants") or []:
        stats = entry["summary"]
        if not stats.get("available"):
            lines.append(f"  {entry['label']}: 样本不足")
            continue
        row = (
            f"  {entry['label']}: 均值 {stats['mean_return_on_budget_percent']:+.3f}%，"
            f"投出 {stats['mean_deployed_percent']:.1f}%，"
            f"费用 {stats['mean_fees_percent_of_budget']:.2f}%，"
            f"回撤 {stats['mean_max_drawdown_percent']:.2f}%"
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


def _render_sweep(runs: list[dict[str, Any]]) -> str:
    """把网格压成一张"每种梯形在各参数下的预算收益均值"表，看排序是否稳定。"""
    keys = [entry["key"] for entry in runs[0]["policies"]]
    labels = {entry["key"]: entry["label"] for entry in runs[0]["policies"]}
    lines = [
        "=" * 100,
        "参数敏感性：各梯形在 (最长持有期, 止损幅度) 网格下的预算收益均值 %",
        "  排序若随参数翻转，说明这份数据分不出优劣，不能拿去改线上。",
        "",
    ]
    header = "  " + f"{'梯形':<30}"
    for item in runs:
        header += f"{item['params']['max_episode_days']}d/{item['params']['trailing_stop_percent']:g}%".rjust(11)
    lines.append(header)
    for key in keys:
        label = labels[key]
        padding = max(0, 30 - sum(2 if ord(ch) > 0x2E80 else 1 for ch in label))
        row = "  " + label + " " * padding
        for item in runs:
            stats = next(e["summary"] for e in item["policies"] if e["key"] == key)
            row += (
                f"{stats['mean_return_on_budget_percent']:+.2f}".rjust(11)
                if stats.get("available")
                else "—".rjust(11)
            )
        lines.append(row)
    lines.append("")
    lines.append("  各列 episode 样本数：")
    counts = "  " + " ".join(
        f"{item['params']['max_episode_days']}d={item['episode_count_evaluated']}"
        for item in runs
    )
    lines.append(counts)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
