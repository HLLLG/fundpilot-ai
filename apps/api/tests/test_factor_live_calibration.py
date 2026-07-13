from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.decision_repository import put_decision_event, upsert_outcome_observation
from app.services.factor_live_calibration import (
    FactorLiveCalibrationStorageUnavailable,
    build_factor_live_calibration,
    build_factor_live_calibration_status,
)


def _event(index: int, *, decision_date: str, evidence_state: str = "available") -> dict:
    event_id = f"daily:r{index}:0:008586"
    return {
        "schema_version": "decision_event.v2",
        "event_id": event_id,
        "event_type": "daily_fund_decision",
        "source_type": "daily",
        "source_report_id": f"r{index}",
        "decision_at": f"{decision_date}T06:30:00+00:00",
        "decision_date": decision_date,
        "fund_code": "008586",
        "fund_name": "测试基金",
        "final_action": "分批加仓",
        "action_category": "bullish",
        "eligible": True,
        "metric_eligible": True,
        "audit_eligible": True,
        "store_authority": "primary",
        "is_backfilled": False,
        "quant_evidence": {
            "schema_version": "quant_evidence.v1",
            "state": evidence_state,
            "factor_snapshot_id": "snapshot-a",
            "model_version": "factor_ic.v3",
            "schema": 3,
            "cohort_mode": "point_in_time",
            "peer_group": "equity",
            "reliability_bucket": "中",
            "reliability_factor_family": "common",
            "reliability_factor_key": "momentum",
            "reliability_factor_percentile": 80.0,
            "reliability_factor_direction": "positive",
            "applicable": evidence_state == "available",
            "data_as_of": decision_date,
        },
    }


def _observation(
    event: dict,
    *,
    relative_available: bool = True,
    hit: bool = True,
    horizon: int = 20,
) -> dict:
    event_id = event["event_id"]
    gross_excess = {
        "eligible": True,
        "mature": relative_available,
        "value_percent": 1.25 if relative_available else None,
        "hit": hit if relative_available else None,
    }
    return {
        "schema_version": "outcome_observation.v2",
        "observation_id": f"{event_id}:T+{horizon}",
        "event_id": event_id,
        "decision_event_id": event_id,
        "horizon_trading_days": horizon,
        "target_date": "2026-10-01",
        "status": "mature",
        "is_terminal": True,
        "mature": True,
        "metrics": {
            "gross_direction": {
                "eligible": True,
                "mature": True,
                "value_percent": 1.5,
                "hit": hit,
            },
            "gross_excess": gross_excess,
        },
    }


def _sample(
    days: int,
    *,
    relative_days: int | None = None,
    duplicate_each_day: int = 1,
) -> tuple[list[dict], list[dict]]:
    events: list[dict] = []
    observations: list[dict] = []
    start = date(2026, 1, 1)
    relative_days = days if relative_days is None else relative_days
    index = 0
    for day_index in range(days):
        decision_date = (start + timedelta(days=day_index)).isoformat()
        for _ in range(duplicate_each_day):
            event = _event(index, decision_date=decision_date)
            events.append(event)
            observations.append(
                _observation(event, relative_available=day_index < relative_days)
            )
            index += 1
    return events, observations


def test_calibration_maturity_thresholds_and_manual_only_guardrail() -> None:
    nineteen = build_factor_live_calibration(*_sample(19))
    twenty = build_factor_live_calibration(*_sample(20))
    sixty = build_factor_live_calibration(*_sample(60, relative_days=48))

    assert nineteen["state"] == "insufficient"
    assert twenty["state"] == "shadow"
    assert sixty["state"] == "ready_for_manual_review"
    assert sixty["auto_tuning_eligible"] is False
    assert sixty["manual_review_required"] is True
    group = sixty["groups"][0]
    assert group["mature_decision_date_count"] == 60
    assert group["relative_return"]["coverage_percent"] == 80.0
    assert group["direction"]["aggregation"] == "equal_weight_by_decision_date"


def test_low_relative_coverage_fails_closed_after_sixty_dates() -> None:
    result = build_factor_live_calibration(*_sample(60, relative_days=47))

    assert result["state"] == "shadow"
    assert result["groups"][0]["reason"] == "relative_return_coverage_below_80_percent"
    assert result["auto_tuning_eligible"] is False


def test_same_day_recommendations_do_not_inflate_mature_date_count() -> None:
    result = build_factor_live_calibration(*_sample(20, duplicate_each_day=3))

    group = result["groups"][0]
    assert group["terminal_observation_count"] == 60
    assert group["mature_decision_date_count"] == 20
    assert result["state"] == "shadow"


def test_relative_coverage_is_equal_weighted_by_date_and_within_day() -> None:
    events, observations = _sample(60, duplicate_each_day=2)
    for index, observation in enumerate(observations):
        if index % 2 == 0:
            continue
        relative = observation["metrics"]["gross_excess"]
        relative.update({"mature": False, "value_percent": None, "hit": None})

    result = build_factor_live_calibration(events, observations)

    assert result["groups"][0]["relative_return"]["coverage_percent"] == 50.0
    assert result["groups"][0]["state"] == "shadow"


def test_unavailable_or_legacy_evidence_is_excluded() -> None:
    unavailable = _event(1, decision_date="2026-01-01", evidence_state="unavailable")
    legacy = _event(2, decision_date="2026-01-02")
    legacy["schema_version"] = "decision_event.v1"

    result = build_factor_live_calibration(
        [unavailable, legacy],
        [_observation(unavailable), _observation(legacy)],
    )

    assert result["state"] == "insufficient"
    assert result["included_observation_count"] == 0
    assert result["excluded"] == {
        "missing_formal_decision_event": 1,
        "quant_evidence_unavailable_or_inapplicable": 1,
    }


def test_factor_groups_are_specific_and_misaligned_actions_are_excluded() -> None:
    first = _event(1, decision_date="2026-01-01")
    second = _event(2, decision_date="2026-01-02")
    second["quant_evidence"]["reliability_factor_key"] = "drawdown"
    second["quant_evidence"]["reliability_factor_percentile"] = 20.0
    second["quant_evidence"]["reliability_factor_direction"] = "negative"

    result = build_factor_live_calibration(
        [first, second],
        [_observation(first), _observation(second)],
    )

    assert result["group_count"] == 1
    assert result["groups"][0]["factor_key"] == "momentum"
    assert result["groups"][0]["factor_percentile_bucket"] == "q4_60_80"
    assert result["groups"][0]["attribution"] == "factor_conditioned_association_not_causal"
    assert result["excluded"] == {
        "factor_direction_not_aligned_with_final_action": 1
    }


def test_repository_status_reads_only_terminal_persisted_rows() -> None:
    event = _event(1, decision_date="2026-01-01")
    put_decision_event(user_id=1, event=event)
    terminal = _observation(event)
    terminal.pop("is_terminal")
    upsert_outcome_observation(user_id=1, observation=terminal)

    result = build_factor_live_calibration_status(
        user_id=1,
        now=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    assert result["formal_event_count"] == 1
    assert result["terminal_observation_count"] == 1
    assert result["included_observation_count"] == 1
    assert result["generated_at"] == "2026-07-13T00:00:00+00:00"


def test_repository_status_rejects_production_mysql_fallback(monkeypatch) -> None:
    class FallbackConnection:
        dialect = "sqlite"

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(uses_mysql=True),
    )

    with pytest.raises(
        FactorLiveCalibrationStorageUnavailable,
        match="拒绝回落 SQLite",
    ):
        build_factor_live_calibration_status(
            user_id=1,
            connection=FallbackConnection(),
        )
