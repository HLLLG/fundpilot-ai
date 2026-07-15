"""D5.1 prompt-shadow registration and champion evidence lifecycle.

Every public entry point is best-effort with respect to the user-facing
champion flow.  Callers may log a failure and continue the original request;
no function in this module substitutes or mutates a champion report.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.config import Settings, get_settings
from app.models import FundDiscoveryReport, InvestorProfile, NewsItem, TopicBrief
from app.services.decision_repository import (
    canonical_hash,
    canonical_json,
    finalize_decision_quality_artifact_receipt,
    get_decision_quality_artifact_receipt,
    list_decision_quality_input_artifacts,
    put_decision_quality_input_artifact,
)
from app.services.deepseek_client import REPORT_TEMPERATURE, _build_chat_payload
from app.services.discovery_prompt import (
    DEFAULT_DISCOVERY_ROLE_PROMPT,
    DISCOVERY_PROMPT_TEMPLATE_VERSION,
)
from app.services.prompt_shadow_contracts import (
    PROMPT_FINAL_PROJECTION_SCHEMA_VERSION,
    PROMPT_GATE_POLICY_ARTIFACT_TYPE,
    PROMPT_GATE_POLICY_SCHEMA_VERSION,
    PROMPT_SHADOW_ATTEMPT_ARTIFACT_TYPE,
    PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION,
    PROMPT_SHADOW_CAPTURE_MODE,
    PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION,
    PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES,
    PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION,
    build_prompt_final_projection,
    build_prompt_gate_policy,
    build_prompt_shadow_attempt,
    build_prompt_shadow_input_artifact,
    build_prompt_shadow_output,
    build_prompt_shadow_registration,
    decision_projection_hash,
    normalize_artifact_receipt_ref,
)
from app.services.prompt_shadow_repository import (
    create_prompt_shadow_run,
    get_prompt_shadow_run,
    transition_prompt_shadow_run,
)
from app.services.provider_call_trace import ProviderCallTraceCollector


logger = logging.getLogger(__name__)

PROMPT_SHADOW_POLICY_REGISTERED_AT = "2026-07-15T00:00:00+00:00"
PROMPT_SHADOW_SCOPE = {
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

# Candidate prompt: it changes reasoning discipline, not the response schema or
# deterministic guard.  Its claims remain subordinate to the exact same facts,
# allocator, tradeability checks, and post-processing as the champion.
PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT = """You are the preregistered challenger for a Chinese mutual-fund research workbench.

Use only the supplied user payload. Work in this order: (1) reject candidates
whose quality gate, point-in-time evidence, tradeability, or benchmark status is
insufficient; (2) compare sectors using evidence available at decision time;
(3) compare funds within an eligible sector using peer-relative quality,
drawdown, trend, fees, and evidence freshness; (4) recommend zero funds when
evidence does not justify action. Treat missing or stale evidence as uncertainty,
never as neutral evidence. Prefer calibrated abstention to filling a quota.

For every recommendation, distinguish observed facts from inference, name the
strongest disconfirming evidence, and lower confidence when sources disagree.
Never invent a fund, metric, date, return, fee, holding, or market event. Do not
choose by one-year return alone. The server, not the model, owns allocation,
eligibility, and execution safety. Output only the requested JSON object."""


@dataclass
class PromptShadowCapture:
    user_id: int
    transport: str
    policy: dict[str, Any]
    policy_ref: dict[str, Any]
    registration: dict[str, Any]
    registration_ref: dict[str, Any]
    champion_attempt: dict[str, Any]
    champion_attempt_ref: dict[str, Any]
    trace_collector: ProviderCallTraceCollector
    raw_content: str | None = None
    parsed_payload: dict[str, Any] | None = None
    parse_status: str | None = None

    @property
    def run_id(self) -> str:
        return str(self.registration["run_id"])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | str) -> str:
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("prompt-shadow timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _policy_id(settings: Settings) -> str:
    champion_template = DEFAULT_DISCOVERY_ROLE_PROMPT.strip()
    challenger_template = PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT.strip()
    material = {
        "schema": PROMPT_GATE_POLICY_SCHEMA_VERSION,
        "champion_template": canonical_hash(champion_template),
        "challenger_template": canonical_hash(challenger_template),
        "assignment_key_id": settings.prompt_shadow_assignment_key_id,
        "sample_basis_points": settings.prompt_shadow_sample_basis_points,
        "max_calls": settings.prompt_shadow_max_challenger_calls_per_day,
    }
    return f"prompt-paired-gate-v2-{canonical_hash(material)[:16]}"


def build_current_prompt_shadow_policy(
    settings: Settings | None = None,
) -> dict[str, Any]:
    config = settings or get_settings()
    champion_template = DEFAULT_DISCOVERY_ROLE_PROMPT.strip()
    challenger_template = PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT.strip()
    return build_prompt_gate_policy(
        {
            "schema_version": PROMPT_GATE_POLICY_SCHEMA_VERSION,
            "policy_id": _policy_id(config),
            "registered_at": PROMPT_SHADOW_POLICY_REGISTERED_AT,
            "effective_from": PROMPT_SHADOW_POLICY_REGISTERED_AT,
            "effective_until": None,
            "scope": dict(PROMPT_SHADOW_SCOPE),
            "champion_prompt": {
                "template_version": DISCOVERY_PROMPT_TEMPLATE_VERSION,
                "template_snapshot": champion_template,
                "template_hash": canonical_hash(champion_template),
            },
            "challenger_prompt": {
                "template_version": "discovery_prompt.2026-07.shadow-candidate.v1",
                "template_snapshot": challenger_template,
                "template_hash": canonical_hash(challenger_template),
            },
            "pairing": {
                "exact_match_fields": [
                    "user_payload",
                    "model",
                    "temperature",
                    "max_tokens",
                    "response_format",
                    "transport",
                    "guard_context",
                ],
                "allowed_difference_fields": ["effective_system_prompt"],
                "max_registration_source_lag_seconds": 300,
                "max_receipt_delay_seconds": 300,
                "max_challenger_start_delay_seconds": 900,
                "max_raw_response_bytes": PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES,
                "attempts_per_role": 1,
                "capture_mode": PROMPT_SHADOW_CAPTURE_MODE,
            },
            "assignment": {
                "algorithm": "hmac_sha256_mod_10000.v1",
                "key_id": config.prompt_shadow_assignment_key_id,
                "sample_basis_points": config.prompt_shadow_sample_basis_points,
            },
            "budget": {
                "timezone": "Asia/Shanghai",
                "scope_key": "global",
                "max_challenger_calls_per_day": (
                    config.prompt_shadow_max_challenger_calls_per_day
                ),
                "reservation_policy": "consume_never_release.v1",
            },
            "gate_thresholds": {
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
            },
            "statistics": {
                "cluster_key": "live_cohort_date_local",
                "aggregation": "equal_weighted_day_means",
                "bootstrap_iterations": 10_000,
                "permutation_iterations": 10_000,
                "confidence_level": 0.95,
                "seed_derivation": (
                    "sha256(prompt-paired-gate-v2|policy_hash|stratum_hash)"
                ),
            },
            "automatic_promotion_allowed": False,
        }
    )


def _artifact_receipt_ref(
    *,
    user_id: int,
    envelope: Mapping[str, Any],
    receipt_row: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = receipt_row.get("payload")
    if not isinstance(receipt, Mapping):
        raise ValueError("artifact receipt storage row has no normalized payload")
    return normalize_artifact_receipt_ref(
        {
            "user_id": user_id,
            "artifact_id": envelope["artifact_id"],
            "artifact_type": envelope["artifact_type"],
            "artifact_content_hash": envelope["content_hash"],
            "receipt_id": receipt["receipt_id"],
            "receipt_content_hash": receipt["content_hash"],
            "source_row_created_at": receipt["source_row_created_at"],
            "source_visible_at": receipt["source_visible_at"],
        },
        expected_user_id=user_id,
        expected_artifact_type=str(envelope["artifact_type"]),
    )


def _put_receipted_artifact(
    *,
    user_id: int,
    artifact: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    envelope = build_prompt_shadow_input_artifact(
        user_id=user_id,
        artifact=artifact,
    )
    stored = put_decision_quality_input_artifact(user_id=user_id, artifact=envelope)
    stored_envelope = stored.get("payload")
    if stored_envelope != envelope:
        raise ValueError("stored prompt-shadow artifact differs from registration")
    receipt = finalize_decision_quality_artifact_receipt(
        user_id=user_id,
        artifact_id=str(envelope["artifact_id"]),
    )
    return envelope, _artifact_receipt_ref(
        user_id=user_id,
        envelope=envelope,
        receipt_row=receipt,
    )


def _policy_ref(policy: Mapping[str, Any], receipt_ref: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy_id": policy["policy_id"],
        "policy_hash": policy["policy_hash"],
        **dict(receipt_ref),
    }


def _registration_ref(
    registration: Mapping[str, Any], receipt_ref: Mapping[str, Any]
) -> dict[str, Any]:
    return {"registration_hash": registration["registration_hash"], **dict(receipt_ref)}


def _attempt_ref(attempt: Mapping[str, Any], receipt_ref: Mapping[str, Any]) -> dict[str, Any]:
    return {"attempt_hash": attempt["attempt_hash"], **dict(receipt_ref)}


def _eligible_configuration(settings: Settings) -> bool:
    secret = settings.prompt_shadow_assignment_secret
    return bool(
        settings.prompt_shadow_enabled
        and settings.deepseek_configured
        and isinstance(secret, str)
        and secret.strip()
        and 1 <= settings.prompt_shadow_sample_basis_points <= 10_000
        and settings.prompt_shadow_max_challenger_calls_per_day >= 1
        and settings.prompt_shadow_challenger_deadline_seconds > 0
    )


def _assignment(
    *,
    settings: Settings,
    user_id: int,
    decision_at: str,
    user_payload: Mapping[str, Any],
) -> dict[str, Any]:
    material = {
        "schema_version": "prompt_shadow_assignment_input.v1",
        "user_id": user_id,
        "decision_at": decision_at,
        "user_payload_hash": canonical_hash(user_payload),
    }
    serialized = canonical_json(material).encode("utf-8")
    secret = str(settings.prompt_shadow_assignment_secret).strip().encode("utf-8")
    digest = hmac.new(secret, serialized, hashlib.sha256).hexdigest()
    bucket = int(digest[:16], 16) % 10_000
    threshold = settings.prompt_shadow_sample_basis_points
    return {
        "algorithm": "hmac_sha256_mod_10000.v1",
        "key_id": settings.prompt_shadow_assignment_key_id,
        "assignment_input_hash": hashlib.sha256(serialized).hexdigest(),
        "hmac_digest": digest,
        "modulus": 10_000,
        "bucket": bucket,
        "threshold": threshold,
        "included": bucket < threshold,
    }


def prepare_prompt_shadow_champion(
    *,
    user_id: int,
    transport: str,
    champion_system_prompt: str,
    challenger_system_prompt: str,
    user_payload: Mapping[str, Any],
    model: str,
    max_tokens: int,
    target_sectors: list[str],
    focus_sectors: list[str],
    scan_mode: str,
    candidate_pool: list[dict[str, Any]],
    discovery_facts: Mapping[str, Any],
    profile: InvestorProfile,
    held_codes: set[str],
    budget_yuan: float,
    sector_heat: list[dict[str, Any]],
    market_news: list[NewsItem] | None,
    topic_briefs: list[TopicBrief] | None,
    analysis_mode: str,
    decision_at: datetime,
    default_prompt_only: bool,
    news_tool_rounds: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> PromptShadowCapture | None:
    """Preregister an assigned champion attempt and commit call-start state."""

    config = settings or get_settings()
    if not _eligible_configuration(config):
        return None
    if (
        transport not in {"sync", "stream"}
        or scan_mode != "full_market"
        or analysis_mode != "fast"
        or not default_prompt_only
        or news_tool_rounds != 0
        or budget_yuan <= 0
        or not champion_system_prompt.strip()
        or not challenger_system_prompt.strip()
        or champion_system_prompt == challenger_system_prompt
    ):
        return None
    decision = _timestamp(decision_at)
    captured_at = _timestamp(now or _utc_now())
    if datetime.fromisoformat(captured_at) - datetime.fromisoformat(decision) > timedelta(
        seconds=300
    ):
        return None

    policy = build_current_prompt_shadow_policy(config)
    _policy_envelope, policy_receipt = _put_receipted_artifact(
        user_id=user_id,
        artifact=policy,
    )
    # A policy first seen after this decision cannot be retroactively applied.
    # Persisting it warms the next request while this request remains champion-only.
    if policy_receipt["source_visible_at"] > decision:
        return None

    assignment = _assignment(
        settings=config,
        user_id=user_id,
        decision_at=decision,
        user_payload=user_payload,
    )
    if not assignment["included"]:
        return None
    champion_messages = [
        {"role": "system", "content": champion_system_prompt},
        {"role": "user", "content": canonical_json(user_payload)},
    ]
    challenger_messages = [
        {"role": "system", "content": challenger_system_prompt},
        {"role": "user", "content": canonical_json(user_payload)},
    ]
    champion_payload = _build_chat_payload(
        messages=champion_messages,
        model=model,
        max_tokens=max_tokens,
        tools=None,
        response_format={"type": "json_object"},
        temperature=REPORT_TEMPERATURE,
    )
    challenger_payload = _build_chat_payload(
        messages=challenger_messages,
        model=model,
        max_tokens=max_tokens,
        tools=None,
        response_format={"type": "json_object"},
        temperature=REPORT_TEMPERATURE,
    )
    if transport == "stream":
        champion_payload["stream"] = True
        challenger_payload["stream"] = True
    prompt_pair = {
        "user_payload": dict(user_payload),
        "user_payload_hash": canonical_hash(user_payload),
        "champion_messages": champion_messages,
        "champion_messages_hash": canonical_hash(champion_messages),
        "challenger_messages": challenger_messages,
        "challenger_messages_hash": canonical_hash(challenger_messages),
        "champion_provider_payload": champion_payload,
        "champion_provider_payload_hash": canonical_hash(champion_payload),
        "challenger_provider_payload": challenger_payload,
        "challenger_provider_payload_hash": canonical_hash(challenger_payload),
        "transport": transport,
    }
    guard_context = {
        "target_sectors": list(target_sectors),
        "focus_sectors": list(focus_sectors),
        "scan_mode": scan_mode,
        "candidate_pool": candidate_pool,
        "discovery_facts": dict(discovery_facts),
        "profile": profile.model_dump(mode="json"),
        "held_codes": sorted(held_codes),
        "requested_budget_yuan": float(budget_yuan),
        "sector_heat": sector_heat,
        "market_news": [item.model_dump(mode="json") for item in (market_news or [])],
        "topic_briefs": [item.model_dump(mode="json") for item in (topic_briefs or [])],
        "analysis_mode": analysis_mode,
        "decision_at": decision,
    }
    run_material = {
        "policy_hash": policy["policy_hash"],
        "assignment_input_hash": assignment["assignment_input_hash"],
        "champion_payload_hash": prompt_pair["champion_provider_payload_hash"],
    }
    run_id = f"dqsr_{canonical_hash(run_material)}"
    registration = build_prompt_shadow_registration(
        {
            "schema_version": PROMPT_SHADOW_REGISTRATION_SCHEMA_VERSION,
            "run_id": run_id,
            "policy_ref": _policy_ref(policy, policy_receipt),
            "decision_at": decision,
            "registered_at": captured_at,
            "capture_mode": PROMPT_SHADOW_CAPTURE_MODE,
            "scope": dict(PROMPT_SHADOW_SCOPE),
            "assignment": assignment,
            "prompt_pair": prompt_pair,
            "guard_context": guard_context,
            "guard_context_hash": canonical_hash(guard_context),
            "candidate_audit_snapshot_hash": canonical_hash(
                {
                    "schema_version": "prompt_shadow_candidate_snapshot.v1",
                    "decision_at": decision,
                    "candidate_pool": candidate_pool,
                }
            ),
            "versions": {
                "discovery_prompt_contract_version": DISCOVERY_PROMPT_TEMPLATE_VERSION,
                "discovery_guard_contract_version": "discovery_guard.current.v1",
                "discovery_allocator_contract_version": "discovery_allocator.current.v1",
                "claim_validator_contract_version": "lookthrough_claim_validator.current.v1",
                "candidate_label_policy_version": "candidate_label.t20.v2",
            },
            "label_plan": {
                "horizon_trading_days": 20,
                "utility_basis": "allocation_weighted_total_return_before_costs",
                "risk_basis": "allocation_weighted_full_path_max_drawdown",
                "cash_return_percent": 0.0,
            },
            "automatic_promotion_allowed": False,
        },
        policy=policy,
        expected_user_id=user_id,
    )
    registration_envelope, registration_receipt = _put_receipted_artifact(
        user_id=user_id,
        artifact=registration,
    )
    run = create_prompt_shadow_run(
        user_id=user_id,
        run_id=run_id,
        policy_id=str(policy["policy_id"]),
        policy_hash=str(policy["policy_hash"]),
        decision_at=decision,
        registration_artifact_id=str(registration_envelope["artifact_id"]),
        created_at=captured_at,
    )
    champion_attempt = build_prompt_shadow_attempt(
        {
            "schema_version": PROMPT_SHADOW_ATTEMPT_SCHEMA_VERSION,
            "run_id": run_id,
            "role": "champion",
            "attempt_number": 1,
            "decision_at": decision,
            "policy_ref": registration["policy_ref"],
            "registration_ref": _registration_ref(
                registration, registration_receipt
            ),
            "provider": "deepseek",
            "operation": "chat_completions",
            "endpoint_base_url": config.deepseek_base_url,
            "provider_payload_hash": prompt_pair["champion_provider_payload_hash"],
            "transport": transport,
            "pre_network_registered_at": captured_at,
            "lease": None,
            "budget_reservation": None,
            "automatic_promotion_allowed": False,
        },
        registration=registration,
        expected_user_id=user_id,
    )
    attempt_envelope, attempt_receipt = _put_receipted_artifact(
        user_id=user_id,
        artifact=champion_attempt,
    )
    if run["status"] == "registration_pending_receipt":
        run = transition_prompt_shadow_run(
            user_id=user_id,
            run_id=run_id,
            expected_status=run["status"],
            expected_state_version=run["state_version"],
            new_status="champion_attempt_pending_receipt",
            updated_at=captured_at,
            updates={
                "champion_attempt_artifact_id": attempt_envelope["artifact_id"]
            },
        )
    if run["status"] == "champion_attempt_pending_receipt":
        run = transition_prompt_shadow_run(
            user_id=user_id,
            run_id=run_id,
            expected_status=run["status"],
            expected_state_version=run["state_version"],
            new_status="champion_ready",
            updated_at=captured_at,
        )
    if run["status"] != "champion_ready":
        return None
    run = transition_prompt_shadow_run(
        user_id=user_id,
        run_id=run_id,
        expected_status="champion_ready",
        expected_state_version=run["state_version"],
        new_status="champion_call_started",
        updated_at=captured_at,
        updates={"champion_network_started_at": captured_at},
    )
    assert run["status"] == "champion_call_started"
    return PromptShadowCapture(
        user_id=user_id,
        transport=transport,
        policy=policy,
        policy_ref=_policy_ref(policy, policy_receipt),
        registration=registration,
        registration_ref=_registration_ref(registration, registration_receipt),
        champion_attempt=champion_attempt,
        champion_attempt_ref=_attempt_ref(champion_attempt, attempt_receipt),
        trace_collector=ProviderCallTraceCollector(transport=transport),
    )


def _candidate_audit_ref(*, user_id: int, report_id: str) -> dict[str, Any]:
    rows = list_decision_quality_input_artifacts(
        user_id=user_id,
        artifact_type="candidate_selection_audit",
        source_report_id=report_id,
        limit=100,
    )
    matches = [
        row
        for row in rows
        if isinstance(row.get("payload"), Mapping)
        and row["payload"].get("logical_key") == f"candidate_audit:{report_id}"
    ]
    if len(matches) != 1:
        raise ValueError("champion report has no unique candidate audit artifact")
    envelope = matches[0]["payload"]
    receipt = get_decision_quality_artifact_receipt(
        user_id=user_id,
        artifact_id=str(envelope["artifact_id"]),
    )
    if receipt is None:
        raise ValueError("champion candidate audit receipt is pending")
    return _artifact_receipt_ref(
        user_id=user_id,
        envelope=envelope,
        receipt_row=receipt,
    )


def build_prompt_shadow_projection(
    *,
    report: FundDiscoveryReport,
    requested_budget_yuan: float,
) -> dict[str, Any]:
    recommendations = [item.model_dump(mode="json") for item in report.recommendations]
    selected_codes = sorted(
        {str(item.fund_code).strip().zfill(6) for item in report.recommendations}
    )
    allocations = sorted(
        (
            {
                "fund_code": str(item.fund_code).strip().zfill(6),
                "suggested_amount_yuan": round(float(item.suggested_amount_yuan), 2),
            }
            for item in report.recommendations
            if item.suggested_amount_yuan is not None
        ),
        key=lambda row: row["fund_code"],
    )
    allocated = round(sum(row["suggested_amount_yuan"] for row in allocations), 2)
    budget = round(float(requested_budget_yuan), 2)
    audit = report.discovery_facts.get("fund_lookthrough_claim_audit")
    audit_status = (
        str(audit.get("status"))
        if isinstance(audit, Mapping) and audit.get("status") in {"clean", "sanitized"}
        else "clean"
    )
    reason_codes = sorted(
        {
            str(reason).strip()
            for item in report.eliminated_candidates
            for reason in item.reasons
            if str(reason).strip()
        }
    )
    return build_prompt_final_projection(
        {
            "schema_version": PROMPT_FINAL_PROJECTION_SCHEMA_VERSION,
            "recommendations": recommendations,
            "allocation_plan": {
                "allocations": allocations,
                "summary": str(report.allocation_plan.get("summary") or ""),
            },
            "eliminated_candidates": [
                item.model_dump(mode="json") for item in report.eliminated_candidates
            ],
            "claim_audit": {"status": audit_status, "notes": []},
            "requested_budget_yuan": budget,
            "selected_codes": selected_codes,
            "allocations": allocations,
            "unallocated_budget_yuan": round(budget - allocated, 2),
            "guard_reason_codes": reason_codes,
            "versions": {
                "discovery_guard_contract_version": "discovery_guard.current.v1",
                "discovery_allocator_contract_version": "discovery_allocator.current.v1",
                "claim_validator_contract_version": "lookthrough_claim_validator.current.v1",
            },
            "automatic_promotion_allowed": False,
        }
    )


def finalize_prompt_shadow_champion(
    *,
    capture: PromptShadowCapture,
    report: FundDiscoveryReport,
    parse_status: str,
    raw_content: str | None,
    parsed_payload: Mapping[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist the post-guard champion output after its report/audit committed."""

    trace = capture.trace_collector.require_trace()
    materialized = _timestamp(now or _utc_now())
    if materialized < trace["completed_at"]:
        materialized = trace["completed_at"]
    raw_bytes = trace["content_bytes"]
    if raw_bytes > PROMPT_SHADOW_RAW_RESPONSE_MAX_BYTES:
        stored_raw = None
        normalized_parse_status = "oversize"
        parsed = None
        parsed_hash = None
        error_category = "oversize"
    else:
        stored_raw = raw_content
        normalized_parse_status = parse_status
        parsed = dict(parsed_payload) if parsed_payload is not None else None
        parsed_hash = canonical_hash(parsed) if parsed is not None else None
        error_category = None if normalized_parse_status in {"valid", "interrupted_salvaged"} else (
            trace.get("error_category") or "provider_output_error"
        )
    successful = normalized_parse_status in {"valid", "interrupted_salvaged"}
    projection = (
        build_prompt_shadow_projection(
            report=report,
            requested_budget_yuan=float(
                capture.registration["guard_context"]["requested_budget_yuan"]
            ),
        )
        if successful
        else None
    )
    output = build_prompt_shadow_output(
        {
            "schema_version": PROMPT_SHADOW_OUTPUT_SCHEMA_VERSION,
            "run_id": capture.run_id,
            "role": "champion",
            "decision_at": capture.registration["decision_at"],
            "policy_ref": capture.policy_ref,
            "registration_ref": capture.registration_ref,
            "attempt_ref": capture.champion_attempt_ref,
            "champion_report_id": report.id,
            "variant_report_id": report.id,
            "candidate_audit_ref": _candidate_audit_ref(
                user_id=capture.user_id,
                report_id=report.id,
            ),
            "trace": trace,
            "response": {
                "raw_content": stored_raw,
                "raw_content_sha256": trace["content_sha256"],
                "raw_content_bytes": raw_bytes,
                "parse_status": normalized_parse_status,
                "parsed_payload": parsed,
                "parsed_payload_hash": parsed_hash,
                "error_category": error_category,
            },
            "final_projection": projection,
            "final_projection_hash": (
                projection["projection_hash"] if projection is not None else None
            ),
            "decision_projection_hash": (
                decision_projection_hash(projection) if projection is not None else None
            ),
            "output_materialized_at": materialized,
            "automatic_promotion_allowed": False,
        },
        registration=capture.registration,
        attempt=capture.champion_attempt,
        expected_user_id=capture.user_id,
    )
    output_envelope, _output_receipt = _put_receipted_artifact(
        user_id=capture.user_id,
        artifact=output,
    )
    run = get_prompt_shadow_run(user_id=capture.user_id, run_id=capture.run_id)
    if run is None or run["status"] != "champion_call_started":
        raise ValueError("champion output no longer owns its operational run")
    run = transition_prompt_shadow_run(
        user_id=capture.user_id,
        run_id=capture.run_id,
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status="champion_output_pending_receipt",
        updated_at=materialized,
        updates={
            "champion_output_artifact_id": output_envelope["artifact_id"],
            "champion_report_id": report.id,
        },
    )
    terminal_status = "champion_succeeded" if successful else "champion_failed"
    updates: dict[str, Any] = {}
    if successful:
        deadline_seconds = get_settings().prompt_shadow_challenger_deadline_seconds
        updates["challenger_deadline_at"] = (
            datetime.fromisoformat(materialized) + timedelta(seconds=deadline_seconds)
        ).isoformat()
    else:
        updates["terminal_reason"] = f"champion_{normalized_parse_status}"
    return transition_prompt_shadow_run(
        user_id=capture.user_id,
        run_id=capture.run_id,
        expected_status=run["status"],
        expected_state_version=run["state_version"],
        new_status=terminal_status,
        updated_at=materialized,
        updates=updates,
    )


__all__ = [
    "PROMPT_SHADOW_CHALLENGER_ROLE_PROMPT",
    "PromptShadowCapture",
    "build_current_prompt_shadow_policy",
    "build_prompt_shadow_projection",
    "finalize_prompt_shadow_champion",
    "prepare_prompt_shadow_champion",
]
