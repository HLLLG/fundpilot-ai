"""Storage-owned activation boundary for the formal D2 replay contract.

The rollout marker is deliberately separate from user/event payloads.  Its
``required_from`` timestamp is created once by the schema migration and the
entire marker is content-addressed.  Application code must validate the marker
before deciding whether a stored event may use the pre-D2 legacy path.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


DECISION_QUALITY_ROLLOUT_SCHEMA_VERSION = "decision_quality_contract_rollout.v1"
DECISION_QUALITY_ROLLOUT_CONTRACT_NAME = "decision_quality_formal_replay"
DECISION_QUALITY_ROLLOUT_CONTRACT_VERSION = "decision_quality_contract.v1"
DECISION_QUALITY_ROLLOUT_HASH_ALGORITHM = "sha256"
DECISION_QUALITY_ROLLOUT_CANONICALIZATION = "json.sort_keys.compact.utf8.v1"

_MARKER_FIELDS = {
    "schema_version",
    "contract_name",
    "contract_version",
    "required_from",
    "created_at",
    "hash_algorithm",
    "canonicalization",
    "marker_hash",
}
_VARIANT_VERSION_FIELDS = (
    "model_version",
    "prompt_version",
    "strategy_version",
    "policy_version",
    "data_version",
    "fee_model_version",
)
_VARIANT_HASH_FIELDS = (
    "model_hash",
    "prompt_hash",
    "prompt_contract_hash",
    "strategy_hash",
    "policy_hash",
    "data_hash",
    "evidence_hash",
    "fee_model_hash",
    "variant_hash",
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _aware_utc_iso(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat()


def build_decision_quality_rollout_marker(
    required_from: str,
) -> dict[str, str]:
    """Build the one canonical marker inserted by the v14 migration."""

    boundary = _aware_utc_iso(required_from, "required_from")
    marker = {
        "schema_version": DECISION_QUALITY_ROLLOUT_SCHEMA_VERSION,
        "contract_name": DECISION_QUALITY_ROLLOUT_CONTRACT_NAME,
        "contract_version": DECISION_QUALITY_ROLLOUT_CONTRACT_VERSION,
        "required_from": boundary,
        # The activation time is also the marker's immutable storage receipt.
        "created_at": boundary,
        "hash_algorithm": DECISION_QUALITY_ROLLOUT_HASH_ALGORITHM,
        "canonicalization": DECISION_QUALITY_ROLLOUT_CANONICALIZATION,
    }
    marker["marker_hash"] = _canonical_hash(marker)
    return marker


def normalize_decision_quality_rollout_marker(
    value: Mapping[str, Any],
) -> dict[str, str]:
    """Validate the singleton marker without repairing or defaulting it."""

    if set(value) != _MARKER_FIELDS:
        raise ValueError("decision-quality rollout marker fields are invalid")
    marker = {key: str(value.get(key) or "").strip() for key in _MARKER_FIELDS}
    if marker["schema_version"] != DECISION_QUALITY_ROLLOUT_SCHEMA_VERSION:
        raise ValueError("decision-quality rollout marker schema is invalid")
    if marker["contract_name"] != DECISION_QUALITY_ROLLOUT_CONTRACT_NAME:
        raise ValueError("decision-quality rollout contract name is invalid")
    if marker["contract_version"] != DECISION_QUALITY_ROLLOUT_CONTRACT_VERSION:
        raise ValueError("decision-quality rollout contract version is invalid")
    if marker["hash_algorithm"] != DECISION_QUALITY_ROLLOUT_HASH_ALGORITHM:
        raise ValueError("decision-quality rollout hash algorithm is invalid")
    if marker["canonicalization"] != DECISION_QUALITY_ROLLOUT_CANONICALIZATION:
        raise ValueError("decision-quality rollout canonicalization is invalid")
    marker["required_from"] = _aware_utc_iso(
        marker["required_from"], "required_from"
    )
    marker["created_at"] = _aware_utc_iso(marker["created_at"], "created_at")
    if marker["created_at"] != marker["required_from"]:
        raise ValueError("decision-quality rollout receipt conflicts with boundary")
    supplied_hash = marker["marker_hash"].lower()
    if len(supplied_hash) != 64 or any(
        char not in "0123456789abcdef" for char in supplied_hash
    ):
        raise ValueError("decision-quality rollout marker hash is invalid")
    expected_hash = _canonical_hash(
        {key: item for key, item in marker.items() if key != "marker_hash"}
    )
    if supplied_hash != expected_hash:
        raise ValueError("decision-quality rollout marker hash mismatch")
    marker["marker_hash"] = supplied_hash
    return marker


def post_rollout_decision_event_error(
    event: Mapping[str, Any],
    *,
    expected_store_authority: str = "primary",
) -> str | None:
    """Return why a newly stored event is not a complete formal D2 event."""

    # Kept local so schema bootstrap can import marker helpers without loading
    # the full decision pipeline.
    from app.services.decision_contract import (
        DECISION_EVENT_SCHEMA_VERSION,
        DECISION_QUALITY_CONTRACT_VERSION,
        DECISION_VARIANT_MANIFEST_SCHEMA_VERSION,
        decision_replay_bundle_error,
    )

    if event.get("schema_version") != DECISION_EVENT_SCHEMA_VERSION:
        return "decision_event_schema_invalid"
    if event.get("quality_contract_version") != DECISION_QUALITY_CONTRACT_VERSION:
        return "decision_event_quality_contract_invalid"
    if event.get("replay_contract_required") is not True:
        return "decision_event_replay_contract_not_required"
    if expected_store_authority == "primary":
        if event.get("store_authority") != "primary":
            return "decision_event_not_primary"
        if event.get("audit_eligible") is not True:
            return "decision_event_audit_ineligible"
        if event.get("metric_eligible") is not True:
            return "decision_event_metric_ineligible"
    elif expected_store_authority == "fallback_non_audited":
        if event.get("store_authority") != "fallback_non_audited":
            return "decision_event_fallback_authority_conflict"
        if event.get("audit_eligible") is not False:
            return "decision_event_fallback_audit_eligibility_conflict"
        if event.get("metric_eligible") is not False:
            return "decision_event_fallback_metric_eligibility_conflict"
    else:
        return "decision_event_store_authority_invalid"
    if event.get("is_backfilled") is True or event.get("backfilled") is True:
        return "decision_event_backfilled"
    decision_kind = event.get("decision_kind")
    if decision_kind not in {"daily", "discovery"}:
        return "decision_event_kind_invalid"
    if event.get("source_type") != decision_kind:
        return "decision_event_source_kind_conflict"
    expected_event_type = (
        "daily_fund_decision"
        if decision_kind == "daily"
        else "fund_discovery_decision"
    )
    if event.get("event_type") != expected_event_type:
        return "decision_event_type_kind_conflict"
    event_id = event.get("event_id")
    if not isinstance(event_id, str) or not event_id.startswith(f"{decision_kind}:"):
        return "decision_event_id_kind_conflict"
    evaluation_class = event.get("evaluation_class")
    actionable_classes = {"bullish", "bearish", "buy"}
    abstention_classes = {"observation", "watch_only", "conditional_wait"}
    if evaluation_class not in actionable_classes | abstention_classes:
        return "decision_event_action_contract_invalid"
    expected_eligible = evaluation_class in actionable_classes
    if event.get("eligible") is not expected_eligible:
        return "decision_event_action_eligibility_conflict"
    if event.get("action_category") != evaluation_class:
        return "decision_event_action_category_conflict"
    final_action = event.get("final_action") or event.get("action")
    if not isinstance(final_action, str) or not final_action.strip():
        return "decision_event_final_action_missing"
    expected_horizons = [1, 5, 20] if decision_kind == "daily" else [5, 20, 60]
    raw_horizons = event.get("horizons")
    if (
        not isinstance(raw_horizons, list)
        or any(isinstance(item, bool) or not isinstance(item, int) for item in raw_horizons)
        or raw_horizons != expected_horizons
    ):
        return "decision_event_horizons_invalid"

    bundle = event.get("replay_bundle")
    bundle_error = decision_replay_bundle_error(bundle)
    if bundle_error is not None:
        return bundle_error
    if not isinstance(bundle, Mapping):
        return "replay_bundle_missing_or_invalid"
    manifest = bundle.get("variant_manifest")
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("schema_version")
        != DECISION_VARIANT_MANIFEST_SCHEMA_VERSION
    ):
        return "replay_bundle_variant_manifest_mismatch"
    if event.get("variant_manifest") != manifest:
        return "replay_variant_manifest_not_bound_to_bundle"
    if event.get("replay_bundle_hash") != bundle.get("bundle_hash"):
        return "replay_bundle_event_hash_mismatch"
    if event.get("replay_refs") != bundle.get("replay_refs"):
        return "replay_refs_not_bound_to_bundle"
    if event.get("decision_kind") != bundle.get("decision_kind"):
        return "replay_bundle_decision_kind_conflict"
    if event.get("decision_at") != bundle.get("decision_at"):
        return "replay_bundle_decision_time_conflict"
    if event.get("recorded_at") != bundle.get("recorded_at"):
        return "replay_bundle_recorded_time_conflict"
    report_id = event.get("source_report_id") or event.get("report_id")
    if report_id is not None and str(report_id) != str(bundle.get("source_report_id")):
        return "replay_bundle_source_report_conflict"
    if event.get("prompt_contract") != bundle.get("prompt_contract_snapshot"):
        return "replay_prompt_contract_not_bound_to_bundle"
    if event.get("fee_policy") != bundle.get("fee_policy_snapshot"):
        return "replay_fee_policy_not_bound_to_bundle"
    for field in (*_VARIANT_VERSION_FIELDS, *_VARIANT_HASH_FIELDS):
        if event.get(field) != manifest.get(field):
            return f"replay_variant_field_not_bound_to_bundle:{field}"
    return None


__all__ = [
    "DECISION_QUALITY_ROLLOUT_CANONICALIZATION",
    "DECISION_QUALITY_ROLLOUT_CONTRACT_NAME",
    "DECISION_QUALITY_ROLLOUT_CONTRACT_VERSION",
    "DECISION_QUALITY_ROLLOUT_HASH_ALGORITHM",
    "DECISION_QUALITY_ROLLOUT_SCHEMA_VERSION",
    "build_decision_quality_rollout_marker",
    "normalize_decision_quality_rollout_marker",
    "post_rollout_decision_event_error",
]
