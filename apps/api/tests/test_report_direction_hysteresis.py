"""日报只读接入方向滞回（连续达标天数 + 退出滞回带）。

回归背景：`sector_direction_states` 是荐基单方读写的全局账本（无 userId、按
(交易日, 板块) 幂等覆盖），日报此前既不写**也不读**，于是只能拿到当日原始档位——同一个
板块今天 `ready_to_start`、明天掉回 `forming`、后天又上来，而抖动大多来自阈值边界上的
一两分之差。荐基靠滞回压掉了它，日报没有，两个界面对同一天同一板块因此结论不同。

这里锁四条契约：
1. **只读**：日报绝不写那张账本（写入会用一份只含持仓板块的窄输入覆盖荐基的状态）；
2. 读到历史时套上滞回，并如实标 `hysteresis_applied=true`；读不到时不得撒谎；
3. 退出滞回带内的已确认方向保持 `ready_to_start`，因此不会被 guard 当作"不可加仓"；
4. 轮动参考的排序发生在滞回**之后**（`entry_state` 是排序第一优先级，顺序错了会让
   展示状态与入选依据是两套东西）。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models import Holding
from app.services import report_sector_opportunity as sector_ctx
from app.services.recommendation_guard import (
    _build_sector_evidence,
    _direction_continuity_evidence,
    _entry_state_add_block_reason,
)
from app.services.sector_direction_state import DirectionStateRecord

TRADE_DATE = "2026-06-11"
PREVIOUS_TRADE_DATE = "2026-06-10"


#: V3 入场线：trend 60 / participation 35 / position 25；退出线 = trend 线 - 8 = 52。
#: 因此 trend=72 当日达标，trend=55 落在退出滞回带内（52 <= 55 < 60）。
_QUALIFYING_TREND = 72.0
_EXIT_BAND_TREND = 55.0


def _mainline_row(*, label: str = "半导体", trend: float = _QUALIFYING_TREND) -> dict:
    """一个真能驱动 `classify_entry_state_v3` 走到 ready_to_start 的 regime 行。

    键名必须是 V3 实际读的那一套（`component_scores` 下的 relative_strength /
    trend_persistence / fund_flow / breadth / market_structure），`status` 也必须落在
    `_DIRECTIONAL_MAINLINE_STATUSES_V3` 里，否则只会得到 ready_on_pullback。
    """
    return {
        "schema_version": "mainline_regime.v1",
        "sector_label": label,
        "status": "confirmed",
        "score": 78.0,
        "confidence": "高",
        "feature_coverage": 0.95,
        "component_scores": {
            # trend_strength = 0.55 * relative_strength + 0.45 * trend_persistence
            "relative_strength": trend,
            "trend_persistence": trend,
            # participation = 0.60 * fund_flow + 0.40 * breadth = 66 >= 35
            "fund_flow": 70.0,
            "breadth": 60.0,
            # position_risk 直接取 market_structure，58 >= 25
            "market_structure": 58.0,
        },
        "features": {
            "position_label": "trend_up",
            "distance_from_ma20_percent": 2.0,
            "distance_from_20d_high_percent": -3.0,
            "return_5d_percent": 4.0,
            "cumulative_20d_net_yi": 30.0,
            "advancing_ratio_percent": 60.0,
            "annualized_volatility_20d_percent": 28.0,
        },
    }


def _heat_row(label: str = "半导体") -> dict:
    return {
        "sector_label": label,
        "change_1d_percent": 1.4,
        "change_5d_percent": 5.2,
        "heat_score": 62.0,
    }


def _holdings(label: str = "半导体") -> list[Holding]:
    return [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长",
            sector_name=label,
            holding_amount=10_000.0,
        )
    ]


def _ready_history(*, label: str = "半导体", days: int = 3) -> dict[str, DirectionStateRecord]:
    return {
        label: DirectionStateRecord(
            trade_date=PREVIOUS_TRADE_DATE,
            sector_label=label,
            entry_state="ready_to_start",
            raw_entry_state="ready_to_start",
            qualifies_for_ready=True,
            consecutive_qualifying_days=days,
        )
    }


def _build_context(monkeypatch, *, history, **overrides) -> dict[str, Any]:
    monkeypatch.setattr(
        sector_ctx,
        "_load_direction_state_history",
        lambda _previous: (history, "loaded" if history is not None else "no_history"),
    )
    monkeypatch.setattr(
        sector_ctx, "_resolve_previous_trade_date", lambda _date: PREVIOUS_TRADE_DATE
    )
    kwargs: dict[str, Any] = {
        "trade_date": TRADE_DATE,
        "fetch_sector_heat": lambda: [_heat_row()],
        "fetch_sector_position": lambda _labels, _date: {},
        "mainline_by_label": {"半导体": _mainline_row()},
        "mainline_meta": {"available": True, "source": "test"},
    }
    kwargs.update(overrides)
    return sector_ctx.build_holding_sector_opportunity_context(_holdings(), **kwargs)


# --- 契约 1：只读 ------------------------------------------------------------


def test_daily_report_never_writes_the_direction_state_ledger(monkeypatch) -> None:
    """写入会用只含持仓板块的窄输入覆盖荐基对同一板块算好的状态，必须禁止。"""
    writes: list[Any] = []

    def _explode(rows, **kwargs):
        writes.append((rows, kwargs))
        raise AssertionError("日报不得写 sector_direction_states")

    monkeypatch.setattr(
        "app.services.sector_direction_state.record_direction_states", _explode
    )

    context = _build_context(monkeypatch, history=_ready_history())

    assert writes == []
    assert context["mainline"]["hysteresis"]["read_only"] is True


def test_module_does_not_reference_the_ledger_writer() -> None:
    """连引用都不该有：只读是结构性保证，不靠"记得别调"。"""
    from pathlib import Path

    source = Path(sector_ctx.__file__).read_text(encoding="utf-8")
    assert "record_direction_states" not in source


# --- 契约 2：读到历史才敢说套了滞回 ------------------------------------------


def test_history_present_marks_hysteresis_applied_and_reports_provenance(
    monkeypatch,
) -> None:
    context = _build_context(monkeypatch, history=_ready_history(days=3))

    meta = context["mainline"]["hysteresis"]
    assert meta["applied"] is True
    assert context["mainline"]["hysteresis_applied"] is True
    assert meta["history_trade_date"] == PREVIOUS_TRADE_DATE
    assert meta["history_source"] == "discovery_global_direction_state_ledger"
    # 天数只能按下界披露：荐基没跑的那天没有记录，streak 会从 1 重新起算。
    assert meta["consecutive_days_is_lower_bound"] is True
    assert "下界" in meta["note"]

    held = context["held"]["半导体"]
    assert held["consecutive_qualifying_days"] == 4
    assert held["raw_entry_state"]


def test_missing_history_does_not_claim_hysteresis(monkeypatch) -> None:
    context = _build_context(monkeypatch, history=None)

    meta = context["mainline"]["hysteresis"]
    assert meta["applied"] is False
    assert context["mainline"]["hysteresis_applied"] is False
    assert meta["reason"] == "no_history"


def test_hysteresis_failure_falls_back_to_raw_rows(monkeypatch) -> None:
    """滞回是增强项：算不出来就退回当日原始档位，不能阻塞日报。"""

    def _explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.services.sector_direction_state.apply_direction_state_hysteresis", _explode
    )

    context = _build_context(monkeypatch, history=_ready_history())

    assert context["mainline"]["hysteresis"]["reason"] == "hysteresis_error"
    assert context["mainline"]["hysteresis_applied"] is False
    assert "半导体" in context["held"]


def test_read_error_is_reported_and_not_treated_as_history(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_direction_state.load_previous_direction_states",
        lambda _date: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    states, reason = sector_ctx._load_direction_state_history(PREVIOUS_TRADE_DATE)

    assert states is None
    assert reason == "direction_state_read_error"


# --- 契约 3：退出滞回带内的已确认方向仍可加仓 --------------------------------


def test_confirmed_direction_inside_the_exit_band_keeps_add_open(monkeypatch) -> None:
    """今日未通过入场线、但趋势仍在退出线之上：方向保持 ready，guard 不得拦加仓。

    这正是此前"日报善变"的典型场景——原始档位会掉到 forming，于是同一个方向在荐基那边
    还是可布局，在日报这边已经变成不可加仓。
    """
    context = _build_context(
        monkeypatch,
        history=_ready_history(days=3),
        mainline_by_label={"半导体": _mainline_row(trend=_EXIT_BAND_TREND)},
    )

    held = context["held"]["半导体"]
    assert held["raw_entry_state"] != "ready_to_start"
    assert held["entry_state"] == "ready_to_start"
    # guard 侧的直接后果：加仓不再被方向档位拦住。
    assert _entry_state_add_block_reason(held) is None


def test_without_history_the_same_row_is_not_promoted(monkeypatch) -> None:
    """没有历史就不得把当日未达标的方向「补」成可布局——滞回不是放宽入场线。"""
    context = _build_context(
        monkeypatch,
        history=None,
        mainline_by_label={"半导体": _mainline_row(trend=_EXIT_BAND_TREND)},
    )

    held = context["held"]["半导体"]
    assert held["entry_state"] != "ready_to_start"
    assert _entry_state_add_block_reason(held) is not None


# --- 契约 4：轮动参考在滞回之后排序 ------------------------------------------


def test_market_top_is_selected_after_hysteresis(monkeypatch) -> None:
    """`entry_state` 是排序第一优先级，所以必须先滞回再选择。

    此前这里用的是复合入口 `select_sector_opportunities`（内部打分即选择），排序因此
    发生在滞回之前——展示出来的状态和入选依据会是两套东西。荐基侧专门为这点留了注释。
    """
    from app.services import sector_opportunity_scoring

    seen: dict[str, Any] = {}
    original = sector_opportunity_scoring.select_scored_sector_opportunities

    def _capture(rows, **kwargs):
        seen["rows"] = [dict(row) for row in rows]
        return original(rows, **kwargs)

    monkeypatch.setattr(
        sector_opportunity_scoring, "select_scored_sector_opportunities", _capture
    )

    _build_context(
        monkeypatch,
        history=_ready_history(days=3),
        mainline_by_label={"半导体": _mainline_row(trend=_EXIT_BAND_TREND)},
        fetch_sector_heat=lambda: [_heat_row(), _heat_row("创新药")],
    )

    assert "rows" in seen, "select_scored_sector_opportunities 必须被调用"
    # 传给选择函数的行必须已经带上滞回产物，否则排序看到的是未平滑状态。
    assert any("raw_entry_state" in row for row in seen["rows"])


# --- 展示层：天数措辞 --------------------------------------------------------


def test_continuity_evidence_uses_lower_bound_wording() -> None:
    text = _direction_continuity_evidence(
        {
            "entry_state": "ready_to_start",
            "raw_entry_state": "ready_to_start",
            "consecutive_qualifying_days": 4,
        }
    )
    assert text == "方向已至少连续 4 个交易日通过入场线"


def test_first_day_streak_is_not_advertised_as_continuity() -> None:
    """「今天刚满足」不能写成「已连续满足」——这是本次要区分的两种情况。"""
    assert (
        _direction_continuity_evidence(
            {
                "entry_state": "ready_to_start",
                "raw_entry_state": "ready_to_start",
                "consecutive_qualifying_days": 1,
            }
        )
        is None
    )


def test_exit_band_is_described_as_retained_not_reconfirmed() -> None:
    text = _direction_continuity_evidence(
        {
            "entry_state": "ready_to_start",
            "raw_entry_state": "ready_on_pullback",
            "consecutive_qualifying_days": 4,
        }
    )
    assert text is not None
    assert "未跌破退出线" in text
    assert "非今日重新确认" in text


def test_continuity_is_surfaced_in_sector_evidence() -> None:
    evidence = _build_sector_evidence(
        {
            "track": "momentum",
            "confidence": "高",
            "entry_state": "ready_to_start",
            "raw_entry_state": "ready_to_start",
            "consecutive_qualifying_days": 3,
        }
    )
    assert any("至少连续 3 个交易日" in item for item in evidence)


def test_continuity_absent_when_hysteresis_did_not_run() -> None:
    evidence = _build_sector_evidence({"track": "momentum", "confidence": "高"})
    assert all("连续" not in item for item in evidence)


@pytest.mark.parametrize("value", [None, {}, {"consecutive_qualifying_days": "x"}])
def test_continuity_evidence_is_defensive(value) -> None:
    assert _direction_continuity_evidence(value) is None
