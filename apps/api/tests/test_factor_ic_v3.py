from __future__ import annotations

from copy import deepcopy
import pytest

from app.services.factor_ic_snapshot import FactorIcPublishRequest


def _v3_payload() -> dict:
    factors = [
        {
            "factor": factor,
            "n_periods": 34,
            "mean_ic": 0.04,
            "ic_std": 0.1,
            "icir": 0.4,
            "t_stat": 2.5,
            "positive_ratio": 0.65,
            "significant": True,
        }
        for factor in ("momentum", "risk_adjusted", "drawdown", "composite")
    ]
    qualified = {
        "factor": "momentum",
        "n_periods": 40,
        "mean_ic": 0.04,
        "icir": 0.3,
        "ci_low": 0.005,
        "q_value": 0.05,
        "qualified": True,
        "walk_forward": {
            "fold_count": 5,
            "valid_fold_count": 5,
            "embargo_trading_days": 20,
            "oos_mean_ic": 0.03,
            "same_direction_folds": 4,
        },
        "factor_family": "common",
        "economic_significance": {
            "schema_version": "factor_economic_significance.v1",
            "label_type": "peer_group_relative_total_return",
            "benchmark": "same_segment_cross_section_median",
            "point_in_time_scope": "membership_only",
            "nav_revision_pit": False,
            "entry_rule": "next_trading_day_first_available_nav",
            "entry_offset_trading_days": 1,
            "quantile_count": 5,
            "period_count": 40,
            "valid_observation_count": 800,
            "peer_relative_coverage_rate": 0.95,
            "top_quantile_relative_return": 0.018,
            "bottom_quantile_relative_return": -0.008,
            "top_bottom_spread": 0.026,
            "hac_lags": 1,
            "standard_error": 0.004,
            "t_stat": 6.5,
            "ci_low": 0.018,
            "ci_high": 0.034,
            "top_net_positive_ratio": 0.65,
            "top_net_positive_cost_rate": 0.005,
            "quintile_mean_relative_returns": [-0.008, -0.003, 0.0, 0.007, 0.018],
            "quintile_monotonicity": 1.0,
            "turnover": 0.4,
            "break_even_fee_rate": 0.018,
            "cost_scenarios": [
                {"fee_rate": 0.0, "top_net_relative_return": 0.018, "spread_net_return": 0.026},
                {"fee_rate": 0.005, "top_net_relative_return": 0.013, "spread_net_return": 0.016},
                {"fee_rate": 0.01, "top_net_relative_return": 0.008, "spread_net_return": 0.006},
            ],
            "top_relative_return_p10": -0.002,
            "top_relative_return_worst": -0.01,
            "downside_distribution_unit": "anchor_top_quantile_mean",
            "walk_forward": {
                "method": "expanding_walk_forward_economic_spread",
                "fold_count": 5,
                "valid_fold_count": 5,
                "embargo_trading_days": 20,
                "oos_mean_spread": 0.02,
                "same_direction_folds": 5,
                "folds": [],
            },
            "qualified": True,
        },
    }
    point_in_time = {
        "ready": True,
        "publishable": True,
        "effective_anchor_count": 30,
        "anchor_coverage_rate": 0.95,
        "cohort_nav_coverage_rate": 0.95,
        "future_snapshot_violations": 0,
        "max_snapshot_age_days": 7,
        "walk_forward_folds": 5,
        "embargo_trading_days": 20,
        "multiple_testing": "benjamini_hochberg",
        "fdr_q_threshold": 0.10,
        "point_in_time_scope": "membership_only",
        "nav_revision_pit": False,
        "nav_publication_lag_trading_days": {"default": 1, "qdii": 2},
        "execution_entry_offset_trading_days": 1,
        "mature_anchor_count_by_horizon": {"5": 30, "20": 28, "60": 24},
        "mature_anchor_coverage_rate_by_horizon": {
            "5": 1.0,
            "20": 0.9333,
            "60": 0.8,
        },
        "horizon_ready": {"5": True, "20": True, "60": True},
        "primary_maturity_horizon": 20,
    }
    return {
        "summary": {
            "schema_version": 3,
            "run_date": "2026-07-13",
            "generated_at": "2026-07-13T08:00:00+00:00",
            "params": {
                "universe_size": 1500,
                "universe_mode": "stratified",
                "sample_pool_size": 25000,
                "nav_days": 1500,
                "rebalance_step": 10,
                "forward_days": 20,
                "factor_lookback": 250,
                "forward_horizons": [5, 20, 60],
                "pit_history_days": 1600,
                "pit_max_snapshot_age_days": 7,
                "pit_walk_forward_folds": 5,
                "pit_embargo_trading_days": 20,
            },
            "available": True,
            "universe_size": 1500,
            "rebalance_count": 30,
            "forward_days": 20,
            "factors": factors,
            "coverage": {
                "source_share_classes": 19000,
                "unique_portfolios": 8000,
                "effective_nav_portfolios": 1500,
                "total_return_preferred_rate": 0.95,
            },
            "research_model": {
                "version": "factor_ic.v3",
                "cohort_mode": "point_in_time",
                "primary_horizon": 20,
                "point_in_time": point_in_time,
                "pit_coverage": {
                    key: value
                    for key, value in point_in_time.items()
                    if key not in {
                        "walk_forward_folds", "embargo_trading_days", "multiple_testing"
                    }
                },
                "validation": {
                    "method": "expanding_walk_forward",
                    "folds": 5,
                    "embargo_trading_days": 20,
                    "multiple_test": "benjamini_hochberg",
                    "fdr_q_threshold": 0.10,
                },
                "economic_significance": {
                    "schema_version": "factor_economic_significance.v1",
                    "label_type": "peer_group_relative_total_return",
                    "benchmark": "same_segment_cross_section_median",
                    "point_in_time_scope": "membership_only",
                    "nav_revision_pit": False,
                    "entry_rule": "next_trading_day_first_available_nav",
                    "entry_offset_trading_days": 1,
                    "quantiles": 5,
                    "cost_rates": [0.0, 0.005, 0.01],
                    "qualification_cost_rate": 0.005,
                    "minimum_periods": 36,
                    "minimum_coverage_rate": 0.80,
                    "minimum_top_net_positive_ratio": 0.55,
                },
                "segments": {
                    key: {
                        "type_factor_model": {
                            "schema_version": "fund_type_factors.v1",
                            "candidate_factors": [],
                            "orientation": "higher_is_better",
                            "tracking_evidence": {
                                "status": "insufficient" if key == "zs" else "not_applicable",
                                "reason": "fixture" if key == "zs" else None,
                            },
                            "size_role": "capacity_risk_guard_only",
                            "nav_information_lag_trading_days": 1,
                            "nav_revision_pit": False,
                        },
                        "horizons": {
                            "20": {
                                "maturity": {
                                    "mature_anchor_count": 28,
                                    "mature_anchor_coverage_rate": 0.9333,
                                    "ready": True,
                                },
                                "qualified": {"momentum": True},
                                "factors": [deepcopy(qualified)],
                            }
                        }
                    }
                    for key in ("gp", "hh", "zq", "zs")
                },
                "peer_distributions": {
                    key: {"eligible_count": 30}
                    for key in ("gp", "hh", "zq", "zs")
                },
                "fund_classifications": {
                    f"{index:06d}": "gp" for index in range(5000)
                },
            },
        },
        "source_commit": "a" * 40,
        "source_run_id": "v3-test",
    }


def test_v3_publish_contract_accepts_strict_point_in_time_evidence() -> None:
    request = FactorIcPublishRequest.model_validate(_v3_payload())
    assert request.summary.schema_version == 3


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("point_in_time", "effective_anchor_count"), 23, "有效锚点不足"),
        (("point_in_time", "anchor_coverage_rate"), 0.899, "锚点覆盖率不足"),
        (("point_in_time", "cohort_nav_coverage_rate"), 0.899, "净值覆盖率不足"),
        (("point_in_time", "future_snapshot_violations"), 1, "未来快照穿越"),
        (("qualified", "n_periods"), 29, "严格门槛"),
        (("qualified", "icir"), 0.19, "严格门槛"),
        (("qualified", "ci_low"), 0.0, "严格门槛"),
        (("qualified", "q_value"), 0.101, "严格门槛"),
        (("walk_forward", "oos_mean_ic"), 0.019, "严格门槛"),
        (("walk_forward", "same_direction_folds"), 3, "严格门槛"),
    ],
)
def test_v3_publish_contract_rejects_false_qualification(
    path: tuple[str, str], value: object, message: str
) -> None:
    payload = _v3_payload()
    model = payload["summary"]["research_model"]
    if path[0] == "point_in_time":
        model["point_in_time"][path[1]] = value
    else:
        for segment in model["segments"].values():
            row = segment["horizons"]["20"]["factors"][0]
            target = row["walk_forward"] if path[0] == "walk_forward" else row
            target[path[1]] = value
    with pytest.raises(ValueError, match=message):
        FactorIcPublishRequest.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("period_count", 35),
        ("peer_relative_coverage_rate", 0.79),
        ("ci_low", 0.0),
        ("top_net_positive_ratio", 0.54),
        ("quintile_monotonicity", 0.49),
        ("break_even_fee_rate", 0.0),
    ],
)
def test_v3_publish_contract_rejects_false_economic_qualification(
    field: str,
    value: object,
) -> None:
    payload = _v3_payload()
    for segment in payload["summary"]["research_model"]["segments"].values():
        segment["horizons"]["20"]["factors"][0]["economic_significance"][field] = value

    with pytest.raises(ValueError, match="严格门槛"):
        FactorIcPublishRequest.model_validate(payload)


def test_v3_publish_contract_rejects_missing_nav_pit_boundary_metadata() -> None:
    payload = _v3_payload()
    model = payload["summary"]["research_model"]
    model["point_in_time"].pop("nav_publication_lag_trading_days")

    with pytest.raises(ValueError, match="NAV 发布滞后"):
        FactorIcPublishRequest.model_validate(payload)


def test_runner_marks_insufficient_pit_history_and_downgrades_to_v2(tmp_path) -> None:
    from app.services.factor_ic_backtest import NavPoint
    from scripts.run_factor_ic import build_ic_report

    from datetime import date, timedelta

    start = date(2025, 1, 1)
    calendar = [(start + timedelta(days=index)).isoformat() for index in range(400)]

    def fetch_rank(_limit: int) -> list[dict]:
        return [
            {
                "fund_code": f"{index:06d}",
                "fund_name": f"基金{index}",
                "fund_type": "gp",
                "return_1y_percent": 100 - index,
            }
            for index in range(24)
        ]

    def fetch_nav(code: str, _name: str, _days: int) -> list[NavPoint]:
        slope = 0.0001 * (int(code) + 1)
        return [NavPoint(day, (1 + slope) ** index) for index, day in enumerate(calendar)]

    summary = build_ic_report(
        fetch_rank=fetch_rank,
        fetch_nav=fetch_nav,
        out_dir=str(tmp_path),
        universe_mode="stratified",
        universe_size=24,
        sample_pool_size=5000,
        nav_days=400,
        universe_snapshots=[
            {
                "snapshot_id": "only-one",
                "snapshot_date": calendar[250],
                "available_at": f"{calendar[250]}T00:00:00+00:00",
                "members": [
                    {"fund_code": f"{index:06d}", "fund_type": "gp"}
                    for index in range(24)
                ],
            }
        ],
    )

    assert summary["schema_version"] == 2
    assert summary["research_model"]["version"] == "factor_ic.v2"
    assert summary["pit_upgrade"]["state"] == "collecting"
    assert summary["pit_upgrade"]["reason"] == "v3_quality_gate_not_met"
