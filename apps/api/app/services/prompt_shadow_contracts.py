"""Pure immutable contracts for D5.1 paired prompt-shadow evidence.

This module performs no persistence, configuration, provider, or clock reads.
It normalizes the four inner artifacts, builds the existing decision-quality
outer envelope, and validates the post-commit/no-lookahead time chain.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from app.services.decision_repository import (
    DECISION_QUALITY_ARTIFACT_RECEIPT_POLICY,
    DECISION_QUALITY_ARTIFACT_RECEIPT_SCHEMA_VERSION,
    canonical_hash,
    canonical_json,
    normalize_decision_quality_artifact_receipt,
    normalize_decision_quality_input_artifact,
)
from app.services.provider_call_trace import (
    ProviderCallTraceError,
    normalize_provider_call_trace,
)


PROMPT_GATE_POLICY_SCHEMA_VERSION = "decision_quality_prompt_gate_policy.v2"
PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION = (
    "decision_quality_prompt_shadow_registration.v1"
)
PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION = "decision_quality_prompt_shadow_attempt.v1"
PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION = "decision_quality_prompt_shadow_output.v1"
PROMPT_FINAL_PROJECTION_SCHEMA_VERSION = (
    "decision_quality_prompt_final_projection.v1"
)

PROMPT_GATE_POLICY_ARTIFACT_TYPE = "decision_quality_prompt_gate_policy"
PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE = (
    "decision_quality_prompt_shadow_registration"
)
PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE = "decision_quality_prompt_shadow_attempt"
PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE = "decision_quality_prompt_shadow_output"

PROMPT_SHADOW_CAPTURE_MODE = "live_only_no_backfill"
PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
PROMPT_SHADOW_RECEIPT_MAX_DELAY_SECONDS = 300
PROMPT_SHADOW_CHALLENGER_START_MAX_DELAY_SECONDS = 900

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_ROLES = frozenset({"champion", "challenger"})
_PARSE_STATUSES = frozenset(
    {
        "valid",
        "empty",
        "invalid",
        "truncated",
        "provider_error",
        "http_error",
        "timeout",
        "interrupted_salvaged",
        "oversize",
    }
)
_SAFE_ERROR_CATEGORIES = frozenset(
    {
        "empty_content",
        "invalid_envelope",
        "invalid_json",
        "http_status",
        "connect_timeout",
        "read_timeout",
        "write_timeout",
        "pool_timeout",
        "timeout",
        "stream_interrupted",
        "consumer_cancelled",
        "connection_error",
        "transport_error",
        "provider_output_error",
        "unknown_provider_error",
        "truncated",
        "oversize",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "cookie",
        "cookies",
        "headers",
        "request_headers",
        "proxy_authorization",
        "secret",
        "password",
    }
)
_EXPECTED_SCOPE = {
    "source_type": "discovery",
    "scan_mode": "full_market",
    "analysis_mode": "fast",
    "horizon_trading_days": 20,
    "provider": "deepseek",
    "provider_mode": "live_only",
    "default_prompt_only": True,
    "news_tool_rounds": 0,
    "judge_mode": "rule_only",
}
_EXPECTED_PAIRING_EXACT_FIELDS = [
    "user_payload",
    "model",
    "temperature",
    "max_tokens",
    "response_format",
    "transport",
    "guard_context",
]
_EXPECTED_GATE_THRESHOLDS = {
    "minimum_mature_decision_days": 60,
    "minimum_paired_label_coverage": 0.80,
    "minimum_differing_case_count": 20,
    "minimum_challenger_valid_completion_rate": 0.95,
    "maximum_challenger_timeout_rate": 0.05,
    "maximum_challenger_invalid_rate": 0.02,
    "maximum_integrity_failure_count": 0,
    "maximum_tenant_failure_count": 0,
    "maximum_guard_failure_count": 0,
    "minimum_mean_utility_delta_pp": 0.05,
    "minimum_utility_ci95_lower_pp": -0.10,
    "maximum_mean_drawdown_delta_pp": 0.0,
    "maximum_drawdown_ci95_upper_pp": 0.10,
    "maximum_sanitized_rate_delta": 0.02,
    "maximum_budget_violation_count": 0,
}
_ARTIFACT_RECEIPT_REF_FIELDS = {
    "user_id",
    "artifact_id",
    "artifact_type",
    "artifact_content_hash",
    "receipt_id",
    "receipt_content_hash",
    "source_row_created_at",
    "source_visible_at",
}
_POLICY_REF_FIELDS = _ARTIFACT_RECEIPT_REF_FIELDS | {"policy_id", "policy_hash"}
_REGISTRATION_REF_FIELDS = _ARTIFACT_RECEIPT_REF_FIELDS | {"registration_hash"}
_ATTEMPT_REF_FIELDS = _ARTIFACT_RECEIPT_REF_FIELDS | {"attempt_hash"}


class PromptShadowContractError(ValueError):
    """One prompt-shadow artifact failed its immutable contract."""


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PromptShadowContractError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise PromptShadowContractError(f"{name} field names must be strings")
    return dict(value)


def _exact_fields(
    value: Mapping[str, Any],
    fields: set[str],
    name: str,
    *,
    optional: set[str] | None = None,
) -> None:
    optional_fields = optional or set()
    unknown = set(value) - fields
    missing = (fields - optional_fields) - set(value)
    if unknown:
        raise PromptShadowContractError(
            f"{name} contains unsupported fields: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise PromptShadowContractError(
            f"{name} is missing fields: {', '.join(sorted(missing))}"
        )


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromptShadowContractError(f"{name} is required")
    return value.strip()


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _sha256(value: object, name: str) -> str:
    text = _text(value, name)
    if (
        text != text.lower()
        or len(text) != 64
        or any(character not in "0123456789abcdef" for character in text)
    ):
        raise PromptShadowContractError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _optional_sha256(value: object, name: str) -> str | None:
    return None if value is None else _sha256(value, name)


def _timestamp(value: object, name: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise PromptShadowContractError(f"{name} must be an ISO timestamp") from exc
    else:
        raise PromptShadowContractError(f"{name} must be an ISO timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PromptShadowContractError(f"{name} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _optional_timestamp(value: object, name: str) -> str | None:
    return None if value is None else _timestamp(value, name)


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PromptShadowContractError(f"{name} must be a positive integer")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PromptShadowContractError(f"{name} must be a non-negative integer")
    return value


def _finite_number(value: object, name: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PromptShadowContractError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or (nonnegative and number < 0):
        raise PromptShadowContractError(f"{name} must be a finite number")
    return number


def _false(value: object, name: str = "automatic_promotion_allowed") -> bool:
    if value is not False:
        raise PromptShadowContractError(f"{name} must be false")
    return False


def _freeze_json(value: object, name: str) -> Any:
    try:
        return json.loads(canonical_json(value))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PromptShadowContractError(f"{name} is not canonical JSON") from exc


def _string_list(
    value: object,
    name: str,
    *,
    unique: bool = False,
    sorted_required: bool = False,
) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PromptShadowContractError(f"{name} must be a list")
    result = [_text(item, f"{name} item") for item in value]
    if unique and len(set(result)) != len(result):
        raise PromptShadowContractError(f"{name} must not contain duplicates")
    if sorted_required and result != sorted(result):
        raise PromptShadowContractError(f"{name} must be sorted")
    return result


def _reject_sensitive_material(value: object, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS:
                raise PromptShadowContractError(
                    f"sensitive field is forbidden in prompt-shadow evidence: {path}.{key}"
                )
            _reject_sensitive_material(item, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, item in enumerate(value):
            _reject_sensitive_material(item, f"{path}[{index}]")


def _hashed(
    value: dict[str, Any],
    hash_field: str,
    *,
    require_hash: bool,
) -> dict[str, Any]:
    supplied = value.pop(hash_field, None)
    digest = canonical_hash(value)
    if require_hash and supplied is None:
        raise PromptShadowContractError(f"{hash_field} is required")
    if supplied is not None and _sha256(supplied, hash_field) != digest:
        raise PromptShadowContractError(f"{hash_field} mismatch")
    value[hash_field] = digest
    return value


def _content_id(value: object, name: str, prefix: str) -> str:
    text = _text(value, name)
    if not text.startswith(prefix):
        raise PromptShadowContractError(f"{name} must start with {prefix}")
    _sha256(text[len(prefix) :], f"{name} digest")
    return text


def _user_id(value: object) -> int:
    if isinstance(value, bool):
        raise PromptShadowContractError("user_id must be a positive integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise PromptShadowContractError("user_id must be a positive integer")
    if result <= 0:
        raise PromptShadowContractError("user_id must be a positive integer")
    return result


def normalize_artifact_receipt_ref(
    value: Mapping[str, Any],
    *,
    expected_user_id: int | None = None,
    expected_artifact_type: str | None = None,
) -> dict[str, Any]:
    result = _mapping(value, "artifact receipt ref")
    _exact_fields(result, _ARTIFACT_RECEIPT_REF_FIELDS, "artifact receipt ref")
    user_id = _user_id(result["user_id"])
    if expected_user_id is not None and user_id != _user_id(expected_user_id):
        raise PromptShadowContractError("artifact receipt ref crosses tenant boundary")
    artifact_id = _content_id(result["artifact_id"], "artifact_id", "dqa_")
    artifact_type = _text(result["artifact_type"], "artifact_type")
    if expected_artifact_type is not None and artifact_type != expected_artifact_type:
        raise PromptShadowContractError("artifact receipt ref has the wrong artifact type")
    artifact_hash = _sha256(
        result["artifact_content_hash"], "artifact_content_hash"
    )
    if artifact_id != f"dqa_{artifact_hash}":
        raise PromptShadowContractError("artifact id conflicts with its content hash")
    source_row_created_at = _timestamp(
        result["source_row_created_at"], "source_row_created_at"
    )
    source_visible_at = _timestamp(result["source_visible_at"], "source_visible_at")
    if datetime.fromisoformat(source_visible_at) < datetime.fromisoformat(
        source_row_created_at
    ):
        raise PromptShadowContractError("receipt visibility predates its source row")
    receipt_id = _content_id(result["receipt_id"], "receipt_id", "dqr_")
    receipt_hash = _sha256(result["receipt_content_hash"], "receipt_content_hash")
    try:
        normalized_receipt = normalize_decision_quality_artifact_receipt(
            {
                "schema_version": DECISION_QUALITY_ARTIFACT_RECEIPT_SCHEMA_VERSION,
                "receipt_policy": DECISION_QUALITY_ARTIFACT_RECEIPT_POLICY,
                "user_id": user_id,
                "artifact_id": artifact_id,
                "artifact_type": artifact_type,
                "artifact_content_hash": artifact_hash,
                "source_row_created_at": source_row_created_at,
                "source_visible_at": source_visible_at,
                "store_authority": "primary",
                "receipt_id": receipt_id,
                "content_hash": receipt_hash,
            }
        )
    except ValueError as exc:
        raise PromptShadowContractError(
            "artifact receipt ref content hash is invalid"
        ) from exc
    return {
        "user_id": user_id,
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "artifact_content_hash": artifact_hash,
        "receipt_id": normalized_receipt["receipt_id"],
        "receipt_content_hash": normalized_receipt["content_hash"],
        "source_row_created_at": source_row_created_at,
        "source_visible_at": source_visible_at,
    }


def _policy_ref(value: object, *, expected_user_id: int | None = None) -> dict[str, Any]:
    raw = _mapping(value, "policy_ref")
    _exact_fields(raw, _POLICY_REF_FIELDS, "policy_ref")
    receipt = normalize_artifact_receipt_ref(
        {key: raw[key] for key in _ARTIFACT_RECEIPT_REF_FIELDS},
        expected_user_id=expected_user_id,
        expected_artifact_type=PROMPT_GATE_POLICY_ARTIFACT_TYPE,
    )
    return {
        "policy_id": _text(raw["policy_id"], "policy_ref.policy_id"),
        "policy_hash": _sha256(raw["policy_hash"], "policy_ref.policy_hash"),
        **receipt,
    }


def _registration_ref(
    value: object, *, expected_user_id: int | None = None
) -> dict[str, Any]:
    raw = _mapping(value, "registration_ref")
    _exact_fields(raw, _REGISTRATION_REF_FIELDS, "registration_ref")
    receipt = normalize_artifact_receipt_ref(
        {key: raw[key] for key in _ARTIFACT_RECEIPT_REF_FIELDS},
        expected_user_id=expected_user_id,
        expected_artifact_type=PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE,
    )
    return {
        "registration_hash": _sha256(
            raw["registration_hash"], "registration_ref.registration_hash"
        ),
        **receipt,
    }


def _attempt_ref(value: object, *, expected_user_id: int | None = None) -> dict[str, Any]:
    raw = _mapping(value, "attempt_ref")
    _exact_fields(raw, _ATTEMPT_REF_FIELDS, "attempt_ref")
    receipt = normalize_artifact_receipt_ref(
        {key: raw[key] for key in _ARTIFACT_RECEIPT_REF_FIELDS},
        expected_user_id=expected_user_id,
        expected_artifact_type=PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
    )
    return {
        "attempt_hash": _sha256(raw["attempt_hash"], "attempt_ref.attempt_hash"),
        **receipt,
    }


def _scope(value: object) -> dict[str, Any]:
    raw = _mapping(value, "scope")
    _exact_fields(raw, set(_EXPECTED_SCOPE), "scope")
    normalized = _freeze_json(raw, "scope")
    if normalized != _EXPECTED_SCOPE:
        raise PromptShadowContractError("scope is outside the D5.1 contract")
    return dict(_EXPECTED_SCOPE)


def _prompt_spec(value: object, name: str) -> dict[str, Any]:
    raw = _mapping(value, name)
    _exact_fields(raw, {"template_version", "template_snapshot", "template_hash"}, name)
    snapshot = _text(raw["template_snapshot"], f"{name}.template_snapshot")
    digest = canonical_hash(snapshot)
    if _sha256(raw["template_hash"], f"{name}.template_hash") != digest:
        raise PromptShadowContractError(f"{name}.template_hash mismatch")
    return {
        "template_version": _text(raw["template_version"], f"{name}.template_version"),
        "template_snapshot": snapshot,
        "template_hash": digest,
    }


def _pairing_policy(value: object) -> dict[str, Any]:
    fields = {
        "exact_match_fields",
        "allowed_difference_fields",
        "max_registration_source_lag_seconds",
        "max_receipt_delay_seconds",
        "max_challenger_start_delay_seconds",
        "max_raw_response_bytes",
        "attempts_per_role",
        "capture_mode",
    }
    raw = _mapping(value, "pairing")
    _exact_fields(raw, fields, "pairing")
    exact = _string_list(raw["exact_match_fields"], "pairing.exact_match_fields", unique=True)
    allowed = _string_list(
        raw["allowed_difference_fields"],
        "pairing.allowed_difference_fields",
        unique=True,
    )
    normalized = {
        "exact_match_fields": exact,
        "allowed_difference_fields": allowed,
        "max_registration_source_lag_seconds": _positive_int(
            raw["max_registration_source_lag_seconds"],
            "pairing.max_registration_source_lag_seconds",
        ),
        "max_receipt_delay_seconds": _positive_int(
            raw["max_receipt_delay_seconds"], "pairing.max_receipt_delay_seconds"
        ),
        "max_challenger_start_delay_seconds": _positive_int(
            raw["max_challenger_start_delay_seconds"],
            "pairing.max_challenger_start_delay_seconds",
        ),
        "max_raw_response_bytes": _positive_int(
            raw["max_raw_response_bytes"], "pairing.max_raw_response_bytes"
        ),
        "attempts_per_role": _positive_int(
            raw["attempts_per_role"], "pairing.attempts_per_role"
        ),
        "capture_mode": _text(raw["capture_mode"], "pairing.capture_mode"),
    }
    expected = {
        "exact_match_fields": _EXPECTED_PAIRING_EXACT_FIELDS,
        "allowed_difference_fields": ["effective_system_prompt"],
        "max_registration_source_lag_seconds": 300,
        "max_receipt_delay_seconds": 300,
        "max_challenger_start_delay_seconds": 900,
        "max_raw_response_bytes": PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES,
        "attempts_per_role": 1,
        "capture_mode": PROMPT_SHADOW_CAPTURE_MODE,
    }
    if normalized != expected:
        raise PromptShadowContractError("pairing policy conflicts with D5.1")
    return normalized


def _assignment_policy(value: object) -> dict[str, Any]:
    raw = _mapping(value, "assignment")
    _exact_fields(raw, {"algorithm", "key_id", "sample_basis_points"}, "assignment")
    sample = _positive_int(raw["sample_basis_points"], "assignment.sample_basis_points")
    if sample > 10_000:
        raise PromptShadowContractError("sample_basis_points must not exceed 10000")
    algorithm = _text(raw["algorithm"], "assignment.algorithm")
    if algorithm != "hmac_sha256_mod_10000.v1":
        raise PromptShadowContractError("assignment algorithm is unsupported")
    return {
        "algorithm": algorithm,
        "key_id": _text(raw["key_id"], "assignment.key_id"),
        "sample_basis_points": sample,
    }


def _budget_policy(value: object) -> dict[str, Any]:
    raw = _mapping(value, "budget")
    fields = {
        "timezone",
        "scope_key",
        "max_challenger_calls_per_day",
        "reservation_policy",
    }
    _exact_fields(raw, fields, "budget")
    result = {
        "timezone": _text(raw["timezone"], "budget.timezone"),
        "scope_key": _text(raw["scope_key"], "budget.scope_key"),
        "max_challenger_calls_per_day": _positive_int(
            raw["max_challenger_calls_per_day"],
            "budget.max_challenger_calls_per_day",
        ),
        "reservation_policy": _text(
            raw["reservation_policy"], "budget.reservation_policy"
        ),
    }
    if (
        result["timezone"] != "Asia/Shanghai"
        or result["scope_key"] != "global"
        or result["reservation_policy"] != "consume_never_release.v1"
    ):
        raise PromptShadowContractError("budget policy conflicts with D5.1")
    return result


def _gate_thresholds(value: object) -> dict[str, Any]:
    raw = _mapping(value, "gate_thresholds")
    _exact_fields(raw, set(_EXPECTED_GATE_THRESHOLDS), "gate_thresholds")
    normalized = _freeze_json(raw, "gate_thresholds")
    if normalized != _EXPECTED_GATE_THRESHOLDS:
        raise PromptShadowContractError("gate thresholds conflict with preregistration")
    return dict(_EXPECTED_GATE_THRESHOLDS)


def _statistics(value: object) -> dict[str, Any]:
    fields = {
        "cluster_key",
        "aggregation",
        "bootstrap_iterations",
        "permutation_iterations",
        "confidence_level",
        "seed_derivation",
    }
    raw = _mapping(value, "statistics")
    _exact_fields(raw, fields, "statistics")
    result = {
        "cluster_key": _text(raw["cluster_key"], "statistics.cluster_key"),
        "aggregation": _text(raw["aggregation"], "statistics.aggregation"),
        "bootstrap_iterations": _positive_int(
            raw["bootstrap_iterations"], "statistics.bootstrap_iterations"
        ),
        "permutation_iterations": _positive_int(
            raw["permutation_iterations"], "statistics.permutation_iterations"
        ),
        "confidence_level": _finite_number(
            raw["confidence_level"], "statistics.confidence_level"
        ),
        "seed_derivation": _text(
            raw["seed_derivation"], "statistics.seed_derivation"
        ),
    }
    expected = {
        "cluster_key": "live_cohort_date_local",
        "aggregation": "equal_weighted_day_means",
        "bootstrap_iterations": 10_000,
        "permutation_iterations": 10_000,
        "confidence_level": 0.95,
        "seed_derivation": (
            "sha256(prompt-paired-gate-v2|policy_hash|stratum_hash)"
        ),
    }
    if result != expected:
        raise PromptShadowContractError("statistics policy conflicts with D5.1")
    return result


_POLICY_FIELDS = {
    "schema_version",
    "policy_id",
    "registered_at",
    "effective_from",
    "effective_until",
    "scope",
    "champion_prompt",
    "challenger_prompt",
    "pairing",
    "assignment",
    "budget",
    "gate_thresholds",
    "statistics",
    "automatic_promotion_allowed",
    "policy_hash",
}


def _normalize_policy(value: Mapping[str, Any], *, require_hash: bool) -> dict[str, Any]:
    raw = _mapping(value, "prompt gate policy")
    _exact_fields(
        raw,
        _POLICY_FIELDS,
        "prompt gate policy",
        optional=set() if require_hash else {"policy_hash"},
    )
    schema = _text(raw["schema_version"], "schema_version")
    if schema != PROMPT_GATE_POLICY_SCHEMA_VERSION:
        raise PromptShadowContractError("prompt gate policy schema is unsupported")
    registered_at = _timestamp(raw["registered_at"], "registered_at")
    effective_from = _timestamp(raw["effective_from"], "effective_from")
    effective_until = _optional_timestamp(raw["effective_until"], "effective_until")
    if datetime.fromisoformat(effective_from) < datetime.fromisoformat(registered_at):
        raise PromptShadowContractError("policy effective_from predates registration")
    if effective_until is not None and datetime.fromisoformat(
        effective_until
    ) <= datetime.fromisoformat(effective_from):
        raise PromptShadowContractError("policy effective_until is not after effective_from")
    result = {
        "schema_version": schema,
        "policy_id": _text(raw["policy_id"], "policy_id"),
        "registered_at": registered_at,
        "effective_from": effective_from,
        "effective_until": effective_until,
        "scope": _scope(raw["scope"]),
        "champion_prompt": _prompt_spec(raw["champion_prompt"], "champion_prompt"),
        "challenger_prompt": _prompt_spec(
            raw["challenger_prompt"], "challenger_prompt"
        ),
        "pairing": _pairing_policy(raw["pairing"]),
        "assignment": _assignment_policy(raw["assignment"]),
        "budget": _budget_policy(raw["budget"]),
        "gate_thresholds": _gate_thresholds(raw["gate_thresholds"]),
        "statistics": _statistics(raw["statistics"]),
        "automatic_promotion_allowed": _false(raw["automatic_promotion_allowed"]),
    }
    if (
        result["champion_prompt"]["template_hash"]
        == result["challenger_prompt"]["template_hash"]
    ):
        raise PromptShadowContractError("champion and challenger prompts must differ")
    _reject_sensitive_material(result)
    if "policy_hash" in raw:
        result["policy_hash"] = raw["policy_hash"]
    return _hashed(result, "policy_hash", require_hash=require_hash)


def build_prompt_gate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_policy(value, require_hash=False)


def normalize_prompt_gate_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_policy(value, require_hash=True)


def _assignment(value: object, policy: Mapping[str, Any] | None) -> dict[str, Any]:
    fields = {
        "algorithm",
        "key_id",
        "assignment_input_hash",
        "hmac_digest",
        "modulus",
        "bucket",
        "threshold",
        "included",
    }
    raw = _mapping(value, "assignment")
    _exact_fields(raw, fields, "assignment")
    modulus = _positive_int(raw["modulus"], "assignment.modulus")
    bucket = _nonnegative_int(raw["bucket"], "assignment.bucket")
    threshold = _positive_int(raw["threshold"], "assignment.threshold")
    if modulus != 10_000 or bucket >= modulus or threshold > modulus:
        raise PromptShadowContractError("assignment bucket contract is invalid")
    if raw["included"] is not True or bucket >= threshold:
        raise PromptShadowContractError("registration must be assigned before outputs")
    result = {
        "algorithm": _text(raw["algorithm"], "assignment.algorithm"),
        "key_id": _text(raw["key_id"], "assignment.key_id"),
        "assignment_input_hash": _sha256(
            raw["assignment_input_hash"], "assignment.assignment_input_hash"
        ),
        "hmac_digest": _sha256(raw["hmac_digest"], "assignment.hmac_digest"),
        "modulus": modulus,
        "bucket": bucket,
        "threshold": threshold,
        "included": True,
    }
    if result["algorithm"] != "hmac_sha256_mod_10000.v1":
        raise PromptShadowContractError("assignment algorithm is unsupported")
    if policy is not None:
        configured = policy["assignment"]
        if (
            result["algorithm"] != configured["algorithm"]
            or result["key_id"] != configured["key_id"]
            or result["threshold"] != configured["sample_basis_points"]
        ):
            raise PromptShadowContractError("assignment conflicts with gate policy")
    return result


def _messages(value: object, name: str) -> list[dict[str, str]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PromptShadowContractError(f"{name} must be a list")
    rows = [_mapping(item, f"{name} item") for item in value]
    if len(rows) != 2:
        raise PromptShadowContractError(f"{name} must contain system and user messages")
    result: list[dict[str, str]] = []
    for index, (row, role) in enumerate(zip(rows, ("system", "user"), strict=True)):
        _exact_fields(row, {"role", "content"}, f"{name}[{index}]")
        if row["role"] != role:
            raise PromptShadowContractError(f"{name}[{index}] role is invalid")
        result.append({"role": role, "content": _text(row["content"], f"{name}.content")})
    return result


def _provider_payload(value: object, *, transport: str, name: str) -> dict[str, Any]:
    raw = _mapping(value, name)
    fields = {"model", "messages", "temperature", "max_tokens", "response_format"}
    if transport == "stream":
        fields.add("stream")
    _exact_fields(raw, fields, name)
    if transport == "stream" and raw["stream"] is not True:
        raise PromptShadowContractError(f"{name}.stream must be true")
    messages = _messages(raw["messages"], f"{name}.messages")
    temperature = _finite_number(raw["temperature"], f"{name}.temperature")
    max_tokens = _positive_int(raw["max_tokens"], f"{name}.max_tokens")
    response_format = _mapping(raw["response_format"], f"{name}.response_format")
    if response_format != {"type": "json_object"}:
        raise PromptShadowContractError(f"{name}.response_format is unsupported")
    result: dict[str, Any] = {
        "model": _text(raw["model"], f"{name}.model"),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if transport == "stream":
        result["stream"] = True
    return result


def _prompt_pair(value: object) -> dict[str, Any]:
    fields = {
        "user_payload",
        "user_payload_hash",
        "champion_messages",
        "champion_messages_hash",
        "challenger_messages",
        "challenger_messages_hash",
        "champion_provider_payload",
        "champion_provider_payload_hash",
        "challenger_provider_payload",
        "challenger_provider_payload_hash",
        "transport",
    }
    raw = _mapping(value, "prompt_pair")
    _exact_fields(raw, fields, "prompt_pair")
    transport = _text(raw["transport"], "prompt_pair.transport")
    if transport not in {"sync", "stream"}:
        raise PromptShadowContractError("prompt_pair transport is unsupported")
    user_payload = _freeze_json(_mapping(raw["user_payload"], "user_payload"), "user_payload")
    champion_messages = _messages(raw["champion_messages"], "champion_messages")
    challenger_messages = _messages(raw["challenger_messages"], "challenger_messages")
    if champion_messages[1] != challenger_messages[1]:
        raise PromptShadowContractError("prompt pair user messages must be identical")
    if champion_messages[0]["content"] == challenger_messages[0]["content"]:
        raise PromptShadowContractError("prompt pair system messages must differ")
    try:
        message_payload = json.loads(champion_messages[1]["content"])
    except json.JSONDecodeError as exc:
        raise PromptShadowContractError("prompt pair user message is not JSON") from exc
    if canonical_hash(message_payload) != canonical_hash(user_payload):
        raise PromptShadowContractError("prompt pair user message conflicts with user_payload")
    if champion_messages[1]["content"] != canonical_json(user_payload):
        raise PromptShadowContractError(
            "prompt pair user message must use canonical JSON"
        )
    champion_provider = _provider_payload(
        raw["champion_provider_payload"],
        transport=transport,
        name="champion_provider_payload",
    )
    challenger_provider = _provider_payload(
        raw["challenger_provider_payload"],
        transport=transport,
        name="challenger_provider_payload",
    )
    if champion_provider["messages"] != champion_messages:
        raise PromptShadowContractError("champion provider messages conflict with prompt pair")
    if challenger_provider["messages"] != challenger_messages:
        raise PromptShadowContractError("challenger provider messages conflict with prompt pair")
    champion_common = dict(champion_provider)
    challenger_common = dict(challenger_provider)
    champion_common.pop("messages")
    challenger_common.pop("messages")
    if champion_common != challenger_common:
        raise PromptShadowContractError(
            "provider payloads may differ only in their system message"
        )
    hashes = {
        "user_payload_hash": canonical_hash(user_payload),
        "champion_messages_hash": canonical_hash(champion_messages),
        "challenger_messages_hash": canonical_hash(challenger_messages),
        "champion_provider_payload_hash": canonical_hash(champion_provider),
        "challenger_provider_payload_hash": canonical_hash(challenger_provider),
    }
    for field, expected in hashes.items():
        if _sha256(raw[field], f"prompt_pair.{field}") != expected:
            raise PromptShadowContractError(f"prompt_pair.{field} mismatch")
    return {
        "user_payload": user_payload,
        "user_payload_hash": hashes["user_payload_hash"],
        "champion_messages": champion_messages,
        "champion_messages_hash": hashes["champion_messages_hash"],
        "challenger_messages": challenger_messages,
        "challenger_messages_hash": hashes["challenger_messages_hash"],
        "champion_provider_payload": champion_provider,
        "champion_provider_payload_hash": hashes["champion_provider_payload_hash"],
        "challenger_provider_payload": challenger_provider,
        "challenger_provider_payload_hash": hashes[
            "challenger_provider_payload_hash"
        ],
        "transport": transport,
    }


_GUARD_CONTEXT_FIELDS = {
    "target_sectors",
    "focus_sectors",
    "scan_mode",
    "candidate_pool",
    "discovery_facts",
    "profile",
    "held_codes",
    "requested_budget_yuan",
    "sector_heat",
    "market_news",
    "topic_briefs",
    "analysis_mode",
    "decision_at",
}


def _guard_context(value: object, decision_at: str) -> dict[str, Any]:
    raw = _mapping(value, "guard_context")
    _exact_fields(raw, _GUARD_CONTEXT_FIELDS, "guard_context")
    result = {
        "target_sectors": _string_list(raw["target_sectors"], "target_sectors", unique=True),
        "focus_sectors": _string_list(raw["focus_sectors"], "focus_sectors", unique=True),
        "scan_mode": _text(raw["scan_mode"], "guard_context.scan_mode"),
        "candidate_pool": _freeze_json(raw["candidate_pool"], "candidate_pool"),
        "discovery_facts": _freeze_json(raw["discovery_facts"], "discovery_facts"),
        "profile": _freeze_json(_mapping(raw["profile"], "profile"), "profile"),
        "held_codes": _string_list(
            raw["held_codes"], "held_codes", unique=True, sorted_required=True
        ),
        "requested_budget_yuan": _finite_number(
            raw["requested_budget_yuan"], "requested_budget_yuan", nonnegative=True
        ),
        "sector_heat": _freeze_json(raw["sector_heat"], "sector_heat"),
        "market_news": _freeze_json(raw["market_news"], "market_news"),
        "topic_briefs": _freeze_json(raw["topic_briefs"], "topic_briefs"),
        "analysis_mode": _text(raw["analysis_mode"], "guard_context.analysis_mode"),
        "decision_at": _timestamp(raw["decision_at"], "guard_context.decision_at"),
    }
    if (
        result["scan_mode"] != "full_market"
        or result["analysis_mode"] != "fast"
        or result["decision_at"] != decision_at
        or result["requested_budget_yuan"] <= 0
    ):
        raise PromptShadowContractError("guard context conflicts with D5.1 scope")
    if not isinstance(result["candidate_pool"], list):
        raise PromptShadowContractError("candidate_pool must be a list")
    if not isinstance(result["sector_heat"], list):
        raise PromptShadowContractError("sector_heat must be a list")
    if not isinstance(result["market_news"], list) or not isinstance(
        result["topic_briefs"], list
    ):
        raise PromptShadowContractError("news and topic briefs must be lists")
    return result


def _versions(value: object) -> dict[str, str]:
    fields = {
        "discovery_prompt_contract_version",
        "discovery_guard_contract_version",
        "discovery_allocator_contract_version",
        "claim_validator_contract_version",
        "candidate_label_policy_version",
    }
    raw = _mapping(value, "versions")
    _exact_fields(raw, fields, "versions")
    return {key: _text(raw[key], f"versions.{key}") for key in sorted(fields)}


def _label_plan(value: object) -> dict[str, Any]:
    fields = {"horizon_trading_days", "utility_basis", "risk_basis", "cash_return_percent"}
    raw = _mapping(value, "label_plan")
    _exact_fields(raw, fields, "label_plan")
    result = {
        "horizon_trading_days": _positive_int(
            raw["horizon_trading_days"], "label_plan.horizon_trading_days"
        ),
        "utility_basis": _text(raw["utility_basis"], "label_plan.utility_basis"),
        "risk_basis": _text(raw["risk_basis"], "label_plan.risk_basis"),
        "cash_return_percent": _finite_number(
            raw["cash_return_percent"], "label_plan.cash_return_percent"
        ),
    }
    expected = {
        "horizon_trading_days": 20,
        "utility_basis": "allocation_weighted_total_return_before_costs",
        "risk_basis": "allocation_weighted_full_path_max_drawdown",
        "cash_return_percent": 0.0,
    }
    if result != expected:
        raise PromptShadowContractError("label plan conflicts with D5.1")
    return result


_REGISTRATION_FIELDS = {
    "schema_version",
    "run_id",
    "policy_ref",
    "decision_at",
    "registered_at",
    "capture_mode",
    "scope",
    "assignment",
    "prompt_pair",
    "guard_context",
    "guard_context_hash",
    "candidate_audit_snapshot_hash",
    "versions",
    "label_plan",
    "automatic_promotion_allowed",
    "registration_hash",
}


def _normalize_registration(
    value: Mapping[str, Any],
    *,
    require_hash: bool,
    policy: Mapping[str, Any] | None,
    expected_user_id: int | None,
) -> dict[str, Any]:
    raw = _mapping(value, "prompt shadow registration")
    _exact_fields(
        raw,
        _REGISTRATION_FIELDS,
        "prompt shadow registration",
        optional=set() if require_hash else {"registration_hash"},
    )
    schema = _text(raw["schema_version"], "schema_version")
    if schema != PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION:
        raise PromptShadowContractError("prompt shadow registration schema is unsupported")
    normalized_policy = normalize_prompt_gate_policy(policy) if policy is not None else None
    policy_ref = _policy_ref(raw["policy_ref"], expected_user_id=expected_user_id)
    if normalized_policy is not None and (
        policy_ref["policy_id"] != normalized_policy["policy_id"]
        or policy_ref["policy_hash"] != normalized_policy["policy_hash"]
    ):
        raise PromptShadowContractError("registration references a different gate policy")
    decision_at = _timestamp(raw["decision_at"], "decision_at")
    registered_at = _timestamp(raw["registered_at"], "registered_at")
    if datetime.fromisoformat(registered_at) < datetime.fromisoformat(decision_at):
        raise PromptShadowContractError("registration predates its decision")
    scope = _scope(raw["scope"])
    if normalized_policy is not None and scope != normalized_policy["scope"]:
        raise PromptShadowContractError("registration scope conflicts with policy")
    prompt_pair = _prompt_pair(raw["prompt_pair"])
    guard_context = _guard_context(raw["guard_context"], decision_at)
    guard_hash = canonical_hash(guard_context)
    if _sha256(raw["guard_context_hash"], "guard_context_hash") != guard_hash:
        raise PromptShadowContractError("guard_context_hash mismatch")
    result = {
        "schema_version": schema,
        "run_id": _content_id(raw["run_id"], "run_id", "dqsr_"),
        "policy_ref": policy_ref,
        "decision_at": decision_at,
        "registered_at": registered_at,
        "capture_mode": _text(raw["capture_mode"], "capture_mode"),
        "scope": scope,
        "assignment": _assignment(raw["assignment"], normalized_policy),
        "prompt_pair": prompt_pair,
        "guard_context": guard_context,
        "guard_context_hash": guard_hash,
        "candidate_audit_snapshot_hash": _sha256(
            raw["candidate_audit_snapshot_hash"], "candidate_audit_snapshot_hash"
        ),
        "versions": _versions(raw["versions"]),
        "label_plan": _label_plan(raw["label_plan"]),
        "automatic_promotion_allowed": _false(raw["automatic_promotion_allowed"]),
    }
    if result["capture_mode"] != PROMPT_SHADOW_CAPTURE_MODE:
        raise PromptShadowContractError("registration is not a live-only capture")
    _reject_sensitive_material(result)
    if "registration_hash" in raw:
        result["registration_hash"] = raw["registration_hash"]
    return _hashed(result, "registration_hash", require_hash=require_hash)


def build_prompt_shadow_registration(
    value: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
    expected_user_id: int | None = None,
) -> dict[str, Any]:
    return _normalize_registration(
        value,
        require_hash=False,
        policy=policy,
        expected_user_id=expected_user_id,
    )


def normalize_prompt_shadow_registration(
    value: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
    expected_user_id: int | None = None,
) -> dict[str, Any]:
    return _normalize_registration(
        value,
        require_hash=True,
        policy=policy,
        expected_user_id=expected_user_id,
    )


def _lease(value: object, role: str) -> dict[str, Any] | None:
    if role == "champion":
        if value is not None:
            raise PromptShadowContractError("champion attempt cannot have a lease")
        return None
    raw = _mapping(value, "lease")
    fields = {"owner_hash", "token_hash", "acquired_at", "expires_at"}
    _exact_fields(raw, fields, "lease")
    acquired = _timestamp(raw["acquired_at"], "lease.acquired_at")
    expires = _timestamp(raw["expires_at"], "lease.expires_at")
    if datetime.fromisoformat(expires) <= datetime.fromisoformat(acquired):
        raise PromptShadowContractError("challenger lease is already expired")
    return {
        "owner_hash": _sha256(raw["owner_hash"], "lease.owner_hash"),
        "token_hash": _sha256(raw["token_hash"], "lease.token_hash"),
        "acquired_at": acquired,
        "expires_at": expires,
    }


def _budget_reservation(
    value: object,
    role: str,
    policy_ref: Mapping[str, Any],
    lease: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if role == "champion":
        if value is not None:
            raise PromptShadowContractError("champion attempt cannot reserve shadow budget")
        return None
    raw = _mapping(value, "budget_reservation")
    fields = {
        "scope_key",
        "budget_date_local",
        "policy_hash",
        "max_calls",
        "reserved_ordinal",
        "reserved_at",
    }
    _exact_fields(raw, fields, "budget_reservation")
    try:
        budget_day = date.fromisoformat(_text(raw["budget_date_local"], "budget_date_local"))
    except ValueError as exc:
        raise PromptShadowContractError("budget_date_local must be an ISO date") from exc
    maximum = _positive_int(raw["max_calls"], "budget_reservation.max_calls")
    ordinal = _positive_int(raw["reserved_ordinal"], "budget_reservation.reserved_ordinal")
    if ordinal > maximum:
        raise PromptShadowContractError("budget reservation exceeds its daily cap")
    reserved_at = _timestamp(raw["reserved_at"], "budget_reservation.reserved_at")
    assert lease is not None
    if not (
        datetime.fromisoformat(lease["acquired_at"])
        <= datetime.fromisoformat(reserved_at)
        <= datetime.fromisoformat(lease["expires_at"])
    ):
        raise PromptShadowContractError("budget reservation falls outside its lease")
    if (
        datetime.fromisoformat(reserved_at).astimezone(ZoneInfo("Asia/Shanghai")).date()
        != budget_day
    ):
        raise PromptShadowContractError(
            "budget date conflicts with Asia/Shanghai reservation time"
        )
    policy_hash = _sha256(raw["policy_hash"], "budget_reservation.policy_hash")
    if policy_hash != policy_ref["policy_hash"]:
        raise PromptShadowContractError("budget reservation references another policy")
    if raw["scope_key"] != "global":
        raise PromptShadowContractError("budget reservation scope is unsupported")
    return {
        "scope_key": "global",
        "budget_date_local": budget_day.isoformat(),
        "policy_hash": policy_hash,
        "max_calls": maximum,
        "reserved_ordinal": ordinal,
        "reserved_at": reserved_at,
    }


def _safe_endpoint(value: object) -> str:
    endpoint = _text(value, "endpoint_base_url")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PromptShadowContractError(
            "endpoint_base_url must be credential-free HTTPS without query or fragment"
        )
    return endpoint.rstrip("/")


_ATTEMPT_FIELDS = {
    "schema_version",
    "run_id",
    "role",
    "attempt_number",
    "decision_at",
    "policy_ref",
    "registration_ref",
    "provider",
    "operation",
    "endpoint_base_url",
    "provider_payload_hash",
    "transport",
    "pre_network_registered_at",
    "lease",
    "budget_reservation",
    "automatic_promotion_allowed",
    "attempt_hash",
}


def _normalize_attempt(
    value: Mapping[str, Any],
    *,
    require_hash: bool,
    registration: Mapping[str, Any] | None,
    expected_user_id: int | None,
) -> dict[str, Any]:
    raw = _mapping(value, "prompt shadow attempt")
    _exact_fields(
        raw,
        _ATTEMPT_FIELDS,
        "prompt shadow attempt",
        optional=set() if require_hash else {"attempt_hash"},
    )
    schema = _text(raw["schema_version"], "schema_version")
    if schema != PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION:
        raise PromptShadowContractError("prompt shadow attempt schema is unsupported")
    normalized_registration = (
        normalize_prompt_shadow_registration(
            registration, expected_user_id=expected_user_id
        )
        if registration is not None
        else None
    )
    role = _text(raw["role"], "role")
    if role not in _ROLES:
        raise PromptShadowContractError("attempt role is unsupported")
    if _positive_int(raw["attempt_number"], "attempt_number") != 1:
        raise PromptShadowContractError("only one provider attempt per role is allowed")
    decision_at = _timestamp(raw["decision_at"], "decision_at")
    run_id = _content_id(raw["run_id"], "run_id", "dqsr_")
    policy_ref = _policy_ref(raw["policy_ref"], expected_user_id=expected_user_id)
    registration_ref = _registration_ref(
        raw["registration_ref"], expected_user_id=expected_user_id
    )
    transport = _text(raw["transport"], "transport")
    if transport not in {"sync", "stream"}:
        raise PromptShadowContractError("attempt transport is unsupported")
    lease = _lease(raw["lease"], role)
    budget = _budget_reservation(raw["budget_reservation"], role, policy_ref, lease)
    registered_at = _timestamp(raw["pre_network_registered_at"], "pre_network_registered_at")
    if datetime.fromisoformat(registered_at) < datetime.fromisoformat(decision_at):
        raise PromptShadowContractError("attempt preregistration predates decision")
    if role == "challenger":
        assert lease is not None and budget is not None
        registered_dt = datetime.fromisoformat(registered_at)
        if not (
            datetime.fromisoformat(budget["reserved_at"])
            <= registered_dt
            <= datetime.fromisoformat(lease["expires_at"])
        ):
            raise PromptShadowContractError(
                "challenger preregistration falls outside its lease reservation"
            )
        if registered_dt.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() != budget[
            "budget_date_local"
        ]:
            raise PromptShadowContractError(
                "challenger budget date conflicts with preregistration time"
            )
    result = {
        "schema_version": schema,
        "run_id": run_id,
        "role": role,
        "attempt_number": 1,
        "decision_at": decision_at,
        "policy_ref": policy_ref,
        "registration_ref": registration_ref,
        "provider": _text(raw["provider"], "provider"),
        "operation": _text(raw["operation"], "operation"),
        "endpoint_base_url": _safe_endpoint(raw["endpoint_base_url"]),
        "provider_payload_hash": _sha256(
            raw["provider_payload_hash"], "provider_payload_hash"
        ),
        "transport": transport,
        "pre_network_registered_at": registered_at,
        "lease": lease,
        "budget_reservation": budget,
        "automatic_promotion_allowed": _false(raw["automatic_promotion_allowed"]),
    }
    if result["provider"] != "deepseek" or result["operation"] != "chat_completions":
        raise PromptShadowContractError("attempt provider operation is unsupported")
    if normalized_registration is not None:
        pair = normalized_registration["prompt_pair"]
        expected_payload_hash = pair[f"{role}_provider_payload_hash"]
        if (
            run_id != normalized_registration["run_id"]
            or decision_at != normalized_registration["decision_at"]
            or policy_ref["policy_id"]
            != normalized_registration["policy_ref"]["policy_id"]
            or policy_ref["policy_hash"]
            != normalized_registration["policy_ref"]["policy_hash"]
            or registration_ref["registration_hash"]
            != normalized_registration["registration_hash"]
            or transport != pair["transport"]
            or result["provider_payload_hash"] != expected_payload_hash
        ):
            raise PromptShadowContractError("attempt conflicts with registration")
        _require_receipt_binding(
            policy_ref,
            normalized_registration["policy_ref"],
            "attempt.policy_ref",
        )
    _reject_sensitive_material(result)
    if "attempt_hash" in raw:
        result["attempt_hash"] = raw["attempt_hash"]
    return _hashed(result, "attempt_hash", require_hash=require_hash)


def build_prompt_shadow_attempt(
    value: Mapping[str, Any],
    *,
    registration: Mapping[str, Any] | None = None,
    expected_user_id: int | None = None,
) -> dict[str, Any]:
    return _normalize_attempt(
        value,
        require_hash=False,
        registration=registration,
        expected_user_id=expected_user_id,
    )


def normalize_prompt_shadow_attempt(
    value: Mapping[str, Any],
    *,
    registration: Mapping[str, Any] | None = None,
    expected_user_id: int | None = None,
) -> dict[str, Any]:
    return _normalize_attempt(
        value,
        require_hash=True,
        registration=registration,
        expected_user_id=expected_user_id,
    )


_PROJECTION_FIELDS = {
    "schema_version",
    "recommendations",
    "allocation_plan",
    "eliminated_candidates",
    "claim_audit",
    "requested_budget_yuan",
    "selected_codes",
    "allocations",
    "unallocated_budget_yuan",
    "guard_reason_codes",
    "versions",
    "automatic_promotion_allowed",
    "projection_hash",
}
_PROJECTION_VERSION_FIELDS = {
    "discovery_guard_contract_version",
    "discovery_allocator_contract_version",
    "claim_validator_contract_version",
}


def _projection_versions(value: object) -> dict[str, str]:
    raw = _mapping(value, "projection versions")
    _exact_fields(raw, _PROJECTION_VERSION_FIELDS, "projection versions")
    return {key: _text(raw[key], key) for key in sorted(_PROJECTION_VERSION_FIELDS)}


def _allocation_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise PromptShadowContractError("allocations must be a list")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        raw = _mapping(item, "allocation")
        _exact_fields(raw, {"fund_code", "suggested_amount_yuan"}, "allocation")
        code = _text(raw["fund_code"], "allocation.fund_code").zfill(6)
        if code in seen:
            raise PromptShadowContractError("allocation fund codes must be unique")
        seen.add(code)
        rows.append(
            {
                "fund_code": code,
                "suggested_amount_yuan": round(
                    _finite_number(
                        raw["suggested_amount_yuan"],
                        "allocation.suggested_amount_yuan",
                        nonnegative=True,
                    ),
                    2,
                ),
            }
        )
    normalized = sorted(rows, key=lambda row: row["fund_code"])
    if rows != normalized:
        raise PromptShadowContractError("allocations must be sorted by fund_code")
    return normalized


def build_decision_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    projection = normalize_prompt_final_projection(value)
    recommendations: list[dict[str, str]] = []
    for item in projection["recommendations"]:
        raw = _mapping(item, "recommendation")
        recommendations.append(
            {
                "fund_code": _text(raw.get("fund_code"), "recommendation.fund_code").zfill(6),
                "action": _text(raw.get("action"), "recommendation.action"),
                "confidence": _text(raw.get("confidence"), "recommendation.confidence"),
            }
        )
    recommendations.sort(key=lambda row: row["fund_code"])
    eliminated_codes = sorted(
        {
            _text(_mapping(item, "eliminated candidate").get("fund_code"), "fund_code").zfill(6)
            for item in projection["eliminated_candidates"]
        }
    )
    return {
        "recommendations": recommendations,
        "allocations": projection["allocations"],
        "eliminated_codes": eliminated_codes,
        "unallocated_budget_yuan": projection["unallocated_budget_yuan"],
    }


def decision_projection_hash(value: Mapping[str, Any]) -> str:
    return canonical_hash(build_decision_projection(value))


def _normalize_projection(
    value: Mapping[str, Any], *, require_hash: bool
) -> dict[str, Any]:
    raw = _mapping(value, "prompt final projection")
    _exact_fields(
        raw,
        _PROJECTION_FIELDS,
        "prompt final projection",
        optional=set() if require_hash else {"projection_hash"},
    )
    schema = _text(raw["schema_version"], "schema_version")
    if schema != PROMPT_FINAL_PROJECTION_SCHEMA_VERSION:
        raise PromptShadowContractError("prompt final projection schema is unsupported")
    recommendations = _freeze_json(raw["recommendations"], "recommendations")
    eliminated = _freeze_json(raw["eliminated_candidates"], "eliminated_candidates")
    if not isinstance(recommendations, list) or not all(
        isinstance(item, Mapping) for item in recommendations
    ):
        raise PromptShadowContractError("recommendations must be a list of objects")
    if not isinstance(eliminated, list) or not all(isinstance(item, Mapping) for item in eliminated):
        raise PromptShadowContractError("eliminated_candidates must be a list of objects")
    selected_codes = _string_list(
        raw["selected_codes"], "selected_codes", unique=True, sorted_required=True
    )
    selected_codes = [code.zfill(6) for code in selected_codes]
    if len(set(selected_codes)) != len(selected_codes) or selected_codes != sorted(
        selected_codes
    ):
        raise PromptShadowContractError(
            "selected_codes must remain unique and sorted after normalization"
        )
    recommendation_code_rows = [
        _text(item.get("fund_code"), "recommendation.fund_code").zfill(6)
        for item in recommendations
    ]
    if len(set(recommendation_code_rows)) != len(recommendation_code_rows):
        raise PromptShadowContractError("recommendation fund codes must be unique")
    recommendation_codes = sorted(recommendation_code_rows)
    if selected_codes != recommendation_codes:
        raise PromptShadowContractError("selected_codes conflict with recommendations")
    allocations = _allocation_rows(raw["allocations"])
    if any(row["fund_code"] not in selected_codes for row in allocations):
        raise PromptShadowContractError("allocation references an unselected fund")
    budget = round(
        _finite_number(raw["requested_budget_yuan"], "requested_budget_yuan", nonnegative=True),
        2,
    )
    if budget <= 0:
        raise PromptShadowContractError("requested_budget_yuan must be positive")
    allocated = round(sum(row["suggested_amount_yuan"] for row in allocations), 2)
    if allocated > budget:
        raise PromptShadowContractError("allocations exceed requested budget")
    unallocated = round(
        _finite_number(raw["unallocated_budget_yuan"], "unallocated_budget_yuan", nonnegative=True),
        2,
    )
    if unallocated != round(budget - allocated, 2):
        raise PromptShadowContractError("unallocated budget conflicts with allocations")
    allocation_plan = _freeze_json(_mapping(raw["allocation_plan"], "allocation_plan"), "allocation_plan")
    plan_allocations = allocation_plan.get("allocations")
    if plan_allocations is not None:
        if not isinstance(plan_allocations, list) or not all(
            isinstance(item, Mapping) for item in plan_allocations
        ):
            raise PromptShadowContractError(
                "allocation_plan.allocations must be a list of objects"
            )
        projected_plan_rows = [
            {
                "fund_code": _text(item.get("fund_code"), "allocation_plan fund_code").zfill(6),
                "suggested_amount_yuan": round(
                    _finite_number(
                        item.get("suggested_amount_yuan"),
                        "allocation_plan amount",
                        nonnegative=True,
                    ),
                    2,
                ),
            }
            for item in plan_allocations
        ]
        if sorted(projected_plan_rows, key=lambda row: row["fund_code"]) != allocations:
            raise PromptShadowContractError("allocation_plan conflicts with projection allocations")
    claim_audit = _freeze_json(_mapping(raw["claim_audit"], "claim_audit"), "claim_audit")
    if claim_audit.get("status") not in {"clean", "sanitized", "violation"}:
        raise PromptShadowContractError("claim_audit status is unsupported")
    result = {
        "schema_version": schema,
        "recommendations": recommendations,
        "allocation_plan": allocation_plan,
        "eliminated_candidates": eliminated,
        "claim_audit": claim_audit,
        "requested_budget_yuan": budget,
        "selected_codes": selected_codes,
        "allocations": allocations,
        "unallocated_budget_yuan": unallocated,
        "guard_reason_codes": _string_list(
            raw["guard_reason_codes"],
            "guard_reason_codes",
            unique=True,
            sorted_required=True,
        ),
        "versions": _projection_versions(raw["versions"]),
        "automatic_promotion_allowed": _false(raw["automatic_promotion_allowed"]),
    }
    _reject_sensitive_material(result)
    if "projection_hash" in raw:
        result["projection_hash"] = raw["projection_hash"]
    return _hashed(result, "projection_hash", require_hash=require_hash)


def build_prompt_final_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_projection(value, require_hash=False)


def normalize_prompt_final_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return _normalize_projection(value, require_hash=True)


def _response(value: object) -> dict[str, Any]:
    fields = {
        "raw_content",
        "raw_content_sha256",
        "raw_content_bytes",
        "parse_status",
        "parsed_payload",
        "parsed_payload_hash",
        "error_category",
    }
    raw = _mapping(value, "response")
    _exact_fields(raw, fields, "response")
    parse_status = _text(raw["parse_status"], "response.parse_status")
    if parse_status not in _PARSE_STATUSES:
        raise PromptShadowContractError("response parse_status is unsupported")
    raw_content = raw["raw_content"]
    raw_bytes = _nonnegative_int(raw["raw_content_bytes"], "raw_content_bytes")
    raw_hash = _sha256(raw["raw_content_sha256"], "raw_content_sha256")
    if raw_content is not None:
        if not isinstance(raw_content, str):
            raise PromptShadowContractError("raw_content must be text or null")
        encoded = raw_content.encode("utf-8")
        if len(encoded) > PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES:
            raise PromptShadowContractError("raw response exceeds the 4 MiB artifact limit")
        if raw_bytes != len(encoded) or raw_hash != hashlib.sha256(encoded).hexdigest():
            raise PromptShadowContractError("raw response hash or byte count mismatch")
        if parse_status == "oversize":
            raise PromptShadowContractError(
                "oversize response must omit raw content from the artifact"
            )
    elif parse_status == "oversize":
        if raw_bytes <= PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES:
            raise PromptShadowContractError("oversize response does not exceed the byte limit")
    elif raw_bytes != 0 or raw_hash != _EMPTY_SHA256:
        raise PromptShadowContractError("missing raw response must use the empty digest")
    parsed_payload = raw["parsed_payload"]
    parsed_hash = raw["parsed_payload_hash"]
    successful_parse = parse_status in {"valid", "interrupted_salvaged"}
    if successful_parse:
        parsed = _freeze_json(_mapping(parsed_payload, "parsed_payload"), "parsed_payload")
        expected_parsed_hash = canonical_hash(parsed)
        if _sha256(parsed_hash, "parsed_payload_hash") != expected_parsed_hash:
            raise PromptShadowContractError("parsed_payload_hash mismatch")
        if raw_content is None:
            raise PromptShadowContractError("successful parsed output requires raw content")
        error_category = None
        if raw["error_category"] is not None:
            raise PromptShadowContractError("successful parsed output cannot have an error")
    else:
        if parsed_payload is not None or parsed_hash is not None:
            raise PromptShadowContractError("failed parsed output cannot retain parsed payload")
        parsed = None
        expected_parsed_hash = None
        error_category = _text(raw["error_category"], "response.error_category")
        if error_category not in _SAFE_ERROR_CATEGORIES:
            raise PromptShadowContractError("response error category is unsupported")
    return {
        "raw_content": raw_content,
        "raw_content_sha256": raw_hash,
        "raw_content_bytes": raw_bytes,
        "parse_status": parse_status,
        "parsed_payload": parsed,
        "parsed_payload_hash": expected_parsed_hash,
        "error_category": error_category,
    }


_OUTPUT_FIELDS = {
    "schema_version",
    "run_id",
    "role",
    "decision_at",
    "policy_ref",
    "registration_ref",
    "attempt_ref",
    "champion_report_id",
    "variant_report_id",
    "candidate_audit_ref",
    "trace",
    "response",
    "final_projection",
    "final_projection_hash",
    "decision_projection_hash",
    "output_materialized_at",
    "automatic_promotion_allowed",
    "output_hash",
}


def _normalize_output(
    value: Mapping[str, Any],
    *,
    require_hash: bool,
    registration: Mapping[str, Any] | None,
    attempt: Mapping[str, Any] | None,
    expected_user_id: int | None,
) -> dict[str, Any]:
    raw = _mapping(value, "prompt shadow output")
    _exact_fields(
        raw,
        _OUTPUT_FIELDS,
        "prompt shadow output",
        optional=set() if require_hash else {"output_hash"},
    )
    schema = _text(raw["schema_version"], "schema_version")
    if schema != PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION:
        raise PromptShadowContractError("prompt shadow output schema is unsupported")
    role = _text(raw["role"], "role")
    if role not in _ROLES:
        raise PromptShadowContractError("output role is unsupported")
    run_id = _content_id(raw["run_id"], "run_id", "dqsr_")
    decision_at = _timestamp(raw["decision_at"], "decision_at")
    policy_ref = _policy_ref(raw["policy_ref"], expected_user_id=expected_user_id)
    registration_ref = _registration_ref(
        raw["registration_ref"], expected_user_id=expected_user_id
    )
    attempt_ref = _attempt_ref(raw["attempt_ref"], expected_user_id=expected_user_id)
    candidate_audit_ref = normalize_artifact_receipt_ref(
        _mapping(raw["candidate_audit_ref"], "candidate_audit_ref"),
        expected_user_id=expected_user_id,
        expected_artifact_type="candidate_selection_audit",
    )
    champion_report_id = _text(raw["champion_report_id"], "champion_report_id")
    variant_report_id = _optional_text(raw["variant_report_id"], "variant_report_id")
    if (role == "champion" and variant_report_id != champion_report_id) or (
        role == "challenger" and variant_report_id is not None
    ):
        raise PromptShadowContractError("variant report identity conflicts with role")
    try:
        trace = normalize_provider_call_trace(_mapping(raw["trace"], "trace"))
    except ProviderCallTraceError as exc:
        raise PromptShadowContractError("provider trace is invalid") from exc
    response = _response(raw["response"])
    if (
        response["raw_content_sha256"] != trace["content_sha256"]
        or response["raw_content_bytes"] != trace["content_bytes"]
    ):
        raise PromptShadowContractError(
            "response content hash or byte count conflicts with provider trace"
        )
    final_projection_raw = raw["final_projection"]
    successful = response["parse_status"] in {"valid", "interrupted_salvaged"}
    if successful:
        projection = normalize_prompt_final_projection(
            _mapping(final_projection_raw, "final_projection")
        )
        projection_hash = projection["projection_hash"]
        if _sha256(raw["final_projection_hash"], "final_projection_hash") != projection_hash:
            raise PromptShadowContractError("final_projection_hash mismatch")
        decision_hash = decision_projection_hash(projection)
        if _sha256(raw["decision_projection_hash"], "decision_projection_hash") != decision_hash:
            raise PromptShadowContractError("decision_projection_hash mismatch")
        if response["parse_status"] == "valid" and trace["outcome"] != "success":
            raise PromptShadowContractError("valid output requires a successful provider trace")
        if response["parse_status"] == "interrupted_salvaged" and not (
            trace["outcome"] == "interrupted" and trace["interrupted_salvaged"] is True
        ):
            raise PromptShadowContractError("salvaged output requires a salvaged trace")
    else:
        if any(
            item is not None
            for item in (
                final_projection_raw,
                raw["final_projection_hash"],
                raw["decision_projection_hash"],
            )
        ):
            raise PromptShadowContractError("failed output cannot contain a final projection")
        projection = None
        projection_hash = None
        decision_hash = None
    materialized_at = _timestamp(raw["output_materialized_at"], "output_materialized_at")
    if datetime.fromisoformat(materialized_at) < datetime.fromisoformat(
        trace["completed_at"]
    ):
        raise PromptShadowContractError("output materialization predates provider completion")
    normalized_registration = (
        normalize_prompt_shadow_registration(
            registration, expected_user_id=expected_user_id
        )
        if registration is not None
        else None
    )
    normalized_attempt = (
        normalize_prompt_shadow_attempt(
            attempt,
            registration=normalized_registration,
            expected_user_id=expected_user_id,
        )
        if attempt is not None
        else None
    )
    if normalized_registration is not None and (
        run_id != normalized_registration["run_id"]
        or decision_at != normalized_registration["decision_at"]
        or registration_ref["registration_hash"]
        != normalized_registration["registration_hash"]
        or policy_ref["policy_hash"]
        != normalized_registration["policy_ref"]["policy_hash"]
    ):
        raise PromptShadowContractError("output conflicts with registration")
    if normalized_attempt is not None and (
        normalized_attempt["role"] != role
        or normalized_attempt["run_id"] != run_id
        or attempt_ref["attempt_hash"] != normalized_attempt["attempt_hash"]
        or trace["request_hash"] != normalized_attempt["provider_payload_hash"]
        or trace["transport"] != normalized_attempt["transport"]
        or trace["provider"] != normalized_attempt["provider"]
        or trace["operation"] != normalized_attempt["operation"]
    ):
        raise PromptShadowContractError("output conflicts with provider attempt")
    if normalized_attempt is not None:
        _require_receipt_binding(
            policy_ref, normalized_attempt["policy_ref"], "output.policy_ref"
        )
        _require_receipt_binding(
            registration_ref,
            normalized_attempt["registration_ref"],
            "output.registration_ref",
        )
    if normalized_registration is not None:
        expected_model = normalized_registration["prompt_pair"][
            f"{role}_provider_payload"
        ]["model"]
        if trace["requested_model"] != expected_model:
            raise PromptShadowContractError(
                "provider trace model conflicts with registered payload"
            )
    result = {
        "schema_version": schema,
        "run_id": run_id,
        "role": role,
        "decision_at": decision_at,
        "policy_ref": policy_ref,
        "registration_ref": registration_ref,
        "attempt_ref": attempt_ref,
        "champion_report_id": champion_report_id,
        "variant_report_id": variant_report_id,
        "candidate_audit_ref": candidate_audit_ref,
        "trace": trace,
        "response": response,
        "final_projection": projection,
        "final_projection_hash": projection_hash,
        "decision_projection_hash": decision_hash,
        "output_materialized_at": materialized_at,
        "automatic_promotion_allowed": _false(raw["automatic_promotion_allowed"]),
    }
    _reject_sensitive_material(result)
    if "output_hash" in raw:
        result["output_hash"] = raw["output_hash"]
    return _hashed(result, "output_hash", require_hash=require_hash)


def build_prompt_shadow_output(
    value: Mapping[str, Any],
    *,
    registration: Mapping[str, Any] | None = None,
    attempt: Mapping[str, Any] | None = None,
    expected_user_id: int | None = None,
) -> dict[str, Any]:
    return _normalize_output(
        value,
        require_hash=False,
        registration=registration,
        attempt=attempt,
        expected_user_id=expected_user_id,
    )


def normalize_prompt_shadow_output(
    value: Mapping[str, Any],
    *,
    registration: Mapping[str, Any] | None = None,
    attempt: Mapping[str, Any] | None = None,
    expected_user_id: int | None = None,
) -> dict[str, Any]:
    return _normalize_output(
        value,
        require_hash=True,
        registration=registration,
        attempt=attempt,
        expected_user_id=expected_user_id,
    )


def _inner_artifact(value: Mapping[str, Any], *, expected_user_id: int) -> dict[str, Any]:
    schema = str(value.get("schema_version") or "") if isinstance(value, Mapping) else ""
    if schema == PROMPT_GATE_POLICY_SCHEMA_VERSION:
        return normalize_prompt_gate_policy(value)
    if schema == PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION:
        return normalize_prompt_shadow_registration(value, expected_user_id=expected_user_id)
    if schema == PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION:
        return normalize_prompt_shadow_attempt(value, expected_user_id=expected_user_id)
    if schema == PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION:
        return normalize_prompt_shadow_output(value, expected_user_id=expected_user_id)
    raise PromptShadowContractError("prompt-shadow inner artifact schema is unsupported")


def build_prompt_shadow_input_artifact(
    *,
    user_id: int,
    artifact: Mapping[str, Any],
    store_authority: str = "primary",
) -> dict[str, Any]:
    """Wrap one normalized inner artifact in the existing immutable envelope."""

    tenant = _user_id(user_id)
    inner = _inner_artifact(artifact, expected_user_id=tenant)
    schema = inner["schema_version"]
    if store_authority != "primary":
        raise PromptShadowContractError("prompt-shadow evidence requires the primary store")
    if schema == PROMPT_GATE_POLICY_SCHEMA_VERSION:
        artifact_type = PROMPT_GATE_POLICY_ARTIFACT_TYPE
        logical_key = f"prompt_shadow_policy:{inner['policy_id']}"
        source_type = "system"
        source_report_id = None
        decision_at = None
        available_at = inner["registered_at"]
        recorded_at = inner["registered_at"]
    elif schema == PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION:
        artifact_type = PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE
        logical_key = f"prompt_shadow_registration:{inner['run_id']}"
        source_type = "discovery"
        source_report_id = None
        decision_at = inner["decision_at"]
        available_at = inner["registered_at"]
        recorded_at = inner["registered_at"]
    elif schema == PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION:
        artifact_type = PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE
        logical_key = f"prompt_shadow_attempt:{inner['run_id']}:{inner['role']}"
        source_type = "discovery"
        source_report_id = None
        decision_at = inner["decision_at"]
        available_at = inner["pre_network_registered_at"]
        recorded_at = inner["pre_network_registered_at"]
    else:
        artifact_type = PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE
        logical_key = f"prompt_shadow_output:{inner['run_id']}:{inner['role']}"
        source_type = "discovery"
        source_report_id = inner["champion_report_id"]
        decision_at = inner["decision_at"]
        available_at = inner["trace"]["completed_at"]
        recorded_at = inner["output_materialized_at"]
    envelope = normalize_decision_quality_input_artifact(
        {
            "artifact_type": artifact_type,
            "artifact_schema_version": schema,
            "logical_key": logical_key,
            "source_type": source_type,
            "source_report_id": source_report_id,
            "decision_event_id": None,
            "decision_at": decision_at,
            "available_at": available_at,
            "recorded_at": recorded_at,
            "store_authority": "primary",
            "audit_eligible": True,
            "artifact": inner,
        }
    )
    # Tenant is deliberately outside the existing v1 envelope hash, but every
    # nested receipt ref has already been checked against this caller-owned id.
    return envelope


def prompt_shadow_nonformal_reason(value: object) -> str | None:
    """Classify legacy/backfill input without mutating or upgrading it."""

    if not isinstance(value, Mapping):
        return "prompt_shadow_artifact_invalid"
    schema = str(value.get("schema_version") or "")
    current = {
        PROMPT_GATE_POLICY_SCHEMA_VERSION,
        PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION,
        PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION,
        PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION,
    }
    if schema not in current:
        return "legacy_prompt_shadow_schema_nonformal"
    capture_mode = value.get("capture_mode")
    if schema == PROMPT_GATE_POLICY_SCHEMA_VERSION and isinstance(
        value.get("pairing"), Mapping
    ):
        capture_mode = value["pairing"].get("capture_mode")
    if capture_mode is not None and capture_mode != PROMPT_SHADOW_CAPTURE_MODE:
        return "prompt_shadow_backfill_nonformal"
    return None


def _receipt_delay_reason(ref: Mapping[str, Any], prefix: str) -> str | None:
    delay = datetime.fromisoformat(ref["source_visible_at"]) - datetime.fromisoformat(
        ref["source_row_created_at"]
    )
    return (
        f"{prefix}_receipt_late"
        if delay.total_seconds() > PROMPT_SHADOW_RECEIPT_MAX_DELAY_SECONDS
        else None
    )


def _require_receipt_binding(
    embedded_ref: Mapping[str, Any],
    receipt_ref: Mapping[str, Any],
    name: str,
) -> None:
    if any(
        embedded_ref.get(field) != receipt_ref.get(field)
        for field in _ARTIFACT_RECEIPT_REF_FIELDS
    ):
        raise PromptShadowContractError(f"{name} receipt binding mismatch")


def validate_prompt_shadow_time_chain(
    *,
    policy: Mapping[str, Any],
    policy_receipt: Mapping[str, Any] | None,
    registration: Mapping[str, Any],
    registration_receipt: Mapping[str, Any] | None,
    champion_attempt: Mapping[str, Any],
    champion_attempt_receipt: Mapping[str, Any] | None,
    champion_output: Mapping[str, Any],
    champion_output_receipt: Mapping[str, Any] | None,
    challenger_attempt: Mapping[str, Any],
    challenger_attempt_receipt: Mapping[str, Any] | None,
    challenger_output: Mapping[str, Any],
    challenger_output_receipt: Mapping[str, Any] | None,
    label_knowledge_boundary: str | datetime,
    evaluation_as_of: str | datetime,
    expected_user_id: int,
) -> dict[str, Any]:
    """Return formal status for complete current artifacts; hashes still fail hard."""

    for item in (policy, registration, champion_attempt, champion_output, challenger_attempt, challenger_output):
        reason = prompt_shadow_nonformal_reason(item)
        if reason is not None:
            return {"formal": False, "reason_codes": [reason]}
    tenant = _user_id(expected_user_id)
    normalized_policy = normalize_prompt_gate_policy(policy)
    normalized_registration = normalize_prompt_shadow_registration(
        registration,
        policy=normalized_policy,
        expected_user_id=tenant,
    )
    normalized_champion_attempt = normalize_prompt_shadow_attempt(
        champion_attempt,
        registration=normalized_registration,
        expected_user_id=tenant,
    )
    normalized_challenger_attempt = normalize_prompt_shadow_attempt(
        challenger_attempt,
        registration=normalized_registration,
        expected_user_id=tenant,
    )
    normalized_champion_output = normalize_prompt_shadow_output(
        champion_output,
        registration=normalized_registration,
        attempt=normalized_champion_attempt,
        expected_user_id=tenant,
    )
    normalized_challenger_output = normalize_prompt_shadow_output(
        challenger_output,
        registration=normalized_registration,
        attempt=normalized_challenger_attempt,
        expected_user_id=tenant,
    )
    challenger_budget = normalized_challenger_attempt["budget_reservation"]
    assert challenger_budget is not None
    if (
        challenger_budget["scope_key"] != normalized_policy["budget"]["scope_key"]
        or challenger_budget["max_calls"]
        != normalized_policy["budget"]["max_challenger_calls_per_day"]
    ):
        raise PromptShadowContractError(
            "challenger budget reservation conflicts with gate policy"
        )
    receipt_inputs = {
        "policy": (policy_receipt, PROMPT_GATE_POLICY_ARTIFACT_TYPE),
        "registration": (
            registration_receipt,
            PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE,
        ),
        "champion_attempt": (
            champion_attempt_receipt,
            PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
        ),
        "champion_output": (
            champion_output_receipt,
            PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE,
        ),
        "challenger_attempt": (
            challenger_attempt_receipt,
            PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
        ),
        "challenger_output": (
            challenger_output_receipt,
            PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE,
        ),
    }
    if any(value is None for value, _artifact_type in receipt_inputs.values()):
        return {"formal": False, "reason_codes": ["prompt_shadow_receipt_missing"]}
    receipts = {
        name: normalize_artifact_receipt_ref(
            _mapping(value, f"{name} receipt"),
            expected_user_id=tenant,
            expected_artifact_type=artifact_type,
        )
        for name, (value, artifact_type) in receipt_inputs.items()
    }
    normalized_artifacts = {
        "policy": normalized_policy,
        "registration": normalized_registration,
        "champion_attempt": normalized_champion_attempt,
        "champion_output": normalized_champion_output,
        "challenger_attempt": normalized_challenger_attempt,
        "challenger_output": normalized_challenger_output,
    }
    for name, artifact in normalized_artifacts.items():
        envelope = build_prompt_shadow_input_artifact(
            user_id=tenant,
            artifact=artifact,
        )
        receipt = receipts[name]
        if (
            receipt["artifact_id"] != envelope["artifact_id"]
            or receipt["artifact_content_hash"] != envelope["content_hash"]
        ):
            raise PromptShadowContractError(
                f"{name} receipt references different artifact content"
            )

    _require_receipt_binding(
        normalized_registration["policy_ref"],
        receipts["policy"],
        "registration.policy_ref",
    )
    for role, attempt in (
        ("champion", normalized_champion_attempt),
        ("challenger", normalized_challenger_attempt),
    ):
        _require_receipt_binding(
            attempt["policy_ref"], receipts["policy"], f"{role}_attempt.policy_ref"
        )
        _require_receipt_binding(
            attempt["registration_ref"],
            receipts["registration"],
            f"{role}_attempt.registration_ref",
        )
    for role, output, attempt_receipt in (
        ("champion", normalized_champion_output, receipts["champion_attempt"]),
        ("challenger", normalized_challenger_output, receipts["challenger_attempt"]),
    ):
        _require_receipt_binding(
            output["policy_ref"], receipts["policy"], f"{role}_output.policy_ref"
        )
        _require_receipt_binding(
            output["registration_ref"],
            receipts["registration"],
            f"{role}_output.registration_ref",
        )
        _require_receipt_binding(
            output["attempt_ref"], attempt_receipt, f"{role}_output.attempt_ref"
        )

    reasons = [
        reason
        for name, ref in receipts.items()
        if (reason := _receipt_delay_reason(ref, name)) is not None
    ]
    registration_source = datetime.fromisoformat(
        receipts["registration"]["source_row_created_at"]
    )
    registration_source_delay = (
        registration_source
        - datetime.fromisoformat(normalized_registration["decision_at"])
    ).total_seconds()
    if registration_source_delay < 0:
        reasons.append("registration_source_predates_decision")
    elif registration_source_delay > PROMPT_SHADOW_RECEIPT_MAX_DELAY_SECONDS:
        reasons.append("registration_source_capture_late")

    boundary = datetime.fromisoformat(_timestamp(label_knowledge_boundary, "label_knowledge_boundary"))
    cutoff = datetime.fromisoformat(_timestamp(evaluation_as_of, "evaluation_as_of"))
    decision = datetime.fromisoformat(normalized_registration["decision_at"])
    policy_effective_from = datetime.fromisoformat(normalized_policy["effective_from"])
    policy_effective_until = (
        datetime.fromisoformat(normalized_policy["effective_until"])
        if normalized_policy["effective_until"] is not None
        else None
    )
    if decision < policy_effective_from or (
        policy_effective_until is not None and decision >= policy_effective_until
    ):
        reasons.append("policy_not_effective_at_decision")
    ordered = [
        datetime.fromisoformat(normalized_policy["registered_at"]),
        datetime.fromisoformat(receipts["policy"]["source_row_created_at"]),
        datetime.fromisoformat(receipts["policy"]["source_visible_at"]),
        decision,
        datetime.fromisoformat(normalized_registration["registered_at"]),
        registration_source,
        datetime.fromisoformat(receipts["registration"]["source_visible_at"]),
        datetime.fromisoformat(normalized_champion_attempt["pre_network_registered_at"]),
        datetime.fromisoformat(receipts["champion_attempt"]["source_row_created_at"]),
        datetime.fromisoformat(receipts["champion_attempt"]["source_visible_at"]),
        datetime.fromisoformat(normalized_champion_output["trace"]["requested_at"]),
        datetime.fromisoformat(normalized_champion_output["trace"]["completed_at"]),
        datetime.fromisoformat(normalized_champion_output["output_materialized_at"]),
        datetime.fromisoformat(receipts["champion_output"]["source_row_created_at"]),
        datetime.fromisoformat(receipts["champion_output"]["source_visible_at"]),
        datetime.fromisoformat(normalized_challenger_attempt["pre_network_registered_at"]),
        datetime.fromisoformat(receipts["challenger_attempt"]["source_row_created_at"]),
        datetime.fromisoformat(receipts["challenger_attempt"]["source_visible_at"]),
        datetime.fromisoformat(normalized_challenger_output["trace"]["requested_at"]),
        datetime.fromisoformat(normalized_challenger_output["trace"]["completed_at"]),
        datetime.fromisoformat(normalized_challenger_output["output_materialized_at"]),
        datetime.fromisoformat(receipts["challenger_output"]["source_row_created_at"]),
        datetime.fromisoformat(receipts["challenger_output"]["source_visible_at"]),
        boundary,
        cutoff,
    ]
    if not all(left <= right for left, right in zip(ordered, ordered[1:])):
        reasons.append("prompt_shadow_time_order_invalid")
    if not (
        datetime.fromisoformat(receipts["policy"]["source_visible_at"])
        < datetime.fromisoformat(normalized_registration["registered_at"])
    ):
        reasons.append("policy_not_preregistered")
    if not (
        datetime.fromisoformat(receipts["champion_output"]["source_visible_at"])
        < boundary
        and datetime.fromisoformat(
            receipts["challenger_output"]["source_visible_at"]
        )
        < boundary
    ):
        reasons.append("prompt_shadow_output_not_prelabel")
    challenger_start_delay = (
        datetime.fromisoformat(normalized_challenger_output["trace"]["requested_at"])
        - datetime.fromisoformat(receipts["champion_output"]["source_visible_at"])
    ).total_seconds()
    if challenger_start_delay < 0:
        reasons.append("challenger_start_predates_champion_receipt")
    elif challenger_start_delay > PROMPT_SHADOW_CHALLENGER_START_MAX_DELAY_SECONDS:
        reasons.append("challenger_start_late")
    reasons = sorted(set(reasons))
    return {"formal": not reasons, "reason_codes": reasons}


__all__ = [
    "PROMPT_FINAL_PROJECTION_SCHEMA_VERSION",
    "PROMPT_GATE_POLICY_ARTIFACT_TYPE",
    "PROMPT_GATE_POLICY_SCHEMA_VERSION",
    "PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE",
    "PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION",
    "PROMPT_SHADOW_CAPTURE_MODE",
    "PROMPT_SHADOW_OUTPUT_ARTIFACT_TYPE",
    "PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION",
    "PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES",
    "PROMPT_SHADOW_REGISTRATION_ARTIFACT_TYPE",
    "PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION",
    "PromptShadowContractError",
    "build_decision_projection",
    "build_prompt_final_projection",
    "build_prompt_gate_policy",
    "build_prompt_shadow_attempt",
    "build_prompt_shadow_input_artifact",
    "build_prompt_shadow_output",
    "build_prompt_shadow_registration",
    "decision_projection_hash",
    "normalize_artifact_receipt_ref",
    "normalize_prompt_final_projection",
    "normalize_prompt_gate_policy",
    "normalize_prompt_shadow_attempt",
    "normalize_prompt_shadow_output",
    "normalize_prompt_shadow_registration",
    "prompt_shadow_nonformal_reason",
    "validate_prompt_shadow_time_chain",
]
