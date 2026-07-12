from __future__ import annotations

from app.services.benchmark_fee_evaluation import (
    evaluate_decision_metrics,
    evaluate_frozen_benchmark,
    summarize_metrics,
)
from app.services.discovery_outcomes import build_discovery_outcomes
from app.services.discovery_outcomes import build_discovery_recommendation_accuracy
from app.services.recommendation_outcomes import build_recommendation_outcomes


def _component(code: str, weight: float, start: float, end: float) -> dict:
    return {
        "code": code,
        "weight_percent": weight,
        "points": [
            {"date": "2026-01-06", "close": start},
            {"date": "2026-01-08", "close": end},
        ],
    }


def _formal_benchmark(*components: dict) -> dict:
    return {
        "tier": "fund_contract_exact",
        "status": "complete",
        "formal_excess_eligible": True,
        "mapping_id": "benchmark-v1",
        "components": list(components),
    }


def _fee(rate: float = 0.5) -> dict:
    return {
        "status": "available",
        "fee_source": "user_assumption",
        "round_trip_fee_percent": rate,
        "fee_calculation": "initial_principal_haircut",
        "is_actual_cost": False,
        "recurring_fund_expenses": "already_embedded_in_nav",
    }


def test_complete_frozen_composite_contract_benchmark_uses_every_weighted_leg():
    benchmark = _formal_benchmark(
        _component("equity", 80, 100, 110),
        _component("bond", 20, 100, 105),
    )

    result = evaluate_frozen_benchmark(
        benchmark,
        baseline_date="2026-01-06",
        target_date="2026-01-08",
        is_frozen=True,
        fetch_component=None,
    )

    assert result["available"] is True
    assert result["formal_excess_eligible"] is True
    assert result["return_percent"] == 9.0
    assert [row["weight_percent"] for row in result["components"]] == [80.0, 20.0]


def test_missing_composite_leg_is_unavailable_and_never_reweighted():
    missing = {"code": "bond", "weight_percent": 20, "points": []}
    result = evaluate_frozen_benchmark(
        _formal_benchmark(_component("equity", 80, 100, 110), missing),
        baseline_date="2026-01-06",
        target_date="2026-01-08",
        is_frozen=True,
        fetch_component=None,
    )

    assert result["available"] is False
    assert result["return_percent"] is None
    assert result["reason"] == "benchmark_component_data_unavailable"


def test_proxy_and_unfrozen_contract_never_enter_formal_excess():
    component = _component("proxy", 100, 100, 102)
    proxy = evaluate_frozen_benchmark(
        {
            "tier": "category_proxy",
            "status": "complete",
            "components": [component],
        },
        baseline_date="2026-01-06",
        target_date="2026-01-08",
        is_frozen=True,
        fetch_component=None,
    )
    unfrozen = evaluate_frozen_benchmark(
        _formal_benchmark(component),
        baseline_date="2026-01-06",
        target_date="2026-01-08",
        is_frozen=False,
        fetch_component=None,
    )

    assert proxy["available"] is True
    assert proxy["reference_return_percent"] == 2.0
    assert proxy["formal_excess_eligible"] is False
    assert proxy["return_percent"] is None
    assert unfrozen["available"] is False
    assert unfrozen["reason"] == "benchmark_not_frozen_at_decision"


def test_formal_tier_without_explicit_frozen_eligibility_is_unavailable():
    malformed = _formal_benchmark(_component("equity", 100, 100, 102))
    malformed.pop("formal_excess_eligible")

    result = evaluate_frozen_benchmark(
        malformed,
        baseline_date="2026-01-06",
        target_date="2026-01-08",
        is_frozen=True,
        fetch_component=None,
    )

    assert result["available"] is False
    assert result["reason"] == "formal_benchmark_mapping_incomplete"


def test_foreign_qdii_component_without_exact_history_degrades_only_benchmark():
    result = evaluate_frozen_benchmark(
        _formal_benchmark(
            {
                "code": "HSTECH",
                "component_type": "index",
                "weight_percent": 100,
            }
        ),
        baseline_date="2026-01-06",
        target_date="2026-01-08",
        is_frozen=True,
    )

    assert result["available"] is False
    assert result["reason"] == "benchmark_component_data_unavailable"
    metrics = evaluate_decision_metrics(
        gross_return_percent=2.0,
        evaluation_class="buy",
        fee_policy=_fee(0.5),
        benchmark_result=result,
    )
    assert metrics["gross_direction"]["mature"] is True
    assert metrics["positive_net_return"]["value_percent"] == 1.5
    assert metrics["gross_excess"]["mature"] is False


def test_buy_metrics_split_gross_fee_and_excess_without_double_deducting_nav_expenses():
    metrics = evaluate_decision_metrics(
        gross_return_percent=2.0,
        evaluation_class="bullish",
        fee_policy=_fee(0.5),
        benchmark_result={
            "available": True,
            "formal_excess_eligible": True,
            "return_percent": 1.0,
        },
    )

    assert metrics["gross_direction"]["hit"] is True
    assert metrics["positive_net_return"]["value_percent"] == 1.5
    assert metrics["positive_net_return"]["hit"] is True
    assert metrics["positive_net_return"]["metadata"]["rate_percent"] == 0.5
    assert (
        metrics["positive_net_return"]["metadata"]["recurring_fund_expenses"]
        == "already_embedded_in_nav"
    )
    assert metrics["gross_excess"]["value_percent"] == 1.0
    assert metrics["net_excess"]["value_percent"] == 0.5


def test_bearish_action_has_direction_and_excess_but_no_invented_round_trip_metric():
    metrics = evaluate_decision_metrics(
        gross_return_percent=-2.0,
        evaluation_class="bearish",
        fee_policy=_fee(1.5),
        benchmark_result={
            "available": True,
            "formal_excess_eligible": True,
            "return_percent": -0.5,
        },
    )

    assert metrics["gross_direction"]["hit"] is True
    assert metrics["gross_excess"]["value_percent"] == -1.5
    assert metrics["gross_excess"]["hit"] is True
    assert metrics["positive_net_return"]["eligible"] is False
    assert metrics["net_excess"]["eligible"] is False


def test_metric_coverage_keeps_missing_fee_and_benchmark_out_of_hits_not_denominator():
    available = evaluate_decision_metrics(
        gross_return_percent=2.0,
        evaluation_class="buy",
        fee_policy=_fee(0.5),
        benchmark_result={
            "available": True,
            "formal_excess_eligible": True,
            "return_percent": 1.0,
        },
    )
    unavailable = evaluate_decision_metrics(
        gross_return_percent=1.0,
        evaluation_class="buy",
        fee_policy={},
        benchmark_result={"available": False, "reason": "not_frozen"},
    )

    summary = summarize_metrics([available, unavailable])

    assert summary["gross_direction"]["coverage_percent"] == 100.0
    assert summary["positive_net_return"]["eligible_count"] == 2
    assert summary["positive_net_return"]["mature_count"] == 1
    assert summary["positive_net_return"]["coverage_percent"] == 50.0
    assert summary["gross_excess"]["coverage_percent"] == 50.0


def test_daily_qdii_uses_fund_valuation_rows_and_persisted_event_v2_metrics():
    event = {
        "schema_version": "decision_event.v2",
        "event_id": "daily:r1:0:000001",
        "recommendation_index": 0,
        "fund_code": "000001",
        "evaluation_class": "bullish",
        "executable_calendar_date": "2026-01-03",
        "fee_policy": _fee(0.5),
        "benchmark": _formal_benchmark(_component("000300", 100, 100, 101)),
    }
    report = {
        "id": "r1",
        "created_at": "2026-01-02T16:00:00+08:00",
        "fund_recommendations": [
            {"fund_code": "000001", "fund_name": "QDII样本", "action": "分批加仓"}
        ],
        "decision_events": [event],
        "analysis_facts": {
            "session": {
                "calendar_date": "2026-01-02",
                "session_kind": "trading_day_after_close",
            }
        },
    }
    nav = {
        "data": [
            {"date": "2026-01-06", "nav": 1.0},
            {"date": "2026-01-08", "nav": 1.02},
        ]
    }

    result = build_recommendation_outcomes(
        report,
        horizons=(1,),
        fetch_nav=lambda *_args, **_kwargs: nav,
        trade_dates=frozenset({"2026-01-02", "2026-01-05"}),
        fetch_benchmark=None,
    )

    item = result["items"][0]
    outcome = item["by_horizon"]["T+1"]
    assert item["baseline_nav_date"] == "2026-01-06"
    assert outcome["target_nav_date"] == "2026-01-08"
    assert outcome["gross_direction_hit"] is True
    assert outcome["positive_net_return_percent"] == 1.5
    assert outcome["gross_excess_return_percent"] == 1.0
    assert outcome["net_excess_return_percent"] == 0.5
    assert outcome["outcome_observation"]["schema_version"] == "outcome_observation.v2"
    assert result["event_contract"]["persistence"] == "persisted"
    assert result["by_horizon"]["T+1"]["positive_net_return"]["coverage_percent"] == 100.0


def test_discovery_persisted_event_emits_fee_adjusted_and_formal_excess_metrics():
    event = {
        "schema_version": "decision_event.v2",
        "event_id": "discovery:d1:0:110011",
        "recommendation_index": 0,
        "fund_code": "110011",
        "evaluation_class": "buy",
        "executable_calendar_date": "2026-01-05",
        "fee_policy": _fee(0.5),
        "benchmark": _formal_benchmark(_component("000300", 100, 100, 101)),
    }
    report = {
        "id": "d1",
        "created_at": "2026-01-05T10:00:00+08:00",
        "recommendations": [
            {"fund_code": "110011", "fund_name": "样本", "action": "分批买入"}
        ],
        "decision_events": [event],
    }
    nav = {
        "data": [
            {"date": "2026-01-06", "nav": 1.0},
            {"date": "2026-01-08", "nav": 1.02},
        ]
    }

    result = build_discovery_outcomes(
        report,
        days=1,
        fetch_nav=lambda *_args, **_kwargs: nav,
        fetch_benchmark=None,
    )

    item = result["items"][0]
    assert item["positive_net_return_percent"] == 1.5
    assert item["gross_excess_return_percent"] == 1.0
    assert item["net_excess_return_percent"] == 0.5
    assert result["event_contract"]["persistence"] == "persisted"
    assert result["outcome_observations"][0]["schema_version"] == "outcome_observation.v2"
    assert result["metrics"]["net_excess"]["hit_rate_percent"] == 100.0


def test_discovery_accuracy_excludes_legacy_and_counts_only_audited_v2_events():
    formal_event = {
        "schema_version": "decision_event.v2",
        "event_id": "discovery:formal:0:110011",
        "recommendation_index": 0,
        "fund_code": "110011",
        "evaluation_class": "buy",
        "metric_eligible": True,
        "executable_calendar_date": "2026-01-05",
        "fee_policy": _fee(0.5),
        "benchmark": {"tier": "unavailable", "status": "unavailable"},
    }
    formal = {
        "id": "formal",
        "created_at": "2026-01-05T10:00:00+08:00",
        "decision_contract": {"persistence": "persisted", "audit_eligible": True},
        "decision_events": [formal_event],
        "recommendations": [
            {"fund_code": "110011", "fund_name": "正式样本", "action": "分批买入"}
        ],
    }
    legacy = {
        "id": "legacy",
        "created_at": "2026-01-05T10:00:00+08:00",
        "recommendations": [
            {"fund_code": "110012", "fund_name": "旧样本", "action": "分批买入"}
        ],
    }
    nav = {
        "data": [
            {"date": "2026-01-05", "nav": 1.0},
            {"date": "2026-01-06", "nav": 1.01},
        ]
    }

    result = build_discovery_recommendation_accuracy(
        [formal, legacy],
        days=1,
        fetch_nav=lambda *_args, **_kwargs: nav,
        fetch_benchmark=None,
    )

    assert result["formal_v2_report_count"] == 1
    assert result["eligible_count"] == 1
    assert result["mature_count"] == 1
    assert result["legacy_reference"]["eligible_count"] == 1
    assert result["legacy_reference"]["mature_count"] == 1
