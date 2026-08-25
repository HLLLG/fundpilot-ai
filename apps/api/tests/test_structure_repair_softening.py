"""「反弹修复结构」分数算法与日报退出档位放宽。

背景（2026-08 线上实测）：医疗方向 20 日深回撤把结构打成 weak_breakdown →
`structure_broken` → invalid → 日报大幅减仓评估 −50%；而当日板块 +2.6%（估算）、
主力资金转为净流入、区间修复已在进行——这些证据在退出链路上完全没有发言权，
日报只能不加度量地复述"超跌反弹，结构未修复"。

契约：
* 分数只是可观测事实，**不改变 entry_state**（荐基入场语义一分不动）；
* 唯一消费方是日报退出链路：结构破坏是判废**唯一**触发条件（非双弱、非主线退潮）
  且修复分过放宽线时，大幅减仓 −50% 放宽为普通减仓评估 −25%（浮盈提档到 −1/3，
  与趋势跌破分支的既有先例同一规则）；
* 连续跌破退出线的清仓升级不受影响——时间确认的失效高于单日反弹证据。
"""

from __future__ import annotations

import pytest

from app.services.decision_guard_shared import (
    ACTION_BUCKET_CLEAR_ALL,
    ACTION_BUCKET_DEEP_REDUCE,
    ACTION_BUCKET_REDUCE,
)
from app.services.sector_direction_exit import (
    EXIT_STATE_DEEP_REDUCE,
    EXIT_STATE_EXIT,
    EXIT_STATE_REDUCE,
    assess_direction_exit,
)
from app.services.sector_direction_state import EXIT_TREND_THRESHOLD
from app.services.sector_opportunity_scoring import (
    V3_STRUCTURE_REPAIR_SOFTEN_THRESHOLD,
    describe_structure_repair_v3,
)

# --------------------------------------------------------------------------
# 分数算法
# --------------------------------------------------------------------------


def _repair(**overrides):
    params = {
        "structure_broken": True,
        "doubly_weak": False,
        "mainline_status": "forming",
        # 线上实测输入：医疗当日 +2.61%（估算）、今日主力净流入、区间修复 30%。
        "change_1d": 2.61,
        "recovery_20d": 30.0,
        "flow_improving": True,
        "today_flow": 16.86,
        "date_aligned": True,
    }
    params.update(overrides)
    return describe_structure_repair_v3(**params)


def test_production_rebound_case_scores_above_the_soften_line() -> None:
    """医疗 2026-08 实测输入：区间修复 30×0.4 + 当日反弹 87×0.3 + 资金回流 100×0.3。"""
    repair = _repair()

    assert repair["score"] == pytest.approx(68.1)
    assert repair["components"] == {
        "range_recovery": 30.0,
        "intraday_rebound": 87.0,
        "flow_reflux": 100.0,
    }
    assert repair["active"] is True
    assert repair["sole_invalid_driver"] is True
    # 新设参数必须如实披露未回测。
    assert repair["thresholds_validated"] is False


def test_missing_evidence_scores_zero_not_neutral() -> None:
    """缺数据按 0 计：修复必须有证据，缺证据不等于在修复。"""
    repair = _repair(change_1d=None, recovery_20d=None, flow_improving=False, today_flow=None)

    assert repair["score"] == 0.0
    assert repair["active"] is False


def test_falling_day_earns_no_rebound_credit() -> None:
    repair = _repair(change_1d=-1.2)
    assert repair["components"]["intraday_rebound"] == 0.0


def test_unconfirmed_inflow_only_earns_half_of_the_flow_axis() -> None:
    """今日转正但资金模式未确认回流：只给一半，防止单日脉冲冒充回流。"""
    repair = _repair(flow_improving=False)
    assert repair["components"]["flow_reflux"] == 50.0


def test_doubly_weak_or_fading_disqualifies_the_sole_driver_claim() -> None:
    assert _repair(doubly_weak=True)["sole_invalid_driver"] is False
    assert _repair(mainline_status="fading")["sole_invalid_driver"] is False
    # 结构未破坏时既不是唯一触发条件，也谈不上激活放宽。
    intact = _repair(structure_broken=False)
    assert intact["sole_invalid_driver"] is False
    assert intact["active"] is False


def test_active_requires_both_breakage_and_score_over_threshold() -> None:
    weak_rebound = _repair(change_1d=0.5, recovery_20d=10.0, flow_improving=False, today_flow=None)
    assert weak_rebound["score"] < V3_STRUCTURE_REPAIR_SOFTEN_THRESHOLD
    assert weak_rebound["active"] is False


# --------------------------------------------------------------------------
# 打分行集成：分数挂在 v3 行上、不改变 entry_state
# --------------------------------------------------------------------------


def _maturity_row(**feature_overrides) -> dict:
    """直接驱动 `_entry_maturity_v3`（先例见 test_sector_direction_narration_coherence）。"""
    from app.services.sector_opportunity_scoring import _entry_maturity_v3

    features = {
        "position_label": "weak_breakdown",
        "distance_from_ma20_percent": -6.0,
        "drawdown_recovery_20d_percent": 30.0,
        "cumulative_20d_net_yi": -55.0,
        "distance_from_20d_high_percent": -11.0,
        "return_5d_percent": -6.0,
    }
    features.update(feature_overrides)
    mainline = {
        "sector_label": "医疗",
        "status": "forming",
        "feature_coverage": 1.0,
        "component_scores": {
            # 趋势 55 ≥ invalid 双弱上限 40：确保 invalid 只由结构破坏触发。
            "relative_strength": 55.0,
            "trend_persistence": 55.0,
            "fund_flow": 50.0,
            "breadth": 50.0,
            "market_structure": 30.0,
        },
        "features": features,
    }
    return _entry_maturity_v3(
        label="医疗",
        track="momentum",
        legacy_score=50.0,
        change_1d=2.61,
        change_5d=-6.0,
        today_flow=16.86,
        flow_5d=-39.16,
        pattern="multi_day_outflow_then_inflow",
        date_aligned=True,
        mainline=mainline,
    )


def test_scoring_row_carries_the_repair_payload_without_changing_state() -> None:
    row = _maturity_row()

    # 结构破坏仍然把方向判废——分数不放松入场语义。
    assert row["entry_state"] == "invalid"
    assert row["entry_gate_inputs"]["structure_broken"] is True

    repair = row["structure_repair"]
    assert repair["score"] == pytest.approx(68.1)
    assert repair["active"] is True
    assert repair["sole_invalid_driver"] is True
    # 修复证据同时进入人话证据行，卡片上可见。
    assert any("反弹修复分" in line for line in row["evidence"])


def test_scoring_row_marks_fading_as_non_sole_driver() -> None:
    from app.services.sector_opportunity_scoring import _entry_maturity_v3

    row = _maturity_row()
    fading = _entry_maturity_v3(
        label="医疗",
        track="momentum",
        legacy_score=50.0,
        change_1d=2.61,
        change_5d=-6.0,
        today_flow=16.86,
        flow_5d=-39.16,
        pattern="multi_day_outflow_then_inflow",
        date_aligned=True,
        mainline={
            "sector_label": "医疗",
            "status": "fading",
            "feature_coverage": 1.0,
            "component_scores": {
                "relative_strength": 55.0,
                "trend_persistence": 55.0,
                "fund_flow": 50.0,
                "breadth": 50.0,
                "market_structure": 30.0,
            },
            "features": {
                "position_label": "weak_breakdown",
                "distance_from_ma20_percent": -6.0,
                "drawdown_recovery_20d_percent": 30.0,
            },
        },
    )
    assert row["structure_repair"]["sole_invalid_driver"] is True
    assert fading["structure_repair"]["sole_invalid_driver"] is False


# --------------------------------------------------------------------------
# 退出档位放宽
# --------------------------------------------------------------------------

EXIT_LINE = EXIT_TREND_THRESHOLD


def _active_repair(**overrides) -> dict:
    payload = {
        "score": 68.1,
        "active": True,
        "sole_invalid_driver": True,
        "threshold": V3_STRUCTURE_REPAIR_SOFTEN_THRESHOLD,
        "components": {
            "range_recovery": 30.0,
            "intraday_rebound": 87.0,
            "flow_reflux": 100.0,
        },
        "thresholds_validated": False,
    }
    payload.update(overrides)
    return payload


def _assess_invalid(**overrides):
    params = {
        "sector_label": "医疗",
        "entry_state": "invalid",
        "trend_strength": 80.0,
        "exit_trend_threshold": EXIT_LINE,
    }
    params.update(overrides)
    return assess_direction_exit(**params)


def test_active_repair_softens_deep_reduce_to_a_quarter_reduction() -> None:
    result = _assess_invalid(structure_repair=_active_repair())

    assert result["exit_state"] == EXIT_STATE_REDUCE
    assert result["min_bucket"] == ACTION_BUCKET_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-25.0)
    # 放宽不恢复加仓资格：invalid 仍然成立。
    assert result["allows_add"] is False
    reasons = "；".join(result["reasons"])
    assert "反弹修复分 68/100" in reasons
    assert "放宽为减仓评估" in reasons
    assert any("恢复大幅减仓评估" in trigger for trigger in result["triggers"])
    # 修复分原样回显，供卡片与复盘引用。
    assert result["structure_repair"]["score"] == pytest.approx(68.1)


def test_softened_tier_keeps_the_unrealized_gain_bump() -> None:
    """浮盈提档沿用趋势跌破分支的既有先例（−25% → −1/3），不是新规则。"""
    result = _assess_invalid(structure_repair=_active_repair(), has_unrealized_gain=True)

    assert result["exit_state"] == EXIT_STATE_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-100.0 / 3.0)


def test_repair_without_sole_driver_does_not_soften() -> None:
    """双弱/主线退潮同时在场：弱的不只是价格结构，维持大幅减仓。"""
    result = _assess_invalid(
        structure_repair=_active_repair(sole_invalid_driver=False),
    )

    assert result["exit_state"] == EXIT_STATE_DEEP_REDUCE
    assert result["min_bucket"] == ACTION_BUCKET_DEEP_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-50.0)


def test_inactive_repair_does_not_soften() -> None:
    result = _assess_invalid(
        structure_repair=_active_repair(active=False, score=41.3),
    )

    assert result["exit_state"] == EXIT_STATE_DEEP_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-50.0)


def test_time_confirmed_failure_still_escalates_to_exit_despite_repair() -> None:
    """连续 3 日低于退出线：时间确认的失效高于单日反弹证据，清仓升级不放宽。"""
    result = _assess_invalid(
        trend_strength=40.0,
        trend_history=[("2026-08-22", 41.0), ("2026-08-21", 39.0)],
        structure_repair=_active_repair(),
    )

    assert result["exit_state"] == EXIT_STATE_EXIT
    assert result["min_bucket"] == ACTION_BUCKET_CLEAR_ALL
    assert result["suggested_position_change_percent"] == pytest.approx(-100.0)


def test_absent_repair_payload_keeps_the_original_deep_reduce() -> None:
    result = _assess_invalid()

    assert result["exit_state"] == EXIT_STATE_DEEP_REDUCE
    assert result["suggested_position_change_percent"] == pytest.approx(-50.0)
    assert result["structure_repair"] is None


# --------------------------------------------------------------------------
# 日报接线：held 行上的修复分传进退出判定
# --------------------------------------------------------------------------


def test_attach_direction_exit_passes_the_repair_payload(monkeypatch) -> None:
    from app.models import Holding
    from app.services.report_sector_opportunity import _attach_direction_exit

    monkeypatch.setattr(
        "app.services.sector_direction_exit.load_direction_trend_history",
        lambda labels, before_trade_date: {},
    )
    monkeypatch.setattr(
        "app.services.sector_direction_exit.load_direction_ledger_health",
        lambda trade_date: {"available": False},
    )
    monkeypatch.setattr(
        "app.services.sector_direction_exit.load_direction_entry_contracts",
        lambda codes: {},
    )
    # 放宽后是卖出档，会触发同族分歧披露的当日账本读取；返回 None 表示当日无账本。
    monkeypatch.setattr(
        "app.services.sector_direction_state.load_previous_direction_states",
        lambda trade_date: None,
    )

    held = {
        "医疗": {
            "sector_label": "医疗",
            "entry_state": "invalid",
            "trend_strength_score": 80.0,
            "structure_repair": _active_repair(),
        }
    }
    holdings = [
        Holding(
            fund_code="011373",
            fund_name="招商前沿医疗保健股票A",
            sector_name="医疗",
            holding_amount=4_500.0,
        )
    ]

    _attach_direction_exit(held, holdings=holdings, trade_date="2026-08-25")

    exit_row = held["医疗"]["direction_exit"]
    assert exit_row["exit_state"] == EXIT_STATE_REDUCE
    assert exit_row["suggested_position_change_percent"] == pytest.approx(-25.0)
    assert exit_row["structure_repair"]["score"] == pytest.approx(68.1)
