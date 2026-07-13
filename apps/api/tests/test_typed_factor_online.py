from __future__ import annotations

import math

from app.services.factor_ic_backtest import NavPoint
from app.services.factor_ic_research import (
    build_peer_distributions,
    score_targets_with_research_model,
)
from app.services.fund_factor_nav import factor_input_from_navs


def _navs(index: int) -> list[float]:
    value = 1.0
    rows = []
    for day in range(260):
        value *= 1 + 0.0001 * (index + 1) + math.sin(day / 13) * 0.0003
        rows.append(value)
    return rows


def _qualified_model(peers: dict) -> dict:
    economic = {
        "qualified": True,
        "top_net_positive_ratio": 0.65,
        "top_bottom_spread": 0.02,
    }
    return {
        "version": "factor_ic.v3",
        "cohort_mode": "point_in_time",
        "primary_horizon": 20,
        "point_in_time": {
            "point_in_time_scope": "membership_only",
            "nav_revision_pit": False,
        },
        "fund_classifications": {"999999": "gp"},
        "peer_distributions": peers,
        "segments": {
            "gp": {
                "type_factor_model": {"schema_version": "fund_type_factors.v1"},
                "horizons": {
                    "20": {
                        "qualified": {"medium_momentum": True},
                        "factors": [
                            {
                                "factor": "medium_momentum",
                                "factor_family": "fund_type_specific",
                                "mean_ic": 0.05,
                                "economic_significance": economic,
                            }
                        ],
                    }
                },
            }
        },
    }


def test_qualified_type_factor_is_used_by_online_scoring() -> None:
    panel = {
        f"{index:06d}": [
            NavPoint(f"2025-{1 + day // 28:02d}-{1 + day % 28:02d}", value)
            for day, value in enumerate(_navs(index))
        ]
        for index in range(30)
    }
    classifications = {code: "gp" for code in panel}
    peers = build_peer_distributions(
        nav_panel=panel,
        classifications=classifications,
        factor_lookback=250,
    )
    target = factor_input_from_navs("999999", "目标", _navs(20))

    [result] = score_targets_with_research_model(
        targets=[target],
        model=_qualified_model(peers),
    )

    assert result["typed_factor_applicable"] is True
    assert result["typed_factor_candidates"] == ["medium_momentum"]
    assert result["typed_factor_percentiles"]["medium_momentum"] is not None
    assert result["typed_factor_reliability"]["medium_momentum"]["qualified"] is True
    assert result["typed_factor_reliability"]["medium_momentum"]["level"] == "中"
    assert "NAV修订时点未冻结" in result["typed_factor_reliability"]["medium_momentum"]["basis"]
    assert result["typed_factor_score"] is not None


def test_type_factor_is_high_only_after_nav_observation_pit() -> None:
    panel = {
        f"{index:06d}": [
            NavPoint(f"2025-{1 + day // 28:02d}-{1 + day % 28:02d}", value)
            for day, value in enumerate(_navs(index))
        ]
        for index in range(30)
    }
    peers = build_peer_distributions(
        nav_panel=panel,
        classifications={code: "gp" for code in panel},
        factor_lookback=250,
    )
    model = _qualified_model(peers)
    model["point_in_time"] = {
        "point_in_time_scope": "nav_observation_pit",
        "nav_revision_pit": True,
    }
    target = factor_input_from_navs("999999", "目标", _navs(20))

    [result] = score_targets_with_research_model(targets=[target], model=model)

    assert result["typed_factor_reliability"]["medium_momentum"]["level"] == "高"


def test_type_factor_fails_closed_when_target_nav_feature_is_missing() -> None:
    peers = {
        "gp": {
            "eligible_count": 30,
            "factors": {},
            "type_factors": {
                "medium_momentum": {"mean": 0.1, "std": 0.05, "z_values": [0.0, 1.0]},
            },
        }
    }
    target = factor_input_from_navs("999999", "目标", [1.0, 1.01])

    [result] = score_targets_with_research_model(
        targets=[target],
        model=_qualified_model(peers),
    )

    assert result["typed_factor_applicable"] is False
    assert result["typed_factor_score"] is None
    assert result["typed_factor_reliability"]["medium_momentum"]["level"] == "不足"


def test_current_survivor_model_never_uses_type_factor_even_if_flagged() -> None:
    peers = {
        "gp": {
            "eligible_count": 30,
            "factors": {},
            "type_factors": {
                "medium_momentum": {"mean": 0.1, "std": 0.05, "z_values": [0.0, 1.0]},
            },
        }
    }
    model = _qualified_model(peers)
    model["version"] = "factor_ic.v2"
    model["cohort_mode"] = "current_survivors"
    target = factor_input_from_navs("999999", "目标", _navs(20))

    [result] = score_targets_with_research_model(targets=[target], model=model)

    assert result["typed_factor_candidates"] == []
    assert result["typed_factor_applicable"] is False
