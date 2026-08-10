"""方向退出侧判定回归。

背景：整套方向成熟度原来只有入场，`invalidation_signals` 那三条只是展示文案，没有任何
代码在逐日跟踪；确定性减仓链路（`resolve_escalation_floor`）也听不见方向信号——它开头
两道早退要求「板块机会不可用 且 量价背离 confidence=高」，于是一个机会仍然可用、置信度
只有中、但趋势已经从 69 掉到 41 的方向产不出任何减仓动作。这里锁住补上的那条链路。
"""
from __future__ import annotations

import pytest

from app.services.decision_guard_shared import (
    ACTION_BUCKET_CLEAR_ALL,
    ACTION_BUCKET_DEEP_REDUCE,
    ACTION_BUCKET_PAUSE,
    ACTION_BUCKET_REDUCE,
    resolve_escalation_floor,
)
from app.services.sector_direction_exit import (
    EXIT_STATE_DEEP_REDUCE,
    EXIT_STATE_EXIT,
    EXIT_STATE_HOLD,
    EXIT_STATE_PAUSE_ADD,
    EXIT_STATE_REDUCE,
    EXIT_STATE_UNAVAILABLE,
    assess_direction_exit,
)
from app.services.sector_direction_state import EXIT_TREND_THRESHOLD

EXIT_LINE = EXIT_TREND_THRESHOLD  # 52.0：入场线 60 − 8，复用既有常量而非新造一条


def _assess(**overrides):
    params = {
        "sector_label": "半导体材料",
        "entry_state": "forming",
        "trend_strength": 60.0,
        "exit_trend_threshold": EXIT_LINE,
    }
    params.update(overrides)
    return assess_direction_exit(**params)


# --------------------------------------------------------------------------
# 状态机
# --------------------------------------------------------------------------


def test_trend_above_exit_line_and_ready_keeps_add_rights() -> None:
    result = _assess(entry_state="ready_to_start", trend_strength=79.06)

    assert result["exit_state"] == EXIT_STATE_HOLD
    assert result["min_bucket"] is None
    assert result["allows_add"] is True


def test_direction_no_longer_ready_blocks_add_without_forcing_a_sale() -> None:
    """决策 2：加仓要求方向当前仍通过入场线；但这不构成卖出理由。"""
    result = _assess(entry_state="forming", trend_strength=58.0)

    assert result["exit_state"] == EXIT_STATE_PAUSE_ADD
    assert result["allows_add"] is False
    # 暂停追涨只封顶加仓，不给减仓比例
    assert result["min_bucket"] == ACTION_BUCKET_PAUSE
    assert result["suggested_position_change_percent"] is None


def test_first_day_below_exit_line_suggests_quarter_reduction() -> None:
    result = _assess(trend_strength=41.0)

    assert result["exit_state"] == EXIT_STATE_REDUCE
    assert result["min_bucket"] == ACTION_BUCKET_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-25.0)
    assert result["consecutive_days_below_exit_line"] == 1
    assert result["allows_add"] is False


def test_unrealized_gain_raises_the_first_reduction_tier() -> None:
    """浮盈提档沿用 resolve_escalation_floor 里既有的同名先例，不是新规则。"""
    result = _assess(trend_strength=41.0, has_unrealized_gain=True)

    assert result["suggested_position_change_percent"] == pytest.approx(-100.0 / 3.0)


def test_persistent_breakdown_escalates_to_deep_reduction() -> None:
    """连续 3 个交易日在退出线下 → 大幅减仓（区分「持续走坏」与「单日插针」）。"""
    result = _assess(
        trend_strength=44.0,
        trend_history=[("2026-08-07", 47.0), ("2026-08-06", 50.0), ("2026-08-05", 66.0)],
    )

    assert result["consecutive_days_below_exit_line"] == 3
    assert result["exit_state"] == EXIT_STATE_DEEP_REDUCE
    assert result["min_bucket"] == ACTION_BUCKET_DEEP_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-50.0)


def test_single_day_dip_after_healthy_history_is_not_persistent() -> None:
    result = _assess(
        trend_strength=51.0,
        trend_history=[("2026-08-07", 70.0), ("2026-08-06", 72.0)],
    )

    assert result["consecutive_days_below_exit_line"] == 1
    assert result["exit_state"] == EXIT_STATE_REDUCE


def test_history_gap_does_not_pretend_missing_days_qualified() -> None:
    """历史里出现空缺（停机／当天没进扫描）时，遇到第一个不在线下的记录即停止。"""
    result = _assess(
        trend_strength=44.0,
        trend_history=[("2026-08-07", 45.0), ("2026-08-06", None)],  # type: ignore[list-item]
    )

    assert result["consecutive_days_below_exit_line"] == 2
    assert result["exit_state"] == EXIT_STATE_REDUCE


def test_invalid_direction_gives_deep_reduction_not_immediate_liquidation() -> None:
    """方向作废先给大幅减仓：清仓在既有标定里是「多重信号极端共振」那一档。"""
    result = _assess(entry_state="invalid", trend_strength=8.43)

    assert result["exit_state"] == EXIT_STATE_DEEP_REDUCE
    assert result["min_bucket"] == ACTION_BUCKET_DEEP_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-50.0)


def test_invalid_direction_confirmed_by_time_escalates_to_liquidation() -> None:
    result = _assess(
        entry_state="invalid",
        trend_strength=8.43,
        trend_history=[("2026-08-07", 12.0), ("2026-08-06", 20.0)],
    )

    assert result["exit_state"] == EXIT_STATE_EXIT
    assert result["min_bucket"] == ACTION_BUCKET_CLEAR_ALL
    assert result["suggested_position_change_percent"] == pytest.approx(-100.0)


def test_missing_trend_does_not_force_a_sale_but_withholds_add_rights() -> None:
    """缺数据不构成卖出理由，但也不授权加仓——这个不对称是刻意的。"""
    result = _assess(entry_state="ready_to_start", trend_strength=None)

    assert result["exit_state"] == EXIT_STATE_UNAVAILABLE
    assert result["min_bucket"] is None
    assert result["allows_add"] is False
    assert result["basis"] == "unavailable"


# --------------------------------------------------------------------------
# 双模式：有／无入场契约
# --------------------------------------------------------------------------


def test_without_entry_contract_falls_back_to_absolute_basis() -> None:
    """线上绝大多数持仓来自截图导入，没有发现基金的买入事件，必须仍然可判。"""
    result = _assess(trend_strength=41.0)

    assert result["basis"] == "absolute"
    assert result["exit_state"] == EXIT_STATE_REDUCE
    assert "跌破退出线" in result["reasons"][0]


def test_entry_contract_makes_the_reason_traceable_to_that_decision() -> None:
    result = _assess(
        trend_strength=41.0,
        entry_contract={
            "sector_label": "半导体材料",
            "entry_date": "2026-07-20",
            "entry_trend": 69.09,
            "entry_participation": 47.7,
            "entry_position_risk": 92.67,
            "thesis_event_id": "evt-1",
        },
    )

    assert result["basis"] == "relative_to_entry"
    assert "2026-07-20" in result["reasons"][0]
    assert "69.1" in result["reasons"][0]
    assert result["entry_reference"]["thesis_event_id"] == "evt-1"


def test_trend_decay_from_entry_blocks_add_while_still_above_exit_line() -> None:
    """趋势还在线上但相对买入明显回落：只禁止加仓，不要求卖出。"""
    result = _assess(
        entry_state="ready_to_start",
        trend_strength=60.0,
        entry_contract={
            "sector_label": "半导体材料",
            "entry_date": "2026-07-20",
            "entry_trend": 79.0,
        },
    )

    assert result["exit_state"] == EXIT_STATE_PAUSE_ADD
    assert result["min_bucket"] == ACTION_BUCKET_PAUSE
    assert result["suggested_position_change_percent"] is None
    assert result["trend_decay_from_entry"] == pytest.approx(19.0)


def test_contract_for_a_different_sector_is_ignored() -> None:
    """当初买的是别的板块时不能拿旧基线判现在这个方向。"""
    result = _assess(
        entry_state="ready_to_start",
        trend_strength=60.0,
        entry_contract={"sector_label": "电力", "entry_date": "2026-07-20", "entry_trend": 79.0},
    )

    assert result["basis"] == "absolute"
    assert result["entry_reference"] is None
    assert result["exit_state"] == EXIT_STATE_HOLD


def test_thresholds_are_declared_unvalidated() -> None:
    """N 与 X 没有回测支撑，返回值必须如实标注，不能让下游当成已验证参数。"""
    assert _assess()["thresholds_validated"] is False


# --------------------------------------------------------------------------
# 与既有风险升级的合并
# --------------------------------------------------------------------------


def test_direction_exit_now_reaches_the_deterministic_reduction_chain() -> None:
    """回归点：机会仍然「可用」、背离置信度只有「中」，原来产不出任何减仓档位。"""
    sector_opportunity = {"opportunity_available": True, "confidence": "中"}

    without = resolve_escalation_floor(
        sector_opportunity=sector_opportunity,
        evidence=None,
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert without["min_bucket"] is None

    with_exit = resolve_escalation_floor(
        sector_opportunity=sector_opportunity,
        evidence=None,
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
        direction_exit=_assess(trend_strength=41.0),
    )
    assert with_exit["min_bucket"] == ACTION_BUCKET_REDUCE
    assert with_exit["suggested_position_change_percent"] == pytest.approx(-25.0)
    assert "跌破退出线" in with_exit["basis"]


def test_merge_keeps_the_more_conservative_of_risk_and_direction() -> None:
    risk_floor = resolve_escalation_floor(
        sector_opportunity={"opportunity_available": False, "confidence": "高"},
        evidence={"composite": {"level": "低"}},
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
    )
    assert risk_floor["min_bucket"] == ACTION_BUCKET_REDUCE

    merged = resolve_escalation_floor(
        sector_opportunity={"opportunity_available": False, "confidence": "高"},
        evidence={"composite": {"level": "低"}},
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
        # 方向侧更保守（大幅减仓）→ 应当胜出
        direction_exit=_assess(entry_state="invalid", trend_strength=8.0),
    )
    assert merged["min_bucket"] == ACTION_BUCKET_DEEP_REDUCE
    assert merged["suggested_position_change_percent"] == pytest.approx(-50.0)
    # 两侧理由都要保留，用户才知道被两件事同时压住
    assert any("不具备参与条件" in reason for reason in merged["reasons"])
    assert any("量价背离" in reason for reason in merged["reasons"])


def test_hold_state_does_not_weaken_an_existing_risk_floor() -> None:
    merged = resolve_escalation_floor(
        sector_opportunity={"opportunity_available": False, "confidence": "高"},
        evidence={"composite": {"level": "低"}},
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
        direction_exit=_assess(entry_state="ready_to_start", trend_strength=90.0),
    )

    assert merged["min_bucket"] == ACTION_BUCKET_REDUCE
    assert merged["suggested_position_change_percent"] == pytest.approx(-25.0)


def test_no_signal_on_either_side_still_returns_no_escalation() -> None:
    merged = resolve_escalation_floor(
        sector_opportunity={"opportunity_available": True, "confidence": "中"},
        evidence=None,
        market_breadth=None,
        over_concentration=False,
        has_unrealized_gain=False,
        decision_style="conservative",
        direction_exit=_assess(entry_state="ready_to_start", trend_strength=90.0),
    )

    assert merged["min_bucket"] is None
    assert merged["reasons"] == []
    assert merged["basis"] == ""
