from app.services.factor_confidence import factor_reliability
from app.services.factor_ic_backtest import NavPoint
from app.services.factor_ic_research import (
    EXECUTION_QUALIFICATION_METHOD,
    POINT_IN_TIME_RESEARCH_MODEL_VERSION,
    RESEARCH_MODEL_VERSION,
    build_peer_distributions,
    score_targets_with_research_model,
)
from app.services.factor_ic_snapshot import FactorIcPublishRequest
from app.services.fund_factor_nav import factor_input_from_navs


def _panel() -> tuple[dict[str, list[NavPoint]], dict[str, str]]:
    calendar = [f"D{index:04d}" for index in range(300)]
    panel = {}
    classifications = {}
    for fund in range(40):
        code = f"{fund:06d}"
        segment = "gp" if fund < 20 else "zq"
        classifications[code] = segment
        slope = 0.0001 * (fund % 20 + 1)
        panel[code] = [
            NavPoint(day, (1.0 + slope) ** offset)
            for offset, day in enumerate(calendar)
        ]
    return panel, classifications


def test_peer_distributions_are_separate_by_fund_type() -> None:
    panel, classifications = _panel()
    peers = build_peer_distributions(
        nav_panel=panel,
        classifications=classifications,
        factor_lookback=250,
    )
    assert set(peers) == {"gp", "zq"}
    assert peers["gp"]["eligible_count"] == 20
    assert peers["zq"]["eligible_count"] == 20
    assert peers["gp"]["factors"]["momentum"]["valid_count"] == 20


def test_target_is_scored_only_against_its_own_peer_group() -> None:
    panel, classifications = _panel()
    peers = build_peer_distributions(
        nav_panel=panel,
        classifications=classifications,
        factor_lookback=250,
    )
    target = factor_input_from_navs(
        "000001",
        "股票基金",
        [point.nav for point in panel["000001"]],
    )
    model = {
        "version": RESEARCH_MODEL_VERSION,
        "fund_classifications": classifications,
        "peer_distributions": peers,
    }
    row = score_targets_with_research_model(targets=[target], model=model)[0]
    assert row["peer_group"] == "gp"
    assert row["peer_count"] == 20
    assert row["feature_count"] == 2
    assert row["applicable"] is True


def test_execution_qualification_requires_statistical_and_economic_gate() -> None:
    panel, classifications = _panel()
    peers = build_peer_distributions(
        nav_panel=panel,
        classifications=classifications,
        factor_lookback=250,
    )
    model = {
        "version": POINT_IN_TIME_RESEARCH_MODEL_VERSION,
        "cohort_mode": "point_in_time",
        "primary_horizon": 20,
        "fund_classifications": classifications,
        "peer_distributions": peers,
        "segments": {
            "gp": {
                "horizons": {
                    "20": {
                        "qualified": {"momentum": True},
                        "factors": [
                            {
                                "factor": "momentum",
                                "economic_significance": {"qualified": True},
                            }
                        ],
                    }
                }
            }
        },
    }
    target = factor_input_from_navs(
        "000001",
        "target",
        [point.nav for point in panel["000001"]],
        feature_freshness="fresh",
    )

    row = score_targets_with_research_model(targets=[target], model=model)[0]

    assert row["descriptive_applicable"] is True
    assert row["execution_qualified"] is True
    assert row["execution_qualified_factor_keys"] == ["momentum"]
    assert row["execution_qualification"] == {
        "status": "qualified",
        "method": EXECUTION_QUALIFICATION_METHOD,
        "primary_horizon_days": "20",
        "reason": None,
    }

    model["segments"]["gp"]["horizons"]["20"]["factors"][0][
        "economic_significance"
    ]["qualified"] = False
    row = score_targets_with_research_model(targets=[target], model=model)[0]

    assert row["descriptive_applicable"] is True
    assert row["execution_qualified"] is False
    assert row["execution_qualified_factor_keys"] == []
    assert row["execution_qualification"]["reason"] == (
        "no_statistically_and_economically_qualified_factor"
    )


def test_unknown_target_fails_closed_instead_of_borrowing_global_ic() -> None:
    panel, classifications = _panel()
    model = {
        "version": RESEARCH_MODEL_VERSION,
        "fund_classifications": classifications,
        "peer_distributions": build_peer_distributions(
            nav_panel=panel,
            classifications=classifications,
            factor_lookback=250,
        ),
    }
    target = factor_input_from_navs("999999", "未知", [1 + i * 0.001 for i in range(300)])
    row = score_targets_with_research_model(targets=[target], model=model)[0]
    assert row["peer_group"] == "unknown"
    assert row["applicable"] is False
    assert row["composite_score"] is None


def test_segment_reliability_respects_negative_oos_direction() -> None:
    model = {
        "primary_horizon": 20,
        "segments": {
            "gp": {
                "label": "主动股票",
                "horizons": {
                    "20": {
                        "qualified": {"momentum": True},
                        "factors": [
                            {
                                "factor": "momentum",
                                "mean_ic": -0.08,
                                "oos_mean_ic": -0.05,
                                "ci_low": -0.12,
                                "direction_stable": True,
                            }
                        ],
                    }
                },
            }
        },
    }
    reliability = factor_reliability({}, research_model=model, segment="gp")
    assert reliability["momentum"]["level"] == "低"
    assert "反向/均值回归" in reliability["momentum"]["basis"]
    assert reliability["size"]["level"] == "不足"


def test_v2_publish_contract_requires_coverage_and_research_model() -> None:
    factors = [
        {
            "factor": factor,
            "n_periods": 30,
            "mean_ic": 0.02,
            "ic_std": 0.1,
            "icir": 0.2,
            "t_stat": 2.1,
            "positive_ratio": 0.6,
            "significant": True,
            "standard_error": 0.009,
            "ci_low": 0.002,
            "ci_high": 0.038,
            "oos_mean_ic": 0.015,
            "oos_positive_ratio": 0.6,
            "direction_stable": True,
        }
        for factor in ("momentum", "risk_adjusted", "drawdown", "composite")
    ]
    payload = {
        "summary": {
            "schema_version": 2,
            "run_date": "2026-07-12",
            "generated_at": "2026-07-12T08:00:00+00:00",
            "params": {
                "universe_size": 1500,
                "universe_mode": "stratified",
                "sample_pool_size": 25000,
                "nav_days": 1500,
                "rebalance_step": 10,
                "forward_days": 20,
                "factor_lookback": 250,
                "forward_horizons": [5, 20, 60],
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
                "total_return_preferred_rate": 1.0,
            },
            "research_model": {
                "version": "factor_ic.v2",
                "cohort_mode": "current_survivors",
                "primary_horizon": 20,
                "segments": {
                    key: {
                        "horizons": {
                            "20": {"qualified": {"momentum": True}}
                        }
                    }
                    for key in ("gp", "hh", "zq", "zs")
                },
                "peer_distributions": {
                    key: {"eligible_count": 30}
                    for key in ("gp", "hh", "zq", "zs")
                },
                "fund_classifications": {f"{index:06d}": "gp" for index in range(5000)},
            },
        },
        "source_commit": "a" * 40,
        "source_run_id": "123",
    }
    request = FactorIcPublishRequest.model_validate(payload)
    assert request.summary.schema_version == 2

    payload["summary"]["coverage"]["effective_nav_portfolios"] = 1199
    try:
        FactorIcPublishRequest.model_validate(payload)
    except ValueError as exc:
        assert "有效总收益序列不足" in str(exc)
    else:
        raise AssertionError("v2 low coverage must be rejected")
