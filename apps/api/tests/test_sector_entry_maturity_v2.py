from __future__ import annotations

from app.services.discovery_sector_prefilter import (
    select_cached_high_elasticity_labels,
    select_opportunity_evidence_labels,
    select_snapshot_flow_inflection_labels,
)
from app.services.sector_opportunity_scoring import (
    ENTRY_FORMING,
    ENTRY_POLICY_VERSION,
    ENTRY_POLICY_VERSION_V3,
    ENTRY_READY_ON_PULLBACK,
    ENTRY_READY_TO_START,
    score_sector_opportunity_rows,
    select_sector_opportunities,
)


def _select_v2(*args, **kwargs):
    """显式回放 v2 口径。

    线上默认已切到 `sector_entry_maturity.2026-08.v3`，但 v2 仍然必须保留并被测试覆盖：
    历史报告是按 v2 冻结的，重新用 v3 的规则解读它们会改写既有结论。
    """
    kwargs.setdefault("entry_policy_version", ENTRY_POLICY_VERSION)
    return select_sector_opportunities(*args, **kwargs)


def _flow(today: float, five_day: float, *, pattern: str = "price_flow_aligned_up") -> dict:
    return {
        "available": True,
        "date_aligned": True,
        "today_available": True,
        "five_day_available": True,
        "today_main_force_net_yi": today,
        "cumulative_5d_net_yi": five_day,
        "pattern_label": pattern,
    }


def _mainline(
    label: str,
    *,
    status: str = "confirmed",
    coverage: float = 0.90,
    relative: float = 75.0,
    trend: float = 70.0,
    fund_flow: float = 72.0,
    breadth: float = 65.0,
    structure: float = 70.0,
    flow_20d: float = 30.0,
    return_5d: float = 4.0,
    volatility_20d: float = 26.0,
    distance_high: float = -4.0,
    position_label: str = "pullback_acceptance",
) -> dict:
    return {
        "schema_version": "mainline_regime.v1",
        "sector_label": label,
        "status": status,
        "score": 72.0,
        "feature_coverage": coverage,
        "confidence": "中",
        "component_scores": {
            "relative_strength": relative,
            "trend_persistence": trend,
            "fund_flow": fund_flow,
            "breadth": breadth,
            "market_structure": structure,
        },
        "features": {
            "cumulative_20d_net_yi": flow_20d,
            "return_5d_percent": return_5d,
            "annualized_volatility_20d_percent": volatility_20d,
            "distance_from_20d_high_percent": distance_high,
            "distance_from_ma20_percent": 2.0,
            "position_label": position_label,
        },
    }


def test_screenshot_replay_prefers_mature_lithium_over_hot_incomplete_rebound() -> None:
    heat = [
        {
            "sector_label": "锂电池",
            "change_1d_percent": 1.05,
            "change_5d_percent": 2.06,
            "heat_score": 1.45,
        },
        {
            "sector_label": "半导体材料",
            "change_1d_percent": 11.42,
            "change_5d_percent": -14.42,
            "heat_score": 1.08,
        },
        {
            "sector_label": "人工智能",
            "change_1d_percent": 6.09,
            "change_5d_percent": -5.25,
            "heat_score": 1.55,
        },
    ]
    mainline = {
        "锂电池": _mainline("锂电池"),
        "半导体材料": {
            "schema_version": "mainline_regime.v1",
            "sector_label": "半导体材料",
            "status": "insufficient",
            "score": 92.0,
            "feature_coverage": 0.30,
            "component_scores": {"breadth": 100.0},
            "features": {},
        },
        "人工智能": _mainline(
            "人工智能",
            status="neutral",
            fund_flow=20.0,
            flow_20d=-600.0,
            return_5d=-5.25,
            distance_high=-0.5,
            position_label="high_extended",
        ),
    }
    rows = _select_v2(
        heat,
        sector_flow_by_label={
            "锂电池": _flow(76.92, 210.45),
            "人工智能": _flow(45.71, -535.45),
        },
        mainline_by_label=mainline,
        focus_sectors=["人工智能"],
    )

    by_label = {row["sector_label"]: row for row in rows}
    assert rows[0]["sector_label"] == "锂电池"
    assert rows[0]["entry_state"] == ENTRY_READY_TO_START
    assert rows[0]["execution_eligible"] is True
    assert rows[0]["legacy_score"] == 47.71
    assert by_label["半导体材料"]["entry_state"] == ENTRY_FORMING
    assert by_label["半导体材料"]["evidence_quality"] == "insufficient"
    assert by_label["半导体材料"]["score"] < rows[0]["score"]
    assert "人工智能" not in by_label


def test_strong_but_extended_direction_waits_for_pullback() -> None:
    rows = _select_v2(
        [
            {
                "sector_label": "机器人",
                "change_1d_percent": 5.2,
                "change_5d_percent": 9.0,
                "heat_score": 6.72,
            }
        ],
        sector_flow_by_label={"机器人": _flow(18.0, 42.0)},
        mainline_by_label={
            "机器人": _mainline(
                "机器人",
                return_5d=9.0,
                distance_high=-0.3,
                position_label="high_extended",
            )
        },
    )

    assert rows[0]["score_policy_version"] == ENTRY_POLICY_VERSION
    assert rows[0]["entry_state"] == ENTRY_READY_ON_PULLBACK
    assert rows[0]["execution_eligible"] is False
    assert any("3%" in value for value in rows[0]["entry_triggers"])


def test_near_high_with_calm_price_and_confirmed_flow_can_start_small() -> None:
    rows = _select_v2(
        [
            {
                "sector_label": "保险",
                "change_1d_percent": -0.6,
                "change_5d_percent": 4.8,
                "heat_score": 1.56,
            }
        ],
        sector_flow_by_label={"保险": _flow(3.0, 8.0)},
        mainline_by_label={
            "保险": _mainline(
                "保险",
                return_5d=4.8,
                distance_high=-0.4,
                position_label="high_extended",
            )
        },
    )

    assert rows[0]["entry_state"] == ENTRY_READY_TO_START
    assert rows[0]["execution_eligible"] is True


def test_equivalent_broad_market_labels_only_take_one_recommendation_slot() -> None:
    heat = [
        {
            "sector_label": "港股通",
            "change_1d_percent": 0.2,
            "change_5d_percent": 3.0,
            "heat_score": 1.32,
        },
        {
            "sector_label": "港股",
            "change_1d_percent": 0.1,
            "change_5d_percent": 2.9,
            "heat_score": 1.22,
        },
        {
            "sector_label": "锂电池",
            "change_1d_percent": 0.8,
            "change_5d_percent": 2.0,
            "heat_score": 1.28,
        },
    ]
    rows = _select_v2(
        heat,
        sector_flow_by_label={
            "港股通": _flow(10.0, 40.0),
            "港股": _flow(8.0, 35.0),
            "锂电池": _flow(5.0, 20.0),
        },
        mainline_by_label={
            label: _mainline(label) for label in ("港股通", "港股", "锂电池")
        },
        max_total=3,
    )

    labels = [row["sector_label"] for row in rows]
    assert len({"港股", "港股通"}.intersection(labels)) == 1
    assert "锂电池" in labels


def test_full_market_prefilter_reserves_evidence_for_quiet_setup() -> None:
    hot_rows = [
        {
            "sector_label": f"热门{i}",
            "change_1d_percent": 8.0 - i * 0.1,
            "change_5d_percent": 15.0 - i * 0.1,
            "heat_score": 10.8 - i * 0.1,
        }
        for i in range(16)
    ]
    quiet = {
        "sector_label": "早期蓄势",
        "change_1d_percent": 0.4,
        "change_5d_percent": 1.2,
        "heat_score": 0.72,
        "advancing_ratio_percent": 56.0,
    }

    labels = select_opportunity_evidence_labels(
        [*hot_rows, quiet],
        [row["sector_label"] for row in hot_rows[:8]],
        [],
    )

    assert "早期蓄势" in labels
    assert len(labels) <= 32


def test_full_market_prefilter_recalls_same_day_flow_inflection_before_heat() -> None:
    hot_rows = [
        {
            "sector_label": f"热门{i}",
            "change_1d_percent": 5.0 - i * 0.05,
            "change_5d_percent": 12.0 - i * 0.05,
            "heat_score": 7.8 - i * 0.05,
        }
        for i in range(36)
    ]
    turning = {
        "sector_label": "资金拐点",
        "change_1d_percent": 0.3,
        "change_5d_percent": -1.0,
        "heat_score": -0.22,
        "advancing_ratio_percent": 58.0,
    }
    snapshot = {
        "items": [
            {
                "sector_label": "资金拐点",
                "main_force_net_yi": 3.2,
                "cumulative_5d_net_yi": -18.0,
            }
        ]
    }

    inflections = select_snapshot_flow_inflection_labels(
        [*hot_rows, turning],
        snapshot,
    )
    labels = select_opportunity_evidence_labels(
        [*hot_rows, turning],
        [row["sector_label"] for row in hot_rows[:8]],
        [],
        flow_inflection_labels=inflections,
    )

    assert inflections == ["资金拐点"]
    assert "资金拐点" in labels
    assert labels.index("资金拐点") < 10


def test_flow_inflection_keeps_live_today_flow_when_five_day_rank_is_stale() -> None:
    heat = [
        {
            "sector_label": "盘中回流",
            "change_1d_percent": -0.4,
            "change_5d_percent": 1.2,
            "advancing_ratio_percent": 52.0,
        }
    ]
    snapshot = {
        "trade_date": "2026-08-04",
        "items": [
            {
                "sector_label": "盘中回流",
                "main_force_net_yi": 2.6,
                "cumulative_5d_net_yi": -30.0,
                "flow_data_date": "2026-08-03",
            }
        ],
    }

    assert select_snapshot_flow_inflection_labels(heat, snapshot) == ["盘中回流"]


def test_full_market_prefilter_reserves_high_price_elasticity_evidence() -> None:
    steady_hot = [
        {
            "sector_label": f"稳步热门{i}",
            "change_1d_percent": 3.0,
            "change_5d_percent": 10.0 - i * 0.05,
            "heat_score": 5.8 - i * 0.02,
        }
        for i in range(30)
    ]
    elastic = {
        "sector_label": "高弹性转折",
        "change_1d_percent": 5.5,
        "change_5d_percent": -4.0,
        "heat_score": 1.7,
    }

    labels = select_opportunity_evidence_labels(
        [*steady_hot, elastic],
        [row["sector_label"] for row in steady_hot[:8]],
        [],
    )

    assert "高弹性转折" in labels


def test_cached_true_volatility_expands_recall_but_rejects_broken_structure() -> None:
    positions = {
        "稳定方向": {
            "available": True,
            "data_end_date": "2026-08-04",
            "annualized_volatility_20d_percent": 12.0,
            "return_20d_percent": 4.0,
            "position_label": "pullback_acceptance",
        },
        "高弹性方向": {
            "available": True,
            "data_end_date": "2026-08-04",
            "annualized_volatility_20d_percent": 38.0,
            "return_20d_percent": 8.0,
            "return_60d_percent": 18.0,
            "drawdown_recovery_20d_percent": 72.0,
            "distance_from_ma20_percent": 1.0,
            "position_label": "pullback_acceptance",
        },
        "高波动破位": {
            "available": True,
            "data_end_date": "2026-08-04",
            "annualized_volatility_20d_percent": 55.0,
            "return_20d_percent": 9.0,
            "distance_from_ma20_percent": -8.0,
            "position_label": "weak_breakdown",
        },
        "高波动但过期": {
            "available": True,
            "data_end_date": "2026-08-03",
            "annualized_volatility_20d_percent": 60.0,
            "return_20d_percent": 12.0,
            "position_label": "pullback_acceptance",
        },
    }

    labels = select_cached_high_elasticity_labels(
        positions,
        as_of_trade_date="2026-08-04",
    )

    assert labels == ["高弹性方向"]


def test_cached_elasticity_expansion_merges_price_flow_and_backtest_evidence() -> None:
    from app.services.discovery_pipeline import _expand_high_elasticity_evidence

    flow_map: dict = {}
    divergence_map: dict = {}
    position_map: dict = {}
    cached_positions = {
        "高弹性方向": {
            "available": True,
            "data_end_date": "2026-08-04",
            "annualized_volatility_20d_percent": 36.0,
            "return_20d_percent": 7.0,
            "drawdown_recovery_20d_percent": 75.0,
            "distance_from_ma20_percent": 1.2,
            "position_label": "pullback_acceptance",
        }
    }

    labels = _expand_high_elasticity_evidence(
        [{"sector_label": "高弹性方向"}],
        flow_labels=[],
        sector_flow_by_label=flow_map,
        sector_divergence_by_label=divergence_map,
        sector_position_by_label=position_map,
        percentile_position_by_label=cached_positions,
        effective_trade_date="2026-08-04",
        flow_builder=lambda _heat, extra, **_kwargs: {
            extra[0]: {"available": True, "today_main_force_net_yi": 2.0}
        },
        divergence_builder=lambda extra: {extra[0]: {"by_rule": {"x": {}}}},
    )

    assert labels == ["高弹性方向"]
    assert flow_map["高弹性方向"]["today_main_force_net_yi"] == 2.0
    assert "高弹性方向" in divergence_map
    assert position_map["高弹性方向"]["annualized_volatility_20d_percent"] == 36.0


def test_v3_selection_priority_rewards_true_sector_elasticity_without_changing_gate() -> None:
    heat = [
        {"sector_label": "低弹性", "change_1d_percent": 0.8, "change_5d_percent": 2.0, "heat_score": 1.28},
        {"sector_label": "高弹性", "change_1d_percent": 0.8, "change_5d_percent": 2.0, "heat_score": 1.28},
    ]
    flows = {label: _flow(4.0, 10.0) for label in ("低弹性", "高弹性")}
    mainlines = {
        "低弹性": _mainline("低弹性", volatility_20d=14.0),
        "高弹性": _mainline("高弹性", volatility_20d=38.0),
    }

    rows = score_sector_opportunity_rows(
        heat,
        sector_flow_by_label=flows,
        mainline_by_label=mainlines,
        entry_policy_version=ENTRY_POLICY_VERSION_V3,
    )
    by_label = {row["sector_label"]: row for row in rows}

    assert by_label["高弹性"]["entry_state"] == by_label["低弹性"]["entry_state"]
    assert by_label["高弹性"]["sector_elasticity_percentile"] > 70.0
    assert by_label["高弹性"]["selection_priority_score"] > by_label["高弹性"]["research_score"]
    assert by_label["高弹性"]["selection_path"] == "high_elasticity"


def test_missing_mainline_evidence_cannot_outrank_complete_forming_direction() -> None:
    heat = [
        {"sector_label": "数据完整", "change_1d_percent": 0.8, "change_5d_percent": 2.0, "heat_score": 1.28},
        {"sector_label": "数据缺失", "change_1d_percent": 9.0, "change_5d_percent": -6.0, "heat_score": 3.0},
    ]
    rows = _select_v2(
        heat,
        sector_flow_by_label={"数据完整": _flow(5.0, 8.0)},
        mainline_by_label={"数据完整": _mainline("数据完整", status="forming")},
    )

    assert rows[0]["sector_label"] == "数据完整"
    missing = next(row for row in rows if row["sector_label"] == "数据缺失")
    assert missing["evidence_quality"] == "insufficient"
    assert missing["entry_state"] == ENTRY_FORMING
