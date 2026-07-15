from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.services.deepseek_http import ProviderFailure


def apply_provider_failure_to_facts(
    facts: dict[str, Any],
    *,
    failure: ProviderFailure,
    attempted_model: str,
    prompt_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark a report as a persisted, fail-closed provider fallback.

    The function mutates ``facts`` deliberately because both deterministic guard
    layers consume that exact object before the report is persisted.
    """

    pipeline = deepcopy(facts.get("pipeline")) if isinstance(facts.get("pipeline"), dict) else {}
    pipeline.update(
        {
            "provider": "offline-fallback",
            "provider_status": "fallback",
            "provider_attempted": True,
            "attempted_model": str(attempted_model),
            "provider_failure_category": failure.category,
            "provider_failure_status_code": failure.status_code,
            "provider_failure_retryable": failure.retryable,
            "provider_failure": {
                "category": failure.category,
                "retryable": failure.retryable,
                "status_code": failure.status_code,
            },
            "execution_blocked": True,
        }
    )
    if prompt_contract is not None:
        pipeline["prompt_contract"] = deepcopy(dict(prompt_contract))
    facts["pipeline"] = pipeline

    guard = deepcopy(facts.get("data_evidence_guard")) if isinstance(
        facts.get("data_evidence_guard"), dict
    ) else {}
    guard["execution_blocked"] = True
    reasons = [str(item) for item in guard.get("global_reasons") or [] if str(item)]
    reason = f"provider_failure:{failure.category}"
    if reason not in reasons:
        reasons.append(reason)
    guard["global_reasons"] = reasons
    facts["data_evidence_guard"] = guard
    return facts


def merge_pipeline_metadata(
    facts: dict[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge final pipeline metadata without dropping earlier failure/audit data."""

    existing = facts.get("pipeline") if isinstance(facts.get("pipeline"), dict) else {}
    facts["pipeline"] = {**dict(metadata), **deepcopy(existing)}
    return facts
