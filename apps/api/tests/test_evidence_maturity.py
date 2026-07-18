from __future__ import annotations

from datetime import datetime, timezone

from app import main
from app.services import evidence_maturity


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


def _healthy_worker(**_kwargs) -> dict:
    return {
        "healthy": True,
        "reason": "ok",
        "heartbeat_at": "2026-07-18T07:59:55+00:00",
        "age_seconds": 5.0,
        "started_at": "2026-07-18T06:00:00+00:00",
        "worker_id": "must-not-leak",
        "jobs": [
            {"name": "market-shared-refresh", "persistent": True, "alive": True}
        ],
    }


def _factor_status(**_kwargs) -> dict:
    return {
        "available": True,
        "stale": False,
        "confidence_eligible": True,
        "run_date": "2026-07-18",
        "age_days": 0,
        "schema_version": 3,
        "source": "database",
        "universe_size": 1500,
        "cohort_mode": "point_in_time",
        "point_in_time": {
            "effective_anchor_count": 4,
            "publishable": False,
            "point_in_time_scope": "membership_only",
            "nav_revision_pit": False,
            "primary_maturity_horizon": "20",
            "mature_anchor_count_by_horizon": {"20": 0, "60": 0},
        },
        "confidence_block_reasons": [],
    }


def _universe_history(**_kwargs) -> dict:
    return {
        "snapshots": [
            {
                "snapshot_date": "2026-07-14",
                "available_at": "2026-07-14T12:00:00+00:00",
                "sampled_fund_count": 1500,
                "fund_type_count": 6,
            },
            {
                "snapshot_date": "2026-07-18",
                "available_at": "2026-07-18T07:00:00+00:00",
                "sampled_fund_count": 1500,
                "fund_type_count": 6,
            },
        ]
    }


def _quality_snapshot(*, user_id: int) -> dict:
    assert user_id == 7
    return {
        "evaluation_as_of": "2026-07-18T00:00:00+00:00",
        "readiness": {
            "status": "insufficient_data",
            "mature_decision_day_count": 0,
            "formal_label_coverage_percent": None,
            "minimum_shadow_mature_decision_days": 20,
            "minimum_manual_review_mature_decision_days": 60,
            "minimum_manual_review_label_coverage_percent": 80,
        },
        "input_counts": {"decision_event_count": 4},
        "automatic_promotion_allowed": False,
    }


def _nav_observation_status() -> dict:
    return {
        "status": "collecting",
        "observation_count": 1498,
        "fund_count": 1498,
        "capture_run_count": 1,
        "revision_count": 0,
        "first_observed_at": "2026-07-18T07:30:00+00:00",
        "latest_observed_at": "2026-07-18T07:30:00+00:00",
        "latest_nav_date": "2026-07-17",
        "latest_capture_fund_count": 1498,
        "availability_basis": "collector_first_observed_at",
        "revision_policy": "first_observed_value",
        "minimum_feature_history_points": 250,
        "full_model_ready": False,
        "automatic_promotion_allowed": False,
    }


def _patch_sources(monkeypatch) -> None:
    monkeypatch.setattr(evidence_maturity, "inspect_worker_health", _healthy_worker)
    monkeypatch.setattr(evidence_maturity, "build_factor_ic_status", _factor_status)
    monkeypatch.setattr(
        evidence_maturity,
        "read_factor_ic_universe_history",
        _universe_history,
    )
    monkeypatch.setattr(evidence_maturity, "list_discovery_reports", lambda **_kwargs: [])
    monkeypatch.setattr(
        evidence_maturity,
        "read_latest_decision_quality_snapshot",
        _quality_snapshot,
    )
    monkeypatch.setattr(
        evidence_maturity,
        "read_nav_observation_status",
        _nav_observation_status,
    )


def test_maturity_projection_distinguishes_missing_from_zero(monkeypatch) -> None:
    _patch_sources(monkeypatch)

    result = evidence_maturity.build_evidence_maturity_status(user_id=7, now=NOW)

    assert result["schema_version"] == "evidence_maturity.v1"
    assert result["overall_status"] == "collecting"
    assert result["automatic_promotion_allowed"] is False
    assert result["worker"]["healthy"] is True
    assert "worker_id" not in result["worker"]
    assert result["universe"]["snapshot_count"] == 2
    assert result["universe"]["effective_anchor_count"] == 4
    assert result["universe"]["anchor_progress_percent"] == 16.67
    assert result["factor_ic"]["mature_period_count_20d"] == 0
    assert result["factor_ic"]["economic_progress_percent_20d"] == 0.0
    assert result["nav_observation"]["observation_count"] == 1498
    assert result["nav_observation"]["full_model_ready"] is False
    assert result["decision_score_shadow"]["artifact_count"] == 0
    assert result["decision_score_shadow"]["scored_coverage_percent"] is None
    assert result["decision_quality"]["mature_decision_day_count"] == 0
    assert result["decision_quality"]["formal_label_coverage_percent"] is None
    assert result["milestones"][1]["theoretical_minimum_months"] == 17.5
    assert any(
        alert["code"] == "nav_observation_pit_collecting"
        for alert in result["alerts"]
    )


def test_unhealthy_worker_degrades_without_promoting_other_evidence(monkeypatch) -> None:
    _patch_sources(monkeypatch)
    monkeypatch.setattr(
        evidence_maturity,
        "inspect_worker_health",
        lambda **_kwargs: {"healthy": False, "reason": "heartbeat_missing"},
    )

    result = evidence_maturity.build_evidence_maturity_status(user_id=7, now=NOW)

    assert result["overall_status"] == "degraded"
    assert result["worker"]["status"] == "unavailable"
    assert result["automatic_promotion_allowed"] is False
    assert result["alerts"][0]["severity"] == "critical"


def test_v2_factor_status_uses_real_pit_upgrade_anchor_count(monkeypatch) -> None:
    _patch_sources(monkeypatch)
    status = _factor_status()
    status["cohort_mode"] = "current_survivors"
    status["point_in_time"] = {}
    status["pit_upgrade"] = {
        "state": "collecting",
        "effective_anchor_count": 3,
    }
    monkeypatch.setattr(
        evidence_maturity,
        "build_factor_ic_status",
        lambda **_kwargs: status,
    )

    result = evidence_maturity.build_evidence_maturity_status(user_id=7, now=NOW)

    assert result["universe"]["effective_anchor_count"] == 3
    assert result["universe"]["anchor_progress_percent"] == 12.5
    assert result["factor_ic"]["point_in_time_scope"] == "unavailable"


def test_authenticated_endpoint_is_no_store_and_uses_request_user(
    auth_client,
    monkeypatch,
) -> None:
    captured: list[int] = []
    payload = {
        "schema_version": "evidence_maturity.v1",
        "overall_status": "collecting",
        "automatic_promotion_allowed": False,
    }
    monkeypatch.setattr(
        main,
        "build_evidence_maturity_status",
        lambda *, user_id: captured.append(user_id) or payload,
    )

    response = auth_client.get("/api/diagnostics/evidence-maturity")

    assert response.status_code == 200
    assert captured and captured[0] > 0
    assert response.json() == payload
    assert "no-store" in response.headers["cache-control"]
