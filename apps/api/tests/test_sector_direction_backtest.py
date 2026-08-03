"""第 0 层标尺的单测：无前视、统计口径、阈值重扫与诚实缺口标注。

这些测试的重点不是"分数等于某个数"，而是**评估器本身可信**：如果它有前视偏差、或者
统计口径把同一天的横截面当成独立样本，那它给出的任何"入场线有超额"的结论都是假的。
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.sector_opportunity_scoring import ENTRY_FORMING
from app.services.sector_direction_backtest import (
    BASELINE_ALL_SECTORS,
    BASELINE_TOP_CHANGE_1D,
    GROUP_PRODUCTION_SELECTION,
    DirectionObservation,
    DirectionReplay,
    ForwardOutcome,
    _group_stats,
    _spearman,
    compute_direction_factor_ic,
    replay_sector_direction,
    scan_entry_gate_thresholds,
    summarize_direction_replay,
)
from app.services.sector_opportunity_scoring import (
    ENTRY_GATE_THRESHOLDS,
    ENTRY_INVALID,
    ENTRY_POLICY_VERSION,
    ENTRY_POLICY_VERSION_V3,
    ENTRY_READY_TO_START,
    V3_GATE_THRESHOLDS,
    classify_entry_state,
    score_sector_opportunity_rows,
)

_LABELS = (
    "半导体",
    "创新药",
    "白酒",
    "银行",
    "光伏",
    "军工",
    "有色金属",
    "传媒",
    "房地产",
    "煤炭",
)


def _weekdays(start: str, count: int) -> list[str]:
    cursor = date.fromisoformat(start)
    days: list[str] = []
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def _price_rows(
    days: list[str],
    *,
    drift: float,
    wiggle: float = 0.012,
    phase: float = 0.0,
    base: float = 100.0,
    divergence_from: str | None = None,
    divergent_drift: float | None = None,
) -> list[dict]:
    """确定性价格路径：线性漂移 + 正弦波动，保证存在真实回撤与涨跌交替。

    ``divergence_from`` / ``divergent_drift`` 用于构造"未来不同、历史相同"的两条序列，
    供无前视测试使用。
    """
    rows: list[dict] = []
    for index, day in enumerate(days):
        effective_drift = drift
        if (
            divergence_from is not None
            and divergent_drift is not None
            and day > divergence_from
        ):
            effective_drift = divergent_drift
        close = base * (1.0 + effective_drift * index) * (
            1.0 + wiggle * math.sin(index * 0.7 + phase)
        )
        rows.append({"date": day, "close": round(close, 4), "volume": 1_000_000 + index * 1_000})
    return rows


def _flow_rows(days: list[str], *, level: float, phase: float = 0.0) -> list[dict]:
    return [
        {
            "date": day,
            "main_force_net_yi": round(level + 3.0 * math.sin(index * 0.5 + phase), 2),
        }
        for index, day in enumerate(days)
    ]


def _build_inputs(
    days: list[str],
    *,
    divergence_from: str | None = None,
) -> dict:
    price_series_by_label: dict[str, list[dict]] = {}
    flow_series_by_label: dict[str, list[dict]] = {}
    for index, label in enumerate(_LABELS):
        drift = (index - 4) * 0.0009
        price_series_by_label[label] = _price_rows(
            days,
            drift=drift,
            phase=index * 0.4,
            divergence_from=divergence_from,
            divergent_drift=-drift - 0.004 if divergence_from else None,
        )
        flow_series_by_label[label] = _flow_rows(
            days, level=(index - 4) * 4.0, phase=index * 0.3
        )
    benchmark = _price_rows(days, drift=0.0002, wiggle=0.006, base=4000.0)
    return {
        "price_series_by_label": price_series_by_label,
        "flow_series_by_label": flow_series_by_label,
        "benchmark_series": benchmark,
    }


@pytest.fixture(autouse=True)
def _all_weekdays_are_trading_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """避免 `get_trade_date_set` 起 akshare 子进程；返回 None 即"所有工作日都是交易日"。"""
    monkeypatch.setattr(
        "app.services.trading_session.get_trade_date_set", lambda: None
    )


@pytest.fixture
def days() -> list[str]:
    return _weekdays("2025-01-02", 130)


def test_replay_produces_observations_and_uses_next_day_close_as_entry(
    days: list[str],
) -> None:
    replay = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5, 20),
        warmup_days=61,
        step=5,
    )

    assert replay.observation_count > 0
    assert replay.decision_dates
    index_by_day = {day: position for position, day in enumerate(days)}

    for observation in replay.observations:
        cursor = index_by_day[observation.decision_date]
        for horizon, outcome in observation.forward.items():
            # 决策在 D 收盘后产生，当天已不可成交：建仓必须是 D+1。
            assert outcome.entry_date == days[cursor + 1]
            assert outcome.exit_date == days[cursor + 1 + horizon]
            assert outcome.max_adverse_excess_percent is not None
            assert outcome.max_adverse_excess_percent <= 0.0


def test_v3_replay_updates_hysteresis_on_unsampled_trading_days(
    days: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import sector_direction_backtest as backtest

    calls: list[str] = []
    original = backtest.apply_direction_state_hysteresis

    def _spy(rows, **kwargs):
        calls.append(str(kwargs.get("trade_date")))
        return original(rows, **kwargs)

    monkeypatch.setattr(backtest, "apply_direction_state_hysteresis", _spy)
    replay = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5,),
        warmup_days=61,
        step=5,
    )

    assert replay.decision_dates
    assert len(calls) > len(replay.decision_dates)
    assert any("逐交易日应用" in caveat for caveat in replay.caveats)


def test_replay_has_no_look_ahead_in_features(days: list[str]) -> None:
    """未来价格完全改变时，所有特征必须逐一相等，只有前瞻收益变化。

    这是评估器最关键的一条：任何前视偏差都会让"入场线有超额"变成自证式结论。
    """
    cutoff = days[100]
    baseline = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5,),
        warmup_days=61,
        step=3,
        end_date=cutoff,
    )
    mutated = replay_sector_direction(
        **_build_inputs(days, divergence_from=cutoff),
        forward_horizons=(5,),
        warmup_days=61,
        step=3,
        end_date=cutoff,
    )

    assert baseline.observation_count > 0
    assert baseline.observation_count == mutated.observation_count

    keyed_baseline = {
        (item.decision_date, item.sector_label): item for item in baseline.observations
    }
    keyed_mutated = {
        (item.decision_date, item.sector_label): item for item in mutated.observations
    }
    assert keyed_baseline.keys() == keyed_mutated.keys()

    forward_changed = 0
    for key, left in keyed_baseline.items():
        right = keyed_mutated[key]
        assert left.factors == right.factors, f"{key} 的因子受到了未来数据影响"
        assert left.entry_state == right.entry_state
        assert left.evidence_quality == right.evidence_quality
        assert left.mainline_status == right.mainline_status
        assert left.gate_inputs == right.gate_inputs
        assert left.change_1d_percent == right.change_1d_percent
        assert left.change_5d_percent == right.change_5d_percent
        if left.excess(5) != right.excess(5):
            forward_changed += 1
    # 未来价格确实被改掉了，所以前瞻收益必须有变化，否则这个测试是空的。
    assert forward_changed > 0


def test_skips_days_whose_trade_date_rolls_back(
    days: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """交易日历缺某天时 effective_trade_date 会回滚，这种日子必须显式跳过并计数。"""
    excluded = days[70]
    monkeypatch.setattr(
        "app.services.trading_session.get_trade_date_set",
        lambda: frozenset(day for day in days if day != excluded),
    )
    replay = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5,),
        warmup_days=61,
        step=1,
    )

    reasons = {item.decision_date: item.reason for item in replay.skipped_days}
    assert reasons.get(excluded) == "trade_calendar_mismatch"
    assert excluded not in replay.decision_dates
    assert all(item.decision_date != excluded for item in replay.observations)


def test_summary_baseline_all_sectors_is_demeaned_to_zero(days: list[str]) -> None:
    """全板块等权基准在去均值口径下必然约等于 0——这是统计口径自洽的必要条件。"""
    replay = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5, 20),
        warmup_days=61,
        step=5,
    )
    summary = summarize_direction_replay(replay, min_decision_days=1)

    for horizon in (5, 20):
        stats = summary["groups"][BASELINE_ALL_SECTORS]["horizons"][str(horizon)]
        assert stats["available"] is True
        assert stats["mean_demeaned_excess_percent"] == pytest.approx(0.0, abs=1e-6)
        # 恒等于 0 的分组不能因为浮点残差被判成显著（真实回测里出现过 t=+2.39）。
        assert stats["decision_day_t_stat"] is None
        assert stats["significant"] is False

    for group in (BASELINE_TOP_CHANGE_1D, GROUP_PRODUCTION_SELECTION):
        assert group in summary["groups"]
    assert summary["verdict"]["auto_tuning_eligible"] is False
    assert summary["decision_day_count"] == len(replay.decision_dates)


def test_summary_declares_historical_gaps_and_feature_coverage(days: list[str]) -> None:
    replay = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5,),
        warmup_days=61,
        step=10,
    )
    summary = summarize_direction_replay(replay, min_decision_days=1)

    joined = " ".join(summary["caveats"])
    assert "上涨广度" in joined
    assert "重叠" in joined
    # 广度无历史 → 该因子覆盖率必须诚实地是 0，而不是被补成中性值。
    assert summary["feature_coverage"]["breadth"] == 0.0
    assert summary["feature_coverage"]["relative_strength"] > 0.0


def test_breadth_injection_restores_coverage(days: list[str]) -> None:
    """未来若开始采集广度历史，注入钩子必须真的把它接进打分。"""
    inputs = _build_inputs(days)
    breadth = {
        label: {day: 50.0 + (index * 3) % 40 for day in days}
        for index, label in enumerate(_LABELS)
    }
    replay = replay_sector_direction(
        **inputs,
        breadth_by_label_date=breadth,
        forward_horizons=(5,),
        warmup_days=61,
        step=10,
    )
    assert replay.feature_coverage["breadth"] == pytest.approx(1.0)


def test_factor_ic_reports_period_counts_per_horizon(days: list[str]) -> None:
    replay = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5, 20),
        warmup_days=61,
        step=5,
    )
    ic = compute_direction_factor_ic(replay, min_labels=5, min_decision_days=1)

    relative = ic["factors"]["relative_strength"]["5"]
    assert relative["available"] is True
    assert relative["n_periods"] > 0
    assert -1.0 <= relative["mean_ic"] <= 1.0
    # 广度全缺 → 不能凭空产出 IC。
    assert ic["factors"]["breadth"]["5"]["available"] is False


def test_spearman_matches_known_relationships() -> None:
    assert _spearman([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0]) == pytest.approx(1.0)
    assert _spearman([1.0, 2.0, 3.0, 4.0], [40.0, 30.0, 20.0, 10.0]) == pytest.approx(-1.0)
    # 非线性但单调 → Rank IC 仍为 1（这正是用 Spearman 而非 Pearson 的原因）。
    assert _spearman([1.0, 2.0, 3.0, 4.0], [1.0, 4.0, 9.0, 16.0]) == pytest.approx(1.0)
    # 并列值取平均秩；全并列时相关系数无定义，返回 None 而不是 0。
    assert _spearman([1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 3.0, 4.0]) is None
    assert _spearman([1.0, 2.0], [1.0, 2.0]) is None


def _observation(
    *,
    day: str,
    label: str,
    excess: float,
    state: str = ENTRY_READY_TO_START,
) -> DirectionObservation:
    return DirectionObservation(
        decision_date=day,
        sector_label=label,
        entry_state=state,
        evidence_quality="complete",
        mainline_status="confirmed",
        confidence="中",
        opportunity_available=True,
        change_1d_percent=1.0,
        change_5d_percent=2.0,
        factors={},
        gate_inputs={},
        features={},
        selection_rank=None,
        forward={
            5: ForwardOutcome(
                horizon=5,
                entry_date=day,
                exit_date=day,
                sector_return_percent=excess,
                benchmark_return_percent=0.0,
                excess_percent=excess,
                max_adverse_excess_percent=-1.0,
                benchmark_calendar_aligned=True,
            )
        },
    )


def test_group_stats_aggregates_at_decision_day_level() -> None:
    """同一天的多个观测高度相关，必须先按日取均值再跨日检验，否则 t 值被放大。"""
    observations = [
        _observation(day="2025-03-03", label="A", excess=4.0),
        _observation(day="2025-03-03", label="B", excess=2.0),
        _observation(day="2025-03-04", label="A", excess=3.0),
        _observation(day="2025-03-05", label="A", excess=5.0),
    ]
    # 横截面均值：3/3 取 1.0（含一个 -2.0 的对照），其余天取 0。
    demeaned = {
        ("2025-03-03", 5): 1.0,
        ("2025-03-04", 5): 0.0,
        ("2025-03-05", 5): 0.0,
    }
    stats = _group_stats(
        observations, horizon=5, demeaned=demeaned, min_decision_days=3
    )

    assert stats["observation_count"] == 4
    assert stats["decision_day_count"] == 3
    # 观测加权：去均值后是 3.0 / 1.0 / 3.0 / 5.0，均值 3.0（"平均每笔多少超额"）。
    assert stats["mean_demeaned_excess_percent"] == pytest.approx(3.0)
    # 决策日加权：3/3 先平均成 2.0，再与 3.0、5.0 平均 → 10/3。t 检验用的是这个。
    assert stats["mean_decision_day_excess_percent"] == pytest.approx(10.0 / 3.0, abs=1e-3)
    assert stats["hit_rate_percent"] == pytest.approx(100.0)
    assert stats["decision_day_t_stat"] is not None
    assert stats["mean_max_adverse_excess_percent"] == pytest.approx(-1.0)


def test_group_stats_requires_minimum_decision_days_for_significance() -> None:
    observations = [
        _observation(day=f"2025-03-{index:02d}", label="A", excess=3.0)
        for index in range(3, 10)
    ]
    demeaned = {(item.decision_date, 5): 0.0 for item in observations}
    stats = _group_stats(
        observations, horizon=5, demeaned=demeaned, min_decision_days=30
    )
    assert stats["decision_day_count"] == 7
    assert stats["significant"] is False


def test_threshold_scan_is_monotone_and_reuses_production_gate(days: list[str]) -> None:
    replay = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5,),
        warmup_days=61,
        step=2,
        entry_policy_version=ENTRY_POLICY_VERSION,
    )
    rows = scan_entry_gate_thresholds(
        replay,
        horizon=5,
        grids={
            "direction": (0.0, 40.0, 55.0, 80.0, 101.0),
            "setup": (0.0,),
            "entry": (0.0,),
            "structure": (0.0,),
        },
        min_decision_days=1,
    )
    by_direction = {
        row["thresholds"]["direction"]: row.get("observation_count", 0) for row in rows
    }
    counts = [by_direction[key] for key in sorted(by_direction)]
    # 方向阈值越高，能通过的方向只能更少，不可能更多。
    assert counts == sorted(counts, reverse=True)
    # 阈值抬到 101 分（不可达）时必然一个都不剩。
    assert by_direction[101.0] == 0

    default_rows = scan_entry_gate_thresholds(
        replay,
        horizon=5,
        grids={key: (value,) for key, value in ENTRY_GATE_THRESHOLDS.items()},
        min_decision_days=1,
    )
    produced = sum(
        item.entry_state == ENTRY_READY_TO_START for item in replay.observations
    )
    # 用线上默认阈值重扫，必须还原出重放时生产打分器自己给出的同一批 ready_to_start。
    assert default_rows[0].get("observation_count", 0) == produced


def test_threshold_scan_dispatches_to_v3_gate(days: list[str]) -> None:
    """v3 观测必须由 v3 门禁重扫；用 v2 那份实现会得到与线上不同的状态。"""
    replay = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5,),
        warmup_days=61,
        step=2,
    )
    assert replay.entry_policy_version == ENTRY_POLICY_VERSION_V3
    produced = sum(
        item.entry_state == ENTRY_READY_TO_START for item in replay.observations
    )
    rows = scan_entry_gate_thresholds(
        replay,
        horizon=5,
        grids={key: (value,) for key, value in V3_GATE_THRESHOLDS.items()},
        min_decision_days=1,
    )
    assert rows[0].get("observation_count", 0) == produced

    # v2 的维度名在 v3 上必须直接报错，不能被静默忽略成"用默认阈值扫了一遍"。
    with pytest.raises(ValueError):
        scan_entry_gate_thresholds(replay, horizon=5, grids={"setup": (10.0,)})

    # 抬高趋势阈值只能让通过的方向更少。
    monotone = scan_entry_gate_thresholds(
        replay,
        horizon=5,
        grids={"trend": (0.0, 60.0, 101.0), "participation": (0.0,), "position": (0.0,)},
        min_decision_days=1,
        collapse_non_binding=False,
    )
    by_trend = {row["thresholds"]["trend"]: row.get("observation_count", 0) for row in monotone}
    assert by_trend[0.0] >= by_trend[60.0] >= by_trend[101.0]
    assert by_trend[101.0] == 0


def test_threshold_scan_rejects_unknown_horizon(days: list[str]) -> None:
    replay = replay_sector_direction(
        **_build_inputs(days),
        forward_horizons=(5,),
        warmup_days=61,
        step=20,
    )
    with pytest.raises(ValueError):
        scan_entry_gate_thresholds(replay, horizon=20)


def test_replay_reports_when_no_sector_has_enough_history() -> None:
    short_days = _weekdays("2025-01-02", 30)
    replay = replay_sector_direction(
        price_series_by_label={"半导体": _price_rows(short_days, drift=0.001)},
        benchmark_series=_price_rows(short_days, drift=0.0002, base=4000.0),
        forward_horizons=(5,),
        warmup_days=61,
    )
    assert replay.observation_count == 0
    assert any("warmup_days" in caveat for caveat in replay.caveats)


def test_empty_replay_summary_is_honest_about_missing_samples() -> None:
    replay = DirectionReplay(
        schema_version="sector_direction_backtest.v1",
        observations=[],
        decision_dates=[],
        labels=[],
        horizons=(5,),
        step=1,
        benchmark_label="000300",
        feature_coverage={},
        skipped_days=[],
        caveats=[],
    )
    summary = summarize_direction_replay(replay)
    stats = summary["groups"][BASELINE_ALL_SECTORS]["horizons"]["5"]
    assert stats["available"] is False
    assert stats["reason"] == "no_forward_observations"
    assert summary["verdict"]["by_horizon"]["5"] == "样本不足"


# --------------------------------------------------------------------------
# 生产端加法改动的契约
# --------------------------------------------------------------------------


def _heat_row(label: str, change_1d: float, change_5d: float) -> dict:
    return {
        "sector_label": label,
        "change_1d_percent": change_1d,
        "change_5d_percent": change_5d,
        "heat_score": round(change_1d * 0.6 + change_5d * 0.4, 2),
    }


def _mainline_row(label: str, *, status: str = "confirmed") -> dict:
    return {
        "schema_version": "mainline_regime.v1",
        "sector_label": label,
        "status": status,
        "score": 72.0,
        "feature_coverage": 0.90,
        "component_scores": {
            "relative_strength": 75.0,
            "trend_persistence": 70.0,
            "fund_flow": 72.0,
            "breadth": 65.0,
            "market_structure": 70.0,
        },
        "features": {
            "cumulative_20d_net_yi": 30.0,
            "return_5d_percent": 3.0,
            "distance_from_20d_high_percent": -4.0,
            "distance_from_ma20_percent": 2.0,
            "position_label": "pullback_acceptance",
        },
    }


def _aligned_flow(today: float, five_day: float, *, pattern: str) -> dict:
    return {
        "available": True,
        "date_aligned": True,
        "today_available": True,
        "five_day_available": True,
        "today_main_force_net_yi": today,
        "cumulative_5d_net_yi": five_day,
        "pattern_label": pattern,
    }


def test_v2_rows_expose_price_structure_score_and_gate_inputs() -> None:
    rows = score_sector_opportunity_rows(
        [_heat_row("锂电池", 1.05, 2.06)],
        sector_flow_by_label={
            "锂电池": _aligned_flow(20.0, 60.0, pattern="price_flow_aligned_up")
        },
        mainline_by_label={"锂电池": _mainline_row("锂电池")},
        entry_policy_version=ENTRY_POLICY_VERSION,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["score_policy_version"] == ENTRY_POLICY_VERSION
    assert isinstance(row["price_structure_score"], float)
    gate = row["entry_gate_inputs"]
    assert set(gate) == {
        "flow_confirmed",
        "flow_broadly_weak",
        "flow_five_day_negative",
        "overheated",
        "mainline_status",
        "position_label",
    }
    assert gate["flow_confirmed"] is True
    assert gate["flow_five_day_negative"] is False
    assert gate["mainline_status"] == "confirmed"
    # 全分量齐备时可得权重占比必须是 1.0，中性填充不改变结果。
    assert row["component_coverage"] == {"direction": 1.0, "setup": 1.0}
    # 入场成熟度必须能被三个分项精确还原，否则阈值重扫得到的状态不等于线上状态。
    reconstructed = (
        row["direction_score"] * 0.35
        + row["setup_maturity_score"] * 0.40
        + row["price_structure_score"] * 0.25
    )
    assert reconstructed == pytest.approx(row["entry_readiness_score"], abs=0.02)


def test_score_rows_can_keep_invalid_directions_for_backtesting() -> None:
    heat = [_heat_row("退潮板块", -1.5, -6.0)]
    flow = {
        "退潮板块": {
            "available": True,
            "date_aligned": True,
            "today_available": True,
            "five_day_available": True,
            "today_main_force_net_yi": -20.0,
            "cumulative_5d_net_yi": -80.0,
            "pattern_label": "weak_outflow",
        }
    }
    mainline = {"退潮板块": _mainline_row("退潮板块", status="fading")}

    dropped = score_sector_opportunity_rows(
        heat, sector_flow_by_label=flow, mainline_by_label=mainline
    )
    kept = score_sector_opportunity_rows(
        heat,
        sector_flow_by_label=flow,
        mainline_by_label=mainline,
        drop_unavailable=False,
    )

    assert dropped == []
    assert len(kept) == 1
    assert kept[0]["entry_state"] == ENTRY_INVALID
    assert kept[0]["opportunity_available"] is False


def _load_cli_module():
    """按文件路径加载 CLI，避免与仓库根目录的 `scripts/` 形成命名空间包冲突。"""
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_sector_direction_backtest.py"
    spec = importlib.util.spec_from_file_location("_run_sector_direction_backtest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cli_renders_report_and_summary_from_injected_inputs(
    days: list[str], tmp_path: Path
) -> None:
    """CLI 端到端（注入数据，不联网）：报告能渲染、summary 可被机器读取。"""
    cli = _load_cli_module()
    base = _build_inputs(days)
    inputs = {
        "price_series_by_label": base["price_series_by_label"],
        "flow_series_by_label": base["flow_series_by_label"],
        "benchmark_by_label": {label: "000300" for label in _LABELS},
        "benchmark_series_by_key": {"000300": base["benchmark_series"]},
        "unavailable": {"不存在的板块": "insufficient_price_history: 3"},
    }
    payload = cli.build_direction_backtest_report(
        sector_labels=list(_LABELS),
        forward_horizons=(5, 20),
        warmup_days=61,
        step=5,
        scan_thresholds=True,
        scan_horizon=5,
        out_dir=str(tmp_path),
        inputs=inputs,
    )

    # policy_evaluated 必须来自重放实际使用的口径，不能是写死的常量：
    # 否则报告会把 v3 的结果标成 v2，读报告的人无法判断自己在看哪套规则。
    assert payload["policy_evaluated"] == ENTRY_POLICY_VERSION_V3
    assert (
        payload["replays"][0]["summary"]["verdict"]["policy_evaluated"]
        == ENTRY_POLICY_VERSION_V3
    )
    assert payload["decision_policy"] == "shadow_record_only"
    assert payload["auto_tuning_eligible"] is False
    assert payload["loaded_label_count"] == len(_LABELS)
    assert payload["observation_count"] > 0
    assert payload["unavailable"] == {"不存在的板块": "insufficient_price_history: 3"}
    assert len(payload["replays"]) == 1
    assert payload["replays"][0]["threshold_scan"]

    report = (tmp_path / "report.txt").read_text(encoding="utf-8")
    assert "板块方向模型前瞻收益评估" in report
    assert "基准·全板块等权" in report
    assert "单因子 Rank IC" in report
    assert "入场线结论" in report
    assert "不存在的板块" in report
    # 报告必须原样带上缺口声明，不能只在 JSON 里藏着。
    assert "上涨广度" in report

    reloaded = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert reloaded["schema_version"] == payload["schema_version"]

    v2_payload = cli.build_direction_backtest_report(
        sector_labels=list(_LABELS),
        forward_horizons=(5,),
        warmup_days=61,
        step=10,
        out_dir=str(tmp_path / "v2"),
        inputs=inputs,
        entry_policy_version=ENTRY_POLICY_VERSION,
    )
    assert v2_payload["policy_evaluated"] == ENTRY_POLICY_VERSION
    assert (
        v2_payload["replays"][0]["summary"]["verdict"]["policy_evaluated"]
        == ENTRY_POLICY_VERSION
    )


def test_cli_fails_closed_when_no_data_is_available(tmp_path: Path) -> None:
    """上游不可得时必须报错退出，而不是拿空数据产出一份"看起来有结论"的报告。"""
    cli = _load_cli_module()
    with pytest.raises(cli.DirectionBacktestUnavailable):
        cli.build_direction_backtest_report(
            sector_labels=["半导体"],
            out_dir=str(tmp_path),
            inputs={
                "price_series_by_label": {},
                "flow_series_by_label": {},
                "benchmark_by_label": {},
                "benchmark_series_by_key": {},
                "unavailable": {"半导体": "price_fetch_failed: ConnectError"},
            },
        )
    assert not (tmp_path / "report.txt").exists()


def test_classify_entry_state_threshold_override() -> None:
    kwargs = {
        "evidence_quality": "complete",
        "mainline_status": "confirmed",
        "direction_score": 52.0,
        "setup_score": 58.0,
        "entry_score": 62.0,
        "structure_score": 70.0,
        "flow_confirmed": True,
        "flow_broadly_weak": False,
        "overheated": False,
        "position_label": "pullback_acceptance",
    }
    # 默认阈值下方向分 52 < 55，不能布局。
    assert classify_entry_state(**kwargs) != ENTRY_READY_TO_START
    # 放宽方向阈值后同一份证据即可通过——证明阈值真的是可覆盖的入参。
    assert (
        classify_entry_state(**kwargs, entry_thresholds={"direction": 50.0})
        == ENTRY_READY_TO_START
    )
    # 资金未确认时，无论阈值多低都不能布局。
    assert (
        classify_entry_state(
            **{**kwargs, "flow_confirmed": False},
            entry_thresholds={"direction": 0.0, "setup": 0.0, "entry": 0.0, "structure": 0.0},
        )
        != ENTRY_READY_TO_START
    )


# --------------------------------------------------------------------------
# 第 1 层口径修复的回归契约
# --------------------------------------------------------------------------


def test_flow_percentiles_use_scale_normalised_values_not_absolute_yi() -> None:
    """大板块不能仅因为绝对亿元大就占据资金分位榜首。

    小板块的净流入相对自身体量强得多（0.5/1.0 = 50% vs 50/500 = 10%），归一化后它的
    资金分位必须更高。这一项在离线回测里把 fund_flow 的 Rank IC 从显著为负翻成正。
    """
    from app.services.mainline_regime import _build_percentile_inputs

    flows = {
        "大板块": {
            "date_aligned": True,
            "flow_universe": "eastmoney_board",
            "cumulative_5d_net_yi": 50.0,
            "normalized_5d_net": 0.10,
        },
        "小板块": {
            "date_aligned": True,
            "flow_universe": "eastmoney_board",
            "cumulative_5d_net_yi": 0.5,
            "normalized_5d_net": 0.50,
        },
    }
    percentiles = _build_percentile_inputs(["大板块", "小板块"], {}, flows)
    assert percentiles["flow_5d"]["小板块"] > percentiles["flow_5d"]["大板块"]


def test_flow_percentiles_are_partitioned_by_flow_universe() -> None:
    """东财 BK 板块与主题指数 f62 聚合是两把尺子，必须分池排名。"""
    from app.services.mainline_regime import _build_percentile_inputs

    flows = {
        "A股板块1": {
            "date_aligned": True,
            "flow_universe": "eastmoney_board",
            "normalized_5d_net": 0.10,
        },
        "A股板块2": {
            "date_aligned": True,
            "flow_universe": "eastmoney_board",
            "normalized_5d_net": 0.20,
        },
        "港股": {
            "date_aligned": True,
            "flow_universe": "index_constituent_aggregate",
            "normalized_5d_net": 0.01,
        },
    }
    percentiles = _build_percentile_inputs(
        ["A股板块1", "A股板块2", "港股"], {}, flows
    )
    pool = percentiles["flow_5d"]
    # 港股独占一个池 → 池内唯一 → 分位 50，而不是因为数值最小被排到 A 股池的底部。
    assert pool["港股"] == pytest.approx(50.0)
    assert pool["A股板块1"] == pytest.approx(25.0)
    assert pool["A股板块2"] == pytest.approx(75.0)


def test_hk_sectors_benchmark_against_hang_seng_not_csi300() -> None:
    from app.services.discovery_sector_position import resolve_benchmark_for_sector

    assert resolve_benchmark_for_sector("港股") == ("HSI", "恒生指数")
    assert resolve_benchmark_for_sector("恒生科技") == ("HSTECH", "恒生科技指数")
    assert resolve_benchmark_for_sector("港股医药")[0] == "HSI"
    assert resolve_benchmark_for_sector("半导体") == ("000300", "沪深300")


def test_percentile_universe_expands_denominator_without_emitting_regimes() -> None:
    """额外板块只补分位分母，不产出 regime 行、不进入方向观察池。"""
    from app.services.mainline_regime import build_mainline_regime_snapshot

    def position(return_20d: float) -> dict:
        return {
            "available": True,
            "return_20d_percent": return_20d,
            "relative_return_20d_percent": return_20d,
            "distance_from_ma20_percent": 1.0,
            "sample_days": 80,
        }

    # 5.5 刻意避开分母板块的取值，否则并列会让分位数恰好等于 50 而掩盖问题。
    evidence = {"半导体": position(5.5)}
    universe = {f"陪跑{index}": position(float(index)) for index in range(1, 10)}

    narrow = build_mainline_regime_snapshot(
        [{"sector_label": "半导体", "change_1d_percent": 1.0}],
        sector_position_by_label=evidence,
        sector_labels=["半导体"],
    )
    wide = build_mainline_regime_snapshot(
        [{"sector_label": "半导体", "change_1d_percent": 1.0}],
        sector_position_by_label=evidence,
        sector_labels=["半导体"],
        percentile_position_by_label=universe,
    )

    assert narrow["sector_count"] == 1
    assert wide["sector_count"] == 1, "分母板块不能产出 regime 行"
    assert narrow["percentile_universe_size"] == 1
    assert wide["percentile_universe_size"] == 10
    assert wide["percentile_universe_expanded"] is True
    # 单板块池里分位恒为 50（"相对自己"没有信息量）；扩到 10 个板块后 5.5 的超额
    # 严格大于 5 个、并列 1 个（自己）→ (5 + 0.5) / 10 = 55 分位。
    narrow_pct = narrow["sectors"][0]["features"]["relative_strength_percentile"]
    wide_pct = wide["sectors"][0]["features"]["relative_strength_percentile"]
    assert narrow_pct == pytest.approx(50.0)
    assert wide_pct == pytest.approx(55.0)


def test_relative_percentile_never_mixes_absolute_pool_per_label() -> None:
    """逐个板块回落到绝对收益分位会把两个不同分布混进同一个 relative_score。"""
    from app.services.mainline_regime import _build_percentile_inputs

    positions = {
        "有基准": {
            "relative_return_20d_percent": 3.0,
            "return_20d_percent": 8.0,
        },
        "无基准": {"return_20d_percent": 20.0},
    }
    percentiles = _build_percentile_inputs(["有基准", "无基准"], positions, {})
    assert set(percentiles["relative_20d"]) == {"有基准"}
    assert set(percentiles["absolute_20d"]) == {"有基准", "无基准"}


def test_missing_component_is_neutral_filled_not_reweighted() -> None:
    """缺失分量按中性 50 计入原权重，不把话语权让给剩下的分量。"""
    from app.services.sector_opportunity_scoring import _weighted_neutral_fill_score

    full, coverage_full = _weighted_neutral_fill_score(
        ((100.0, 0.5), (100.0, 0.25), (100.0, 0.25))
    )
    assert full == pytest.approx(100.0)
    assert coverage_full == pytest.approx(1.0)

    partial, coverage_partial = _weighted_neutral_fill_score(
        ((100.0, 0.5), (None, 0.25), (None, 0.25))
    )
    # 旧的重归一化行为会给出 100.0；中性填充给出 0.5*100 + 0.5*50 = 75。
    assert partial == pytest.approx(75.0)
    assert coverage_partial == pytest.approx(0.5)

    none_available, coverage_none = _weighted_neutral_fill_score(
        ((None, 0.5), (None, 0.5))
    )
    assert none_available is None
    assert coverage_none == pytest.approx(0.0)


def test_missing_breadth_no_longer_amplifies_fund_flow_weight() -> None:
    """指数型板块缺广度时，形态成熟度不应变成"几乎只看资金"。"""
    heat = [_heat_row("恒生科技", 1.0, 2.0)]
    flow = {"恒生科技": _aligned_flow(10.0, 40.0, pattern="price_flow_aligned_up")}
    mainline = _mainline_row("恒生科技")
    mainline["component_scores"]["fund_flow"] = 100.0
    mainline["component_scores"]["breadth"] = None
    mainline["feature_coverage"] = 0.90

    rows = score_sector_opportunity_rows(
        heat,
        sector_flow_by_label=flow,
        mainline_by_label={"恒生科技": mainline},
        entry_policy_version=ENTRY_POLICY_VERSION,
    )
    row = rows[0]
    assert row["component_coverage"]["setup"] == pytest.approx(0.75)
    # 重归一化会把资金权重从 0.50 抬到 0.667；中性填充下广度按 50 计入它自己的 0.25。
    expected = 0.50 * 100.0 + 0.25 * 50.0 + 0.15 * 70.0 + 0.10 * 70.0
    # setup 还会叠加形态/资金加减分，这里只校验基础加权部分没被放大。
    assert row["setup_maturity_score"] >= expected
    assert row["setup_maturity_score"] < 100.0


def test_pullback_state_requires_five_day_flow_not_negative() -> None:
    """「等待合适位置」不能挂在资金正在流出的方向上。"""
    kwargs = {
        "evidence_quality": "complete",
        "mainline_status": "forming",
        "direction_score": 70.0,
        "setup_score": 55.0,
        "entry_score": 55.0,
        "structure_score": 60.0,
        "flow_confirmed": False,
        "flow_broadly_weak": False,
        "overheated": True,
        "position_label": "high_extended",
    }
    assert classify_entry_state(**kwargs) == "ready_on_pullback"
    assert (
        classify_entry_state(**{**kwargs, "flow_five_day_negative": True})
        == "forming"
    )


def test_correlated_directions_are_deduplicated_by_measured_correlation() -> None:
    """储能/锂电池这类未进手写映射表的高相关方向不应同时入选。"""
    from app.services.sector_opportunity_scoring import (
        select_scored_sector_opportunities,
    )

    base = [0.4, -0.9, 1.3, 0.2, -0.6, 1.1, -0.3, 0.8, -1.2, 0.5,
            0.7, -0.4, 0.9, -1.1, 0.3, 0.6, -0.8, 1.0, -0.2, 0.1]
    rows = [
        {
            "sector_label": label,
            "score_policy_version": ENTRY_POLICY_VERSION,
            "entry_state": ENTRY_READY_TO_START,
            "evidence_quality": "complete",
            "research_score": score,
            "entry_readiness_score": score,
            "track": "momentum",
            "sector_group": label,
        }
        for label, score in (("储能", 90.0), ("锂电池", 88.0), ("银行", 60.0))
    ]
    series = {
        "储能": base,
        # 锂电池与储能几乎同一条收益曲线 → 同一笔风险暴露。
        "锂电池": [value * 1.02 + 0.01 for value in base],
        "银行": list(reversed(base)),
    }

    without_series = select_scored_sector_opportunities(rows, max_total=3)
    with_series = select_scored_sector_opportunities(
        rows, max_total=3, return_series_by_label=series
    )

    assert [item["sector_label"] for item in without_series] == ["储能", "锂电池", "银行"]
    assert [item["sector_label"] for item in with_series] == ["储能", "银行"]


def test_correlation_dedup_is_skipped_when_series_are_too_short() -> None:
    """样本不足时宁可不去重，也不用噪声相关系数误杀方向。"""
    from app.services.sector_opportunity_scoring import (
        select_scored_sector_opportunities,
    )

    rows = [
        {
            "sector_label": label,
            "score_policy_version": ENTRY_POLICY_VERSION,
            "entry_state": ENTRY_READY_TO_START,
            "evidence_quality": "complete",
            "research_score": score,
            "entry_readiness_score": score,
            "track": "momentum",
            "sector_group": label,
        }
        for label, score in (("储能", 90.0), ("锂电池", 88.0))
    ]
    short = {"储能": [1.0, -1.0, 1.0], "锂电池": [1.0, -1.0, 1.0]}
    selected = select_scored_sector_opportunities(
        rows, max_total=2, return_series_by_label=short
    )
    assert [item["sector_label"] for item in selected] == ["储能", "锂电池"]


def test_correlation_dedup_uses_actual_common_trading_dates() -> None:
    from app.services.sector_opportunity_scoring import (
        select_scored_sector_opportunities,
    )

    rows = [
        {
            "sector_label": label,
            "score_policy_version": ENTRY_POLICY_VERSION,
            "entry_state": ENTRY_READY_TO_START,
            "evidence_quality": "complete",
            "research_score": score,
            "entry_readiness_score": score,
            "track": "momentum",
            "sector_group": label,
        }
        for label, score in (("储能", 90.0), ("锂电池", 88.0))
    ]
    left = {f"2026-06-{day:02d}": float(day) for day in range(1, 21)}
    right = {f"2026-06-{day:02d}": float(day) for day in range(11, 31)}

    selected = select_scored_sector_opportunities(
        rows,
        max_total=2,
        return_series_by_label={"储能": left, "锂电池": right},
    )

    # 两边各有 20 点，但实际共同交易日只有 10 个，不能尾部强行对齐后误杀。
    assert [item["sector_label"] for item in selected] == ["储能", "锂电池"]


# --------------------------------------------------------------------------
# v3 契约
# --------------------------------------------------------------------------


def test_v3_is_the_production_default_and_v2_remains_replayable() -> None:
    heat = [_heat_row("锂电池", 1.05, 2.06)]
    flow = {"锂电池": _aligned_flow(20.0, 60.0, pattern="price_flow_aligned_up")}
    mainline = {"锂电池": _mainline_row("锂电池")}

    default_rows = score_sector_opportunity_rows(
        heat, sector_flow_by_label=flow, mainline_by_label=mainline
    )
    v2_rows = score_sector_opportunity_rows(
        heat,
        sector_flow_by_label=flow,
        mainline_by_label=mainline,
        entry_policy_version=ENTRY_POLICY_VERSION,
    )
    assert default_rows[0]["score_policy_version"] == ENTRY_POLICY_VERSION_V3
    assert v2_rows[0]["score_policy_version"] == ENTRY_POLICY_VERSION


def test_v3_blocks_are_orthogonal_and_composite_is_reproducible() -> None:
    """每个原始分量只进一次；综合分必须能由三块精确还原。"""
    from app.services.sector_opportunity_scoring import (
        V3_BLOCK_WEIGHTS,
        V3_PARTICIPATION_WEIGHTS,
        V3_TREND_WEIGHTS,
    )

    rows = score_sector_opportunity_rows(
        [_heat_row("锂电池", 1.05, 2.06)],
        sector_flow_by_label={
            "锂电池": _aligned_flow(20.0, 60.0, pattern="price_flow_aligned_up")
        },
        mainline_by_label={"锂电池": _mainline_row("锂电池")},
    )
    row = rows[0]
    components = _mainline_row("锂电池")["component_scores"]

    expected_trend = (
        components["relative_strength"] * V3_TREND_WEIGHTS["relative_strength"]
        + components["trend_persistence"] * V3_TREND_WEIGHTS["trend_persistence"]
    )
    expected_participation = (
        components["fund_flow"] * V3_PARTICIPATION_WEIGHTS["fund_flow"]
        + components["breadth"] * V3_PARTICIPATION_WEIGHTS["breadth"]
    )
    assert row["trend_strength_score"] == pytest.approx(expected_trend, abs=0.01)
    assert row["participation_score"] == pytest.approx(expected_participation, abs=0.01)
    # 价格位置直接取 market_structure，不再叠加任何 pullback / 贴高点加减分。
    assert row["position_risk_score"] == pytest.approx(components["market_structure"])
    assert row["direction_score"] == pytest.approx(
        expected_trend * V3_BLOCK_WEIGHTS["trend_strength"]
        + expected_participation * V3_BLOCK_WEIGHTS["participation"]
        + components["market_structure"] * V3_BLOCK_WEIGHTS["position_risk"],
        abs=0.02,
    )
    # v3 不再产出这三个 v2 分数，避免把共线的东西继续当三重确认展示。
    assert "setup_maturity_score" not in row
    assert "entry_readiness_score" not in row
    assert "price_structure_score" not in row


def test_v3_overheat_discloses_risk_instead_of_blocking_entry() -> None:
    """实测过热方向的前瞻超额显著为正；v3 不再用它否决布局，只缩小首批。"""
    mainline = _mainline_row("机器人")
    mainline["features"]["return_5d_percent"] = 14.0
    mainline["features"]["distance_from_20d_high_percent"] = -0.5
    mainline["features"]["position_label"] = "high_extended"

    rows = score_sector_opportunity_rows(
        [_heat_row("机器人", 5.2, 14.0)],
        sector_flow_by_label={
            "机器人": _aligned_flow(18.0, 42.0, pattern="price_flow_aligned_up")
        },
        mainline_by_label={"机器人": mainline},
    )
    row = rows[0]
    assert row["entry_state"] == ENTRY_READY_TO_START
    assert row["overheat_flags"], "过热必须被记录为风险，而不是消失"
    assert row["first_tranche_scale"] < 1.0
    assert "首批" in row["entry_hint"]
    # 同一份证据在 v2 下只会得到"等待过热缓解"。
    v2_row = score_sector_opportunity_rows(
        [_heat_row("机器人", 5.2, 14.0)],
        sector_flow_by_label={
            "机器人": _aligned_flow(18.0, 42.0, pattern="price_flow_aligned_up")
        },
        mainline_by_label={"机器人": mainline},
        entry_policy_version=ENTRY_POLICY_VERSION,
    )[0]
    assert v2_row["entry_state"] == "ready_on_pullback"


def test_v3_single_day_divergence_no_longer_invalidates_a_direction() -> None:
    """v2 只要命中单日 distribution 就判 invalid，实测导致 91% 的观测被否决。"""
    mainline = _mainline_row("半导体")
    flow = {
        "半导体": {
            "available": True,
            "date_aligned": True,
            "today_available": True,
            "five_day_available": True,
            # 今日流出但 5 日与 20 日累计仍为正 → 不构成持续转弱。
            "today_main_force_net_yi": -8.0,
            "cumulative_5d_net_yi": 30.0,
            "pattern_label": "distribution",
        }
    }
    heat = [_heat_row("半导体", 1.2, 3.0)]

    v3_row = score_sector_opportunity_rows(
        heat,
        sector_flow_by_label=flow,
        mainline_by_label={"半导体": mainline},
        drop_unavailable=False,
    )[0]
    v2_row = score_sector_opportunity_rows(
        heat,
        sector_flow_by_label=flow,
        mainline_by_label={"半导体": mainline},
        drop_unavailable=False,
        entry_policy_version=ENTRY_POLICY_VERSION,
    )[0]

    assert v2_row["entry_state"] == ENTRY_INVALID
    assert v3_row["entry_state"] != ENTRY_INVALID
    assert any("量价背离" in item for item in v3_row["penalties"])


def test_v3_invalidates_only_on_multi_window_flow_weakness() -> None:
    from app.services.sector_opportunity_scoring import classify_entry_state_v3

    base = {
        "evidence_quality": "complete",
        "mainline_status": "confirmed",
        "trend_strength": 80.0,
        "participation": 70.0,
        "position_risk": 60.0,
        "structure_broken": False,
    }
    assert classify_entry_state_v3(**base) == ENTRY_READY_TO_START
    assert (
        classify_entry_state_v3(**{**base, "mainline_status": "crowded"})
        == ENTRY_READY_TO_START
    )
    assert classify_entry_state_v3(**{**base, "mainline_status": "fading"}) == ENTRY_INVALID
    assert classify_entry_state_v3(**{**base, "structure_broken": True}) == ENTRY_INVALID
    # 只有趋势与参与度**同时**处于低位才算 invalid。
    assert (
        classify_entry_state_v3(**{**base, "trend_strength": 30.0, "participation": 20.0})
        == ENTRY_INVALID
    )
    # 单弱不构成否决：趋势不够 → 只观察；趋势够但参与度不够 → 等待。
    assert classify_entry_state_v3(**{**base, "trend_strength": 45.0}) == ENTRY_FORMING
    assert (
        classify_entry_state_v3(**{**base, "participation": 40.0})
        == "ready_on_pullback"
    )
    assert (
        classify_entry_state_v3(**{**base, "mainline_status": "neutral"})
        == "ready_on_pullback"
    )


def test_v3_insufficient_evidence_can_never_reach_ready() -> None:
    from app.services.sector_opportunity_scoring import classify_entry_state_v3

    assert (
        classify_entry_state_v3(
            evidence_quality="insufficient",
            mainline_status="confirmed",
            trend_strength=100.0,
            participation=100.0,
            position_risk=100.0,
            structure_broken=False,
        )
        == ENTRY_FORMING
    )


# --------------------------------------------------------------------------
# 跨日滞回
# --------------------------------------------------------------------------


def _v3_row(label: str, *, entry_state: str, trend: float) -> dict:
    return {
        "sector_label": label,
        "score_policy_version": ENTRY_POLICY_VERSION_V3,
        "entry_state": entry_state,
        "trend_strength_score": trend,
        "participation_score": 60.0,
        "position_risk_score": 55.0,
        "direction_score": trend * 0.7 + 60.0 * 0.15 + 55.0 * 0.15,
        "entry_reason": "原因",
        "entry_hint": "提示",
    }


def test_first_qualifying_day_only_observes() -> None:
    """首次通过入场线当天不直接给买入动作，避免边界抖动带来的天天换人。"""
    from app.services.sector_direction_state import (
        DirectionStateRecord,
        apply_direction_state_hysteresis,
    )

    rows = [_v3_row("半导体", entry_state=ENTRY_READY_TO_START, trend=70.0)]
    day_one = apply_direction_state_hysteresis(
        rows, trade_date="2026-06-10", previous_trade_date="2026-06-09", previous_states={}
    )[0]
    assert day_one["entry_state"] == ENTRY_FORMING
    assert day_one["raw_entry_state"] == ENTRY_READY_TO_START
    assert day_one["consecutive_qualifying_days"] == 1
    assert day_one["execution_eligible"] is False
    assert "已满足 1 天" in day_one["entry_reason"]

    day_two = apply_direction_state_hysteresis(
        rows,
        trade_date="2026-06-11",
        previous_trade_date="2026-06-10",
        previous_states={
            "半导体": DirectionStateRecord(
                trade_date="2026-06-10",
                sector_label="半导体",
                entry_state=ENTRY_FORMING,
                raw_entry_state=ENTRY_READY_TO_START,
                qualifies_for_ready=True,
                consecutive_qualifying_days=1,
            )
        },
    )[0]
    assert day_two["entry_state"] == ENTRY_READY_TO_START
    assert day_two["consecutive_qualifying_days"] == 2
    assert day_two["execution_eligible"] is True


def test_hysteresis_band_prevents_same_day_downgrade() -> None:
    """已确认的方向掉到入场线以下、但仍在退出线之上时保持可布局，不当天翻脸。"""
    from app.services.sector_direction_state import (
        EXIT_TREND_THRESHOLD,
        DirectionStateRecord,
        apply_direction_state_hysteresis,
    )

    confirmed = {
        "半导体": DirectionStateRecord(
            trade_date="2026-06-10",
            sector_label="半导体",
            entry_state=ENTRY_READY_TO_START,
            raw_entry_state=ENTRY_READY_TO_START,
            qualifies_for_ready=True,
            consecutive_qualifying_days=3,
        )
    }
    inside_band = apply_direction_state_hysteresis(
        [
            _v3_row(
                "半导体",
                entry_state="ready_on_pullback",
                trend=EXIT_TREND_THRESHOLD + 1.0,
            )
        ],
        trade_date="2026-06-11",
        previous_trade_date="2026-06-10",
        previous_states=confirmed,
    )[0]
    assert inside_band["entry_state"] == ENTRY_READY_TO_START

    below_band = apply_direction_state_hysteresis(
        [
            _v3_row(
                "半导体",
                entry_state="ready_on_pullback",
                trend=EXIT_TREND_THRESHOLD - 1.0,
            )
        ],
        trade_date="2026-06-11",
        previous_trade_date="2026-06-10",
        previous_states=confirmed,
    )[0]
    assert below_band["entry_state"] == "ready_on_pullback"


def test_invalid_always_breaks_out_of_the_hysteresis_band() -> None:
    """结构/资金真的坏掉时，滞回不能把方向继续留在可布局里。"""
    from app.services.sector_direction_state import (
        DirectionStateRecord,
        apply_direction_state_hysteresis,
    )

    row = apply_direction_state_hysteresis(
        [_v3_row("半导体", entry_state=ENTRY_INVALID, trend=95.0)],
        trade_date="2026-06-11",
        previous_trade_date="2026-06-10",
        previous_states={
            "半导体": DirectionStateRecord(
                trade_date="2026-06-10",
                sector_label="半导体",
                entry_state=ENTRY_READY_TO_START,
                raw_entry_state=ENTRY_READY_TO_START,
                qualifies_for_ready=True,
                consecutive_qualifying_days=5,
            )
        },
    )[0]
    assert row["entry_state"] == ENTRY_INVALID


def test_missing_history_does_not_become_an_extra_gate() -> None:
    """没有历史时（首次运行/存储不可用）滞回必须完全不生效，而不是变成额外一道门。"""
    from app.services.sector_direction_state import apply_direction_state_hysteresis

    row = apply_direction_state_hysteresis(
        [_v3_row("半导体", entry_state=ENTRY_READY_TO_START, trend=70.0)],
        trade_date="2026-06-10",
        previous_states=None,
    )[0]
    assert row["entry_state"] == ENTRY_READY_TO_START
    assert row["execution_eligible"] is True


def test_hysteresis_leaves_v2_rows_untouched() -> None:
    from app.services.sector_direction_state import apply_direction_state_hysteresis

    v2_row = {
        "sector_label": "半导体",
        "score_policy_version": ENTRY_POLICY_VERSION,
        "entry_state": ENTRY_READY_TO_START,
    }
    result = apply_direction_state_hysteresis(
        [v2_row], trade_date="2026-06-10", previous_states={}
    )[0]
    assert result == v2_row


def test_direction_states_round_trip_through_the_store(tmp_path, monkeypatch) -> None:
    """落盘 → 读回：同一交易日同一板块幂等覆盖，且能被下一日的滞回消费。"""
    import sqlite3
    from contextlib import contextmanager

    from app.db_migrations import run_migrations
    from app.services import sector_direction_state as store

    database = tmp_path / "states.db"
    with sqlite3.connect(database) as bootstrap:
        run_migrations(bootstrap)

    @contextmanager
    def _connect():
        from app.db_connect import DbConnection

        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        try:
            yield DbConnection(connection, "sqlite")
        finally:
            if connection:
                connection.close()

    monkeypatch.setattr("app.database._connect", _connect)

    rows = store.apply_direction_state_hysteresis(
        [_v3_row("半导体", entry_state=ENTRY_READY_TO_START, trend=72.0)],
        trade_date="2026-06-10",
        previous_states={},
    )
    assert store.record_direction_states(rows, trade_date="2026-06-10") == 1
    # 幂等：同一天重跑不应报错也不应重复。
    assert store.record_direction_states(rows, trade_date="2026-06-10") == 1

    loaded = store.load_previous_direction_states("2026-06-10")
    assert loaded is not None
    record = loaded["半导体"]
    assert record.qualifies_for_ready is True
    assert record.consecutive_qualifying_days == 1
    assert record.entry_state == ENTRY_FORMING

    promoted = store.apply_direction_state_hysteresis(
        [_v3_row("半导体", entry_state=ENTRY_READY_TO_START, trend=72.0)],
        trade_date="2026-06-11",
        previous_trade_date="2026-06-10",
        previous_states=loaded,
    )[0]
    assert promoted["entry_state"] == ENTRY_READY_TO_START


def test_direction_state_store_distinguishes_empty_history_from_a_missing_day(
    tmp_path, monkeypatch
) -> None:
    import sqlite3
    from contextlib import contextmanager

    from app.db_connect import DbConnection
    from app.db_migrations import run_migrations
    from app.services import sector_direction_state as store

    database = tmp_path / "empty-states.db"
    with sqlite3.connect(database) as bootstrap:
        run_migrations(bootstrap)

    @contextmanager
    def _connect():
        raw = sqlite3.connect(database)
        raw.row_factory = sqlite3.Row
        try:
            yield DbConnection(raw, "sqlite")
        finally:
            raw.close()

    monkeypatch.setattr("app.database._connect", _connect)

    assert store.load_previous_direction_states("2026-06-10") is None
    rows = store.apply_direction_state_hysteresis(
        [_v3_row("半导体", entry_state=ENTRY_READY_TO_START, trend=72.0)],
        trade_date="2026-06-10",
        previous_states={},
    )
    assert store.record_direction_states(rows, trade_date="2026-06-10") == 1
    # 同一决策日重跑时，今天刚写入的行不能伪装成“上一交易日已有历史”。
    assert store.load_previous_direction_states("2026-06-09") is None
    assert store.load_previous_direction_states("2026-06-11") == {}


def test_direction_state_store_uses_mysql_upsert_syntax(monkeypatch) -> None:
    from contextlib import contextmanager

    from app.services import sector_direction_state as store

    statements: list[str] = []

    class _Cursor:
        def close(self) -> None:
            return None

    class _Connection:
        dialect = "mysql"

        def executemany(self, statement, _payload):
            statements.append(statement)
            return _Cursor()

        def commit(self) -> None:
            return None

    @contextmanager
    def _connect():
        yield _Connection()

    monkeypatch.setattr("app.database._connect", _connect)
    row = _v3_row("半导体", entry_state=ENTRY_READY_TO_START, trend=72.0)
    row["qualifies_for_ready"] = True

    assert store.record_direction_states([row], trade_date="2026-06-10") == 1
    assert "ON DUPLICATE KEY UPDATE" in statements[0]
    assert "ON CONFLICT" not in statements[0]


def test_flow_cache_loader_is_zero_network_and_builds_a_cached_benchmark(
    tmp_path, monkeypatch
) -> None:
    import sqlite3

    from app.services.sector_direction_backtest import (
        load_direction_backtest_inputs_from_flow_cache,
    )

    database = tmp_path / "flow-cache.db"
    cached_days = _weekdays("2026-01-02", 70)
    series = [
        {
            "date": day,
            "close_price": 100.0 + index,
            "main_force_net_yi": float(index % 7 - 3),
        }
        for index, day in enumerate(cached_days)
    ]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE sector_spot_cache (cache_key TEXT PRIMARY KEY, payload TEXT)"
        )
        connection.execute(
            "INSERT INTO sector_spot_cache (cache_key, payload) VALUES (?, ?)",
            ("board-flow-hist:v2:BK1036", json.dumps({"series": series})),
        )

    def _network_forbidden(*_args, **_kwargs):
        raise AssertionError("sqlite cache mode must not call the network")

    monkeypatch.setattr(
        "app.services.index_daily_client.fetch_index_daily_history",
        _network_forbidden,
    )
    loaded = load_direction_backtest_inputs_from_flow_cache(
        str(database),
        sector_labels=["半导体"],
        min_history_days=61,
    )

    assert loaded["price_series_by_label"]["半导体"]
    assert loaded["benchmark_by_label"] == {
        "半导体": "cached_equal_weight"
    }
    assert loaded["benchmark_series_by_key"]["cached_equal_weight"]
