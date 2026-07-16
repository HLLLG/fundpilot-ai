"""Append-only persistence for discovery mainline research snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from app.services.decision_repository import (
    ImmutableRecordConflict,
    canonical_hash,
    list_decision_quality_input_artifacts,
    put_decision_quality_input_artifact,
)
from app.services.mainline_regime import MAINLINE_SNAPSHOT_SCHEMA_VERSION


MAINLINE_SNAPSHOT_ARTIFACT_TYPE = "mainline_daily_snapshot"
MAINLINE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION = (
    "decision_quality_mainline_snapshot_artifact.v1"
)


def persist_discovery_mainline_snapshot(
    *,
    user_id: int,
    report: Mapping[str, Any],
    store_authority: str,
    report_recorded_at: str | datetime,
    connection: Any,
) -> dict[str, Any] | None:
    facts = report.get("discovery_facts")
    facts_map = facts if isinstance(facts, Mapping) else {}
    snapshot = facts_map.get("mainline_snapshot")
    if not isinstance(snapshot, Mapping) or not snapshot:
        return None
    if snapshot.get("schema_version") != MAINLINE_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("mainline snapshot schema_version is unsupported")
    if snapshot.get("automatic_promotion_allowed") is not False:
        raise ValueError("mainline snapshot must disable automatic promotion")
    if snapshot.get("execution_gate_changed") is not False:
        raise ValueError("mainline snapshot must not change execution gates")

    report_id = str(report.get("id") or "").strip()
    if not report_id:
        raise ValueError("mainline snapshot source report id is required")
    decision_at = _aware_text(snapshot.get("decision_at"), "snapshot.decision_at")
    captured_at = _aware_text(snapshot.get("captured_at"), "snapshot.captured_at")
    recorded_at = _aware_text(report_recorded_at, "report_recorded_at")
    if _as_datetime(decision_at) > _as_datetime(captured_at):
        raise ValueError("mainline snapshot cannot be captured before decision_at")
    if _as_datetime(captured_at) > _as_datetime(recorded_at):
        raise ValueError("mainline snapshot cannot be recorded before capture")

    frozen_snapshot = dict(snapshot)
    supplied_hash = str(frozen_snapshot.pop("snapshot_hash", "")).strip()
    expected_hash = canonical_hash(frozen_snapshot)
    if supplied_hash != expected_hash:
        raise ValueError("mainline snapshot_hash mismatch")
    wrapper = {
        "schema_version": MAINLINE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION,
        "source_report_id": report_id,
        "decision_at": decision_at,
        "captured_at": captured_at,
        "recorded_at": recorded_at,
        "snapshot_hash": supplied_hash,
        "snapshot": dict(snapshot),
        "evaluation_mode": "shadow_research_only",
        "automatic_promotion_allowed": False,
    }
    existing = _existing_for_report(
        user_id=user_id,
        report_id=report_id,
        connection=connection,
    )
    if existing is not None:
        existing_wrapper = _artifact_payload(existing)
        if existing_wrapper.get("snapshot_hash") != supplied_hash:
            raise ImmutableRecordConflict(
                "mainline snapshot report identity already exists with different evidence"
            )
        return existing

    return put_decision_quality_input_artifact(
        user_id=user_id,
        artifact={
            "artifact_type": MAINLINE_SNAPSHOT_ARTIFACT_TYPE,
            "artifact_schema_version": MAINLINE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION,
            "logical_key": f"mainline_snapshot:{report_id}",
            "source_type": "discovery",
            "source_report_id": report_id,
            "decision_event_id": None,
            "decision_at": decision_at,
            "available_at": captured_at,
            "recorded_at": recorded_at,
            "store_authority": store_authority,
            "audit_eligible": False,
            "artifact": wrapper,
        },
        connection=connection,
    )


def _existing_for_report(
    *,
    user_id: int,
    report_id: str,
    connection: Any,
) -> dict[str, Any] | None:
    rows = list_decision_quality_input_artifacts(
        user_id=user_id,
        artifact_type=MAINLINE_SNAPSHOT_ARTIFACT_TYPE,
        source_type="discovery",
        source_report_id=report_id,
        limit=2,
        connection=connection,
    )
    return rows[0] if rows else None


def _artifact_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    envelope = row.get("payload")
    if isinstance(envelope, Mapping):
        artifact = envelope.get("artifact")
        if isinstance(artifact, Mapping):
            return artifact
    return {}


def _aware_text(value: object, field: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


__all__ = [
    "MAINLINE_SNAPSHOT_ARTIFACT_SCHEMA_VERSION",
    "MAINLINE_SNAPSHOT_ARTIFACT_TYPE",
    "persist_discovery_mainline_snapshot",
]
