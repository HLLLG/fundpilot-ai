from __future__ import annotations

from app.services.decision_contract import build_report_decision_bundle
from app.services.decision_outcome_persistence import (
    OutcomeEvidenceConflict,
    persist_daily_outcome_result,
    persist_discovery_outcome_result,
)
from app.services.decision_repository import (
    list_outcome_observation_revisions,
    put_decision_event,
    upsert_outcome_observation,
)


def _event(
    *,
    eligible: bool = True,
    decision_kind: str = "daily",
    report_id: str = "r1",
) -> dict:
    action = (
        "分批加仓"
        if decision_kind == "daily" and eligible
        else "分批买入"
        if eligible
        else "观察"
    )
    report = {
        "id": report_id,
        "created_at": "2026-07-01T06:00:00+00:00",
        "provider": "deepseek-chat",
        (
            "fund_recommendations"
            if decision_kind == "daily"
            else "recommendations"
        ): [
            {
                "fund_code": "008586",
                "fund_name": "华夏人工智能ETF联接C",
                "action": action,
            }
        ],
        "analysis_facts" if decision_kind == "daily" else "discovery_facts": {
            "data_evidence": {
                "schema_version": "1.0",
                "generated_at": "2026-07-01T06:00:02+00:00",
                "decision_ready": True,
                "items": [
                    {
                        "fact_id": "fund.008586.nav",
                        "source": "official_nav",
                        "source_type": "official",
                        "available_at": "2026-07-01T05:59:00+00:00",
                        "fetched_at": "2026-07-01T06:00:02+00:00",
                        "freshness": "fresh",
                        "confidence": "high",
                        "is_estimate": False,
                    }
                ],
            }
        },
    }
    return build_report_decision_bundle(
        report,
        decision_kind=decision_kind,  # type: ignore[arg-type]
    )["events"][0]


def _report(event: dict | None = None) -> dict:
    return {
        "id": "r1",
        "decision_contract": {
            "persistence": "persisted",
            "store_authority": "primary",
            "audit_eligible": True,
        },
        "decision_events": [event or _event()],
    }


def _initialise(event: dict | None = None) -> None:
    frozen = event or _event()
    put_decision_event(user_id=1, event=frozen)
    upsert_outcome_observation(
        user_id=1,
        observation={
            "schema_version": "outcome_observation.v2",
            "observation_id": f"{frozen['event_id']}:T+5",
            "event_id": frozen["event_id"],
            "horizon_trading_days": 5,
            "target_date": None,
            "status": "pending",
            "mature": False,
            "source": "not_observed",
            "metrics": {},
        },
    )


def _mature_observation(*, target_nav: float = 1.1) -> dict:
    return {
        "schema_version": "outcome_observation.v2",
        "observation_id": "daily:r1:0:008586:T+5",
        "event_id": "daily:r1:0:008586",
        "horizon_trading_days": 5,
        "target_date": "2026-07-08",
        "status": "mature",
        "mature": True,
        "observation_at": None,
        "source": "official_fund_nav",
        "baseline": {"date": "2026-07-01", "nav": 1.0},
        "target": {"date": "2026-07-08", "nav": target_nav},
        "metrics": {"gross_direction": {"eligible": True, "mature": True, "hit": True}},
    }


def test_daily_mature_observation_is_frozen_and_identical_retry_is_idempotent() -> None:
    _initialise()
    result = {
        "items": [
            {
                "decision_event": _event(),
                "by_horizon": {
                    "T+5": {"outcome_observation": _mature_observation()}
                },
            }
        ]
    }

    first = persist_daily_outcome_result(_report(), result)
    second = persist_daily_outcome_result(_report(), result)

    assert first["outcome_evidence"]["status"] == "persisted"
    assert first["outcome_evidence"]["terminal_count"] == 1
    assert second["outcome_evidence"]["max_revision_no"] == 2
    revisions = list_outcome_observation_revisions(
        user_id=1,
        observation_id="daily:r1:0:008586:T+5",
    )
    assert len(revisions) == 2


def test_changed_terminal_nav_is_rejected_instead_of_overwriting_frozen_result() -> None:
    _initialise()
    base = {
        "items": [
            {
                "decision_event": _event(),
                "by_horizon": {
                    "T+5": {"outcome_observation": _mature_observation()}
                },
            }
        ]
    }
    persist_daily_outcome_result(_report(), base)
    changed = {
        "items": [
            {
                "decision_event": _event(),
                "by_horizon": {
                    "T+5": {
                        "outcome_observation": _mature_observation(target_nav=1.2)
                    }
                },
            }
        ]
    }

    try:
        persist_daily_outcome_result(_report(), changed)
    except OutcomeEvidenceConflict:
        pass
    else:  # pragma: no cover
        raise AssertionError("terminal evidence change must be rejected")


def test_discovery_retryable_nav_gap_stays_non_terminal() -> None:
    event = _event(decision_kind="discovery", report_id="d1")
    _initialise(event)
    report = _report(event)
    result = {
        "outcome_observations": [
            {
                "schema_version": "outcome_observation.v2",
                "observation_id": "discovery:d1:0:008586:T+5",
                "event_id": "discovery:d1:0:008586",
                "horizon_trading_days": 5,
                "target_date": None,
                "status": "skipped",
                "skip_reason": "nav_history_unavailable",
                "mature": False,
                "source": "akshare.fund_open_fund_info_em",
                "metrics": {},
            }
        ]
    }

    persisted = persist_discovery_outcome_result(report, result)

    assert persisted["outcome_evidence"]["terminal_count"] == 0
    assert persisted["outcome_evidence"]["max_revision_no"] == 2


def test_non_actionable_and_legacy_observations_are_not_persisted() -> None:
    non_actionable = _event(eligible=False)
    report = _report(non_actionable)
    result = {
        "items": [
            {
                "decision_event": non_actionable,
                "by_horizon": {
                    "T+5": {"outcome_observation": _mature_observation()}
                },
            }
        ]
    }
    ignored = persist_daily_outcome_result(report, result)
    assert ignored["outcome_evidence"]["status"] == "nothing_to_persist"

    legacy = persist_daily_outcome_result(
        {"id": "old", "decision_events": []},
        {"items": []},
    )
    assert legacy["outcome_evidence"]["status"] == "legacy_dynamic_not_persisted"
