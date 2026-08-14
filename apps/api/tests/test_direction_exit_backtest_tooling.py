"""方向退出参数回测入口与档位边界 sweep 的工具正确性。

这两个脚本是给 `PERSISTENT_BREAKDOWN_DAYS` / `RELATIVE_TREND_DECAY_POINTS`（契约标注
`thresholds_validated=false`）和加仓档位等分边界补回测的**入口**。工具本身错了，跑出来
的"参数结论"只会更危险，所以路径逻辑必须先被锁住：

* 档位边界变体必须与生产 `_resolve_sector_add_tier` 在生产边界下逐点一致；
* 部分赎回必须先进先出、费率按每笔持有天数各计各的；
* 退出路径必须按状态升级执行（−25% → −50% → 清仓），对照组不受影响；
* 样本不足时**不产出任何参数结论**（insufficient_data），这是数据充分性检查的意义。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(API_ROOT / "scripts"))

import run_direction_exit_backtest as exit_bt  # noqa: E402
import run_position_sizing_backtest as sizing  # noqa: E402

from app.services.recommendation_guard import (  # noqa: E402
    _ADD_TIER_PERCENTS,
    _resolve_sector_add_tier,
)
from app.services.sector_opportunity_scoring import (  # noqa: E402
    ENTRY_POLICY_VERSION_V3,
)

_COSTS = sizing.CostModel()


# --------------------------------------------------------------------------
# 档位边界变体
# --------------------------------------------------------------------------


def test_production_variant_matches_the_shipped_tier_resolver() -> None:
    production = sizing.build_tier_variants()[0]
    for score in (45.0, 51.0, 59.5, 60.0, 68.0, 76.5, 85.0, 99.0):
        expected, _basis = _resolve_sector_add_tier(
            {
                "score_policy_version": ENTRY_POLICY_VERSION_V3,
                "direction_score": score,
            }
        )
        assert production.tier_percent(score) == expected, score


def test_flat_variant_ignores_the_score() -> None:
    flat = next(v for v in sizing.build_tier_variants() if v.flat_percent is not None)
    assert flat.tier_percent(99.0) == _ADD_TIER_PERCENTS[2]
    assert flat.tier_percent(40.0) == _ADD_TIER_PERCENTS[2]
    assert flat.tier_percent(None) == _ADD_TIER_PERCENTS[2]


# --------------------------------------------------------------------------
# 部分赎回
# --------------------------------------------------------------------------


def test_redeem_fraction_is_fifo_and_prorates_cost_basis() -> None:
    position = sizing.Position()
    position.buy(cash=50.0, price=1.0, trade_date="2026-01-02", costs=_COSTS)
    position.buy(cash=50.0, price=2.0, trade_date="2026-03-02", costs=_COSTS)
    units_before = position.units

    # 赎回一半：先吃掉最早那笔（价格 1.0 的份额）。
    proceeds = position.redeem_fraction(
        fraction=0.5, price=2.0, trade_date="2026-06-01", costs=_COSTS
    )

    assert proceeds > 0
    assert position.units == pytest.approx(units_before * 0.5)
    # 第一笔份额多于一半赎回量，剩余里两笔都有；最早那笔被吃得更多。
    assert position.tranches[0].trade_date == "2026-01-02"
    assert position.tranches[0].units < position.tranches[1].units


def test_redeem_fraction_full_is_liquidate() -> None:
    position = sizing.Position()
    position.buy(cash=100.0, price=1.0, trade_date="2026-01-02", costs=_COSTS)
    position.redeem_fraction(fraction=1.0, price=1.1, trade_date="2026-02-02", costs=_COSTS)
    assert not position.tranches


# --------------------------------------------------------------------------
# 退出路径（合成序列，确定性）
# --------------------------------------------------------------------------


def _prices(count: int) -> list[dict]:
    # 2026-01-01 起连续自然日、价格恒定：收益全为费用，路径只由退出规则驱动。
    from datetime import date, timedelta

    start = date(2026, 1, 1)
    return [
        {"date": (start + timedelta(days=index)).isoformat(), "close": 100.0}
        for index in range(count)
    ]


def _signals(
    prices: list[dict],
    *,
    trend_by_index: dict[int, float],
    state_by_index: dict[int, str] | None = None,
) -> dict[str, "sizing.Signal"]:
    result: dict[str, sizing.Signal] = {}
    for index, row in enumerate(prices):
        trend = trend_by_index.get(index)
        if trend is None:
            continue
        state = (state_by_index or {}).get(index, "forming")
        result[str(row["date"])] = sizing.Signal(
            entry_state=state,
            direction_score=trend,
            trend_strength=trend,
        )
    return result


def _variant(**overrides) -> "exit_bt.ExitVariant":
    params = {
        "key": "test",
        "label": "test",
        "persistent_days": 3,
        "decay_points": None,
    }
    params.update(overrides)
    return exit_bt.ExitVariant(**params)


def _run_episode(variant, prices, signals):
    return exit_bt.simulate_exit_episode(
        variant=variant,
        prices=prices,
        signal_index=0,
        signals_by_date=signals,
        sector_label="测试",
        costs=_COSTS,
        max_days=30,
        base_fraction=0.20,
    )


def test_healthy_trend_never_reduces() -> None:
    prices = _prices(40)
    signals = _signals(
        prices,
        trend_by_index={index: 80.0 for index in range(40)},
        state_by_index={0: "ready_to_start"},
    )
    outcome = _run_episode(_variant(), prices, signals)
    assert outcome.exit_reason == "max_days"


def test_breakdown_escalates_by_stage_and_invalid_persistent_exits() -> None:
    prices = _prices(40)
    trend = {index: 80.0 for index in range(40)}
    states = {0: "ready_to_start"}
    # 第 6 天起跌破退出线；第 8 天起方向 invalid。
    for index in range(6, 40):
        trend[index] = 40.0
    for index in range(8, 40):
        states[index] = "invalid"
    signals = _signals(prices, trend_by_index=trend, state_by_index=states)

    outcome = _run_episode(_variant(persistent_days=3), prices, signals)

    # 跌破首日 → −25%；连续 3 日 → 再 −50%；invalid+持续 → 全退。
    assert outcome.exit_reason == "direction_exit"
    assert outcome.hold_days < 30


def test_no_exit_rules_control_holds_to_maturity() -> None:
    prices = _prices(40)
    trend = {index: (80.0 if index < 6 else 40.0) for index in range(40)}
    signals = _signals(
        prices, trend_by_index=trend, state_by_index={0: "ready_to_start"}
    )
    outcome = _run_episode(
        _variant(key="no_exit_rules", persistent_days=None), prices, signals
    )
    assert outcome.exit_reason == "max_days"


def test_immediate_full_exit_control_leaves_on_first_breakdown() -> None:
    prices = _prices(40)
    trend = {index: (80.0 if index < 6 else 40.0) for index in range(40)}
    signals = _signals(
        prices, trend_by_index=trend, state_by_index={0: "ready_to_start"}
    )
    outcome = _run_episode(
        _variant(
            key="immediate", persistent_days=1, immediate_full_exit=True
        ),
        prices,
        signals,
    )
    assert outcome.exit_reason == "direction_exit"
    # 跌破发生在第 6 天收盘，次日执行：入场在第 1 天，持有 6 个自然日。
    assert outcome.hold_days == 6


def test_decay_gate_blocks_the_add_but_not_the_hold() -> None:
    """趋势相对入场回落 ≥X：禁加仓（买入笔数不增），但不强制减仓。"""
    prices = _prices(40)
    # 入场 80，此后回落到 66（仍在退出线上方）；方向每天都 ready。
    trend = {0: 80.0}
    states = {0: "ready_to_start"}
    for index in range(1, 40):
        trend[index] = 66.0
        states[index] = "ready_to_start"
    signals = _signals(prices, trend_by_index=trend, state_by_index=states)

    gated = _run_episode(
        _variant(persistent_days=3, decay_points=12.0), prices, signals
    )
    open_add = _run_episode(
        _variant(persistent_days=3, decay_points=None), prices, signals
    )

    assert gated.exit_reason == "max_days" and open_add.exit_reason == "max_days"
    assert gated.buy_count == 1, "回落 14 分 ≥ 12：首仓之外不得再加"
    assert open_add.buy_count > 1, "不启用 decay 时同一序列应持续加仓"


# --------------------------------------------------------------------------
# 加仓节流变体（sweep 的候选条件本身必须先正确）
# --------------------------------------------------------------------------


def _throttle_context(*, trade_date: str, price: float) -> "sizing.AddContext":
    position = sizing.Position()
    position.buy(cash=20.0, price=100.0, trade_date="2026-01-05", costs=_COSTS)
    return sizing.AddContext(
        position=position,
        price=price,
        remaining_budget=80.0,
        signal_ready=True,
        tier_percent=10.0,
        tranche_scale=1.0,
        tranche_index=0,
        trade_date=trade_date,
    )


def test_gap_throttle_blocks_adds_inside_the_window() -> None:
    add = sizing._throttled_current_of_holding(min_gap_days=3)
    assert add(_throttle_context(trade_date="2026-01-07", price=110.0)) == 0.0
    assert add(_throttle_context(trade_date="2026-01-08", price=110.0)) > 0.0


def test_step_up_throttle_requires_price_progress() -> None:
    add = sizing._throttled_current_of_holding(min_step_up_percent=5.0)
    assert add(_throttle_context(trade_date="2026-01-20", price=104.0)) == 0.0
    assert add(_throttle_context(trade_date="2026-01-20", price=105.0)) > 0.0


def test_no_throttle_matches_the_production_loss_floor_carrier() -> None:
    """两个参数都不启用时，行为必须与生产口径（现状 + 浮亏封档）一致。"""
    plain = sizing._current_of_holding(loss_behaviour="floor")
    throttled = sizing._throttled_current_of_holding()
    # 浮盈情形。
    ctx = _throttle_context(trade_date="2026-01-20", price=110.0)
    assert throttled(ctx) == pytest.approx(plain(ctx))
    # 浮亏情形（封到最低档）。
    ctx_loss = _throttle_context(trade_date="2026-01-20", price=90.0)
    assert throttled(ctx_loss) == pytest.approx(plain(ctx_loss))


def test_throttle_variant_grid_covers_both_families() -> None:
    variants = sizing.build_throttle_variants()
    assert variants[0].key == "throttle_none"
    assert {v.min_gap_days for v in variants if v.min_gap_days} == {3, 5, 7}
    assert {v.min_step_up_percent for v in variants if v.min_step_up_percent} == {3.0, 5.0}


# --------------------------------------------------------------------------
# 数据充分性检查
# --------------------------------------------------------------------------


def test_insufficient_data_produces_no_conclusions() -> None:
    prices = _prices(40)
    signals = _signals(
        prices,
        trend_by_index={index: 80.0 for index in range(40)},
        state_by_index={0: "ready_to_start"},
    )
    prepared = sizing.Prepared(
        signals={"测试": signals},
        prices_by_label={"测试": prices},
        index_by_label={
            "测试": {str(row["date"]): index for index, row in enumerate(prices)}
        },
        benchmark_key="沪深300",
        decision_day_count=40,
        caveats=[],
        market_context={"available": False},
    )

    payload = exit_bt.run(
        prepared,
        max_days=30,
        base_fraction=0.20,
        costs=_COSTS,
        min_episodes=30,
        min_decision_days=60,
        sqlite_cache="synthetic",
    )

    assert payload["status"] == "insufficient_data"
    assert "variants" not in payload
    assert payload["episode_count_evaluated"] < 30
