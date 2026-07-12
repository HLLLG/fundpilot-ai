from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.database import _connect
from app.request_context import get_request_user_id
from app.services.decision_repository import (
    ObservationFinalizedConflict,
    upsert_outcome_observation,
)


class OutcomeEvidencePersistenceError(RuntimeError):
    """Raised when computed evidence cannot be durably frozen."""


class OutcomeEvidenceConflict(OutcomeEvidencePersistenceError):
    """Raised when a terminal observation disagrees with newly fetched data."""


_RETRYABLE_DISCOVERY_REASONS = {
    "nav_history_unavailable",
    "baseline_nav_unavailable",
    "target_nav_unavailable",
    "benchmark_component_fetch_failed",
}


def persist_daily_outcome_result(
    report: Mapping[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    eligible_events = _eligible_v2_event_horizons(report)
    for item in result.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        event = item.get("decision_event")
        event_id = str(event.get("event_id") or "") if isinstance(event, Mapping) else ""
        if event_id not in eligible_events:
            continue
        horizons = item.get("by_horizon")
        if not isinstance(horizons, Mapping):
            continue
        for horizon_result in horizons.values():
            if not isinstance(horizon_result, Mapping):
                continue
            observation = horizon_result.get("outcome_observation")
            if (
                isinstance(observation, Mapping)
                and _observation_horizon(observation) in eligible_events[event_id]
            ):
                observations.append(_normalize_observation(observation))
    return _persist(report, result, observations)


def persist_discovery_outcome_result(
    report: Mapping[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    eligible_events = _eligible_v2_event_horizons(report)
    observations = [
        _normalize_observation(observation)
        for observation in result.get("outcome_observations") or []
        if isinstance(observation, Mapping)
        and str(observation.get("event_id") or "") in eligible_events
        and _observation_horizon(observation)
        in eligible_events[str(observation.get("event_id") or "")]
    ]
    return _persist(report, result, observations)


def _persist(
    report: Mapping[str, Any],
    result: dict[str, Any],
    observations: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    rows = list(observations)
    contract = report.get("decision_contract")
    if not isinstance(contract, Mapping) or contract.get("persistence") != "persisted":
        result["outcome_evidence"] = {
            "status": "legacy_dynamic_not_persisted",
            "attempted_count": 0,
            "persisted_count": 0,
            "terminal_count": 0,
            "reason": "report_has_no_persisted_decision_event_v2",
        }
        return result
    if not rows:
        result["outcome_evidence"] = {
            "status": "nothing_to_persist",
            "attempted_count": 0,
            "persisted_count": 0,
            "terminal_count": 0,
            "reason": "no_metric_eligible_v2_observation",
        }
        return result

    user_id = get_request_user_id()
    persisted: list[dict[str, Any]] = []
    try:
        with _connect() as connection:
            for observation in rows:
                try:
                    saved = upsert_outcome_observation(
                        user_id=user_id,
                        observation=observation,
                        connection=connection,
                    )
                except ObservationFinalizedConflict as exc:
                    raise OutcomeEvidenceConflict(
                        "official outcome evidence differs from the already frozen terminal observation"
                    ) from exc
                persisted.append(saved)
    except OutcomeEvidenceConflict:
        raise
    except Exception as exc:  # noqa: BLE001 - translate storage failures at API boundary
        raise OutcomeEvidencePersistenceError(
            "outcome evidence could not be durably persisted"
        ) from exc

    result["outcome_evidence"] = {
        "status": "persisted",
        "attempted_count": len(rows),
        "persisted_count": len(persisted),
        "terminal_count": sum(1 for row in persisted if bool(row.get("is_terminal"))),
        "max_revision_no": max(
            (int(row.get("revision_no") or 0) for row in persisted),
            default=0,
        ),
        "store_authority": contract.get("store_authority"),
        "audit_eligible": contract.get("audit_eligible") is True,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    return result


def _eligible_v2_event_horizons(
    report: Mapping[str, Any],
) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for event in report.get("decision_events") or []:
        if not isinstance(event, Mapping):
            continue
        event_id = str(event.get("event_id") or "")
        if (
            event_id
            and str(event.get("schema_version") or "") == "decision_event.v2"
            and event.get("eligible") is True
        ):
            horizons: set[int] = set()
            for value in event.get("horizons") or []:
                try:
                    parsed = int(value)
                except (TypeError, ValueError):
                    continue
                if parsed > 0:
                    horizons.add(parsed)
            result[event_id] = horizons
    return result


def _observation_horizon(observation: Mapping[str, Any]) -> int:
    try:
        return int(observation.get("horizon_trading_days") or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(observation)
    status = str(normalized.get("status") or "").strip().lower()
    skip_reason = str(normalized.get("skip_reason") or "").strip()
    if status == "skipped" and skip_reason in _RETRYABLE_DISCOVERY_REASONS:
        normalized["source_status"] = "skipped"
        normalized["status"] = "data_unavailable"
        normalized["is_terminal"] = False
    elif status in {"pending", "immature", "data_unavailable", "unavailable", "retryable"}:
        normalized["is_terminal"] = False
    elif status in {"mature", "hit", "miss"}:
        normalized["is_terminal"] = True
    # Non-actionable observations are not passed to this function.  Keeping no
    # synthetic recorded_at/observed_at here makes identical retries hash-stable;
    # the repository records the actual database observation time separately.
    return normalized


__all__ = [
    "OutcomeEvidenceConflict",
    "OutcomeEvidencePersistenceError",
    "persist_daily_outcome_result",
    "persist_discovery_outcome_result",
]
