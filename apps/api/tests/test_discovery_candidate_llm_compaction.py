from __future__ import annotations

from app.services.discovery_candidate_llm import (
    slim_candidate_for_llm,
    slim_candidate_pool_for_llm,
)


def test_slim_candidate_pool_preserves_every_candidate_and_order() -> None:
    candidates = [
        {
            "fund_code": str(index).zfill(6),
            "fund_name": f"候选 {index}",
            "sector_label": f"板块 {index % 8}",
        }
        for index in range(1, 29)
    ]

    projected = slim_candidate_pool_for_llm(
        candidates,
        sector_heat=[],
        trade_date=None,
    )

    assert [item["fund_code"] for item in projected] == [
        item["fund_code"] for item in candidates
    ]


def test_peer_projection_preserves_every_metric_and_explicit_state() -> None:
    candidate = {
        "fund_code": "000001",
        "peer_rank": {
            "status": "available",
            "execution_tilt_eligible": False,
            "metrics": {
                "available_metric": {
                    "label": "可用指标",
                    "orientation": "higher_is_better",
                    "role": "performance",
                    "applicable": True,
                    "available": True,
                    "value": 12.3,
                    "percentile": 88.0,
                    "sample_count": 120,
                    "coverage_rate": 0.95,
                    "qualified": True,
                    "qualification_required": True,
                },
                "missing_metric": {
                    "label": "缺失指标",
                    "role": "risk",
                    "applicable": True,
                    "available": False,
                    "qualified": False,
                    "reason": "target_metric_value_missing",
                },
                "not_applicable_metric": {
                    "applicable": False,
                    "available": False,
                    "reason": "metric_not_applicable_to_equity",
                },
            },
        },
    }

    projected = slim_candidate_for_llm(
        candidate,
        sector_change_index={},
        trade_date=None,
    )["peer_research"]
    all_metrics = {
        **projected["metrics"],
        **projected["not_applicable_metrics"],
    }

    assert set(all_metrics) == {
        "available_metric",
        "missing_metric",
        "not_applicable_metric",
    }
    assert all_metrics["available_metric"]["value"] == 12.3
    assert all_metrics["available_metric"]["percentile"] == 88.0
    assert all_metrics["missing_metric"] == {
        "applicable": True,
        "available": False,
        "label": "缺失指标",
        "role": "risk",
        "qualified": False,
        "reason": "target_metric_value_missing",
    }
    assert all_metrics["not_applicable_metric"] == {
        "applicable": False,
        "available": False,
        "reason": "metric_not_applicable_to_equity",
    }


def test_tradeability_projection_preserves_execution_and_fee_contract() -> None:
    candidate = {
        "fund_code": "000001",
        "tradeability": {
            "schema_version": "fund_tradeability.v1",
            "fund_code": "000001",
            "fund_name": "重复名称",
            "data_status": "complete",
            "freshness": "fresh",
            "can_purchase": True,
            "purchase_state": "limited",
            "redemption_state": "open",
            "minimum_purchase_yuan": 10.0,
            "minimum_initial_purchase_yuan": 10.0,
            "daily_purchase_limit_yuan": 5_000.0,
            "daily_purchase_limit_unlimited": False,
            "minimums": {"initial_yuan": 10.0},
            "purchase_limit": {"amount_yuan": 5_000.0},
            "tradeability_gate": {
                "status": "eligible",
                "effective_min_purchase_yuan": 100.0,
                "max_purchase_yuan": 5_000.0,
                "max_purchase_unlimited": False,
                "reason_codes": [],
            },
            "standard_purchase_fee_tiers": [
                {
                    "condition": "小于100万元",
                    "min_amount_yuan": None,
                    "max_amount_yuan": 1_000_000.0,
                    "fee_type": "percent",
                    "fee_percent": 1.5,
                    "source_rate": "standard_undiscounted",
                }
            ],
            "redemption_fee_tiers": [
                {
                    "condition": "小于7天",
                    "min_days": None,
                    "max_days": 7,
                    "fee_percent": 1.5,
                }
            ],
            "instruction": "与系统提示重复的说明",
        },
    }

    projected = slim_candidate_for_llm(
        candidate,
        sector_change_index={},
        trade_date=None,
    )["tradeability"]

    assert projected["freshness"] == "fresh"
    assert projected["purchase_state"] == "limited"
    assert projected["daily_purchase_limit_yuan"] == 5_000.0
    assert projected["tradeability_gate"]["effective_min_purchase_yuan"] == 100.0
    assert projected["tradeability_gate"]["max_purchase_yuan"] == 5_000.0
    assert projected["standard_purchase_fee_tiers"][0]["fee_percent"] == 1.5
    assert projected["standard_purchase_fee_tiers"][0]["max_amount_yuan"] == 1_000_000.0
    assert projected["redemption_fee_tiers"][0]["max_days"] == 7
    assert projected["redemption_fee_tiers"][0]["fee_percent"] == 1.5
    assert "minimums" not in projected
    assert "purchase_limit" not in projected
    assert "fund_name" not in projected
    assert "instruction" not in projected


def test_vehicle_quality_and_current_tracking_error_survive_projection() -> None:
    projected = slim_candidate_for_llm(
        {
            "fund_code": "000001",
            "vehicle_quality_score": 82.5,
            "vehicle_quality_status": "qualified",
            "vehicle_quality_assessment": {
                "schema_version": "fund_vehicle_quality.v1",
                "status": "qualified",
                "score": 82.5,
                "threshold": 70.0,
                "components": {"tracking_quality": 15.0},
                "reasons": ["跟踪误差较低"],
                "penalties": [],
            },
            "benchmark_metrics": {
                "status": "qualified",
                "qualified": True,
                "tracking_metrics": {
                    "applicable": True,
                    "available": True,
                    "tracking_difference_percent": -0.4,
                    "tracking_error_annualized_percent": 1.2,
                },
            },
        },
        sector_change_index={},
        trade_date=None,
    )

    assert projected["vehicle_quality_score"] == 82.5
    assert projected["vehicle_quality_assessment"]["score"] == 82.5
    tracking = projected["benchmark_metrics"]["tracking_metrics"]
    assert tracking["tracking_difference_percent"] == -0.4
    assert tracking["tracking_error_annualized_percent"] == 1.2
