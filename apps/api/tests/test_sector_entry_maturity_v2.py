from __future__ import annotations

from app.services.discovery_sector_prefilter import select_opportunity_evidence_labels
from app.services.sector_opportunity_scoring import (
    ENTRY_FORMING,
    ENTRY_POLICY_VERSION,
    ENTRY_READY_ON_PULLBACK,
    ENTRY_READY_TO_START,
    select_sector_opportunities,
)


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
    rows = select_sector_opportunities(
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
    rows = select_sector_opportunities(
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
    rows = select_sector_opportunities(
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
    rows = select_sector_opportunities(
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
    assert len(labels) <= 24


def test_missing_mainline_evidence_cannot_outrank_complete_forming_direction() -> None:
    heat = [
        {"sector_label": "数据完整", "change_1d_percent": 0.8, "change_5d_percent": 2.0, "heat_score": 1.28},
        {"sector_label": "数据缺失", "change_1d_percent": 9.0, "change_5d_percent": -6.0, "heat_score": 3.0},
    ]
    rows = select_sector_opportunities(
        heat,
        sector_flow_by_label={"数据完整": _flow(5.0, 8.0)},
        mainline_by_label={"数据完整": _mainline("数据完整", status="forming")},
    )

    assert rows[0]["sector_label"] == "数据完整"
    missing = next(row for row in rows if row["sector_label"] == "数据缺失")
    assert missing["evidence_quality"] == "insufficient"
    assert missing["entry_state"] == ENTRY_FORMING
