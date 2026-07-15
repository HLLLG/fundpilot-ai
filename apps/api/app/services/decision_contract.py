from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal
from zoneinfo import ZoneInfo

from app.services.trading_session import resolve_confirm_date


DECISION_CONTRACT_SCHEMA_VERSION = "decision_contract.v1"
DECISION_EVENT_SCHEMA_VERSION = "decision_event.v2"
OUTCOME_OBSERVATION_SCHEMA_VERSION = "outcome_observation.v2"
POLICY_VERSION = "decision_policy.2026-07.v4"
FEE_MODEL_VERSION = "fee_assumption.initial_principal_haircut.v1"
ANALYSIS_PROMPT_VERSION = "analysis_prompt.2026-07.v4"
DISCOVERY_PROMPT_VERSION = "discovery_prompt.2026-07.v4"
QUANT_EVIDENCE_SNAPSHOT_SCHEMA_VERSION = "quant_evidence.v2"
DECISION_REPLAY_BUNDLE_SCHEMA_VERSION = "decision_replay_bundle.v1"
DECISION_VARIANT_MANIFEST_SCHEMA_VERSION = "decision_variant_manifest.v1"
DECISION_REPLAY_INPUT_SCHEMA_VERSION = "decision_replay_input.v1"
DECISION_QUALITY_CONTRACT_VERSION = "decision_quality_contract.v1"
STRATEGY_VERSION = "decision_strategy.post_guard.v1"
DATA_VERSION = "decision_input_snapshot.v1"

_CN_TZ = ZoneInfo("Asia/Shanghai")
_DAILY_HORIZONS = (1, 5, 20)
_DISCOVERY_HORIZONS = (5, 20, 60)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def payload_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def derive_decision_replay_refs(data_evidence: object) -> list[dict[str, Any]]:
    """Derive immutable replay receipts from the frozen evidence registry.

    A caller cannot nominate an unrelated hash as replay evidence: every ref is
    deterministically reconstructed from the complete ``data_evidence`` item.
    An explicitly unavailable fact is still a fact known at ``fetched_at``;
    that receipt time is used without pretending that source data existed.
    """

    if not isinstance(data_evidence, Mapping):
        return []
    raw_items = data_evidence.get("items")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        return []
    items = [deepcopy(dict(item)) for item in raw_items if isinstance(item, Mapping)]
    items.sort(
        key=lambda item: (
            str(item.get("fact_id") or ""),
            payload_hash(item),
        )
    )
    refs: list[dict[str, Any]] = []
    for item in items:
        fact_id = _optional_text(item.get("fact_id"))
        fetched_at = _optional_text(item.get("fetched_at"))
        source_available_at = _optional_text(item.get("available_at"))
        refs.append(
            {
                "source": _optional_text(item.get("source")),
                "ref_id": f"data_evidence:{fact_id}" if fact_id else None,
                "available_at": source_available_at or fetched_at,
                "first_observed_at": fetched_at,
                "availability_basis": (
                    "source_available_at"
                    if source_available_at is not None
                    else "unavailability_observed_at"
                ),
                "content_hash": payload_hash(item),
            }
        )
    return refs


def build_decision_replay_bundle(
    *,
    facts: Mapping[str, Any],
    decision_kind: Literal["daily", "discovery"],
    decision_at: str,
    recorded_at: str | None = None,
    source_report_id: str | None = None,
    model_version: str,
    prompt_version: str,
    prompt_contract: Mapping[str, Any] | None,
    strategy_version: str = STRATEGY_VERSION,
    policy_version: str = POLICY_VERSION,
    fee_model_version: str = FEE_MODEL_VERSION,
    fee_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze all inputs needed to reproduce a decision variant offline."""

    canonical_decision_at = _canonical_datetime(decision_at)
    facts_snapshot = deepcopy(dict(facts))
    raw_evidence = facts_snapshot.get("data_evidence")
    data_evidence_snapshot = (
        deepcopy(dict(raw_evidence)) if isinstance(raw_evidence, Mapping) else {}
    )
    prompt_snapshot = (
        deepcopy(dict(prompt_contract))
        if isinstance(prompt_contract, Mapping)
        else None
    )
    fee_snapshot = deepcopy(dict(fee_policy or {}))
    replay_refs = derive_decision_replay_refs(data_evidence_snapshot)
    registry_receipts = [
        timestamp
        for field in ("generated_at", "recorded_at", "fetched_at", "first_observed_at")
        if data_evidence_snapshot.get(field) is not None
        and (timestamp := _strict_aware_datetime(data_evidence_snapshot.get(field)))
        is not None
    ]
    receipt_candidates = [
        timestamp
        for timestamp in (
            _strict_aware_datetime(canonical_decision_at),
            _strict_aware_datetime(recorded_at or decision_at),
            *(
                _strict_aware_datetime(ref.get("first_observed_at"))
                for ref in replay_refs
            ),
            *registry_receipts,
        )
        if timestamp is not None
    ]
    canonical_recorded_at = max(receipt_candidates).isoformat()
    data_evidence_hash = payload_hash(data_evidence_snapshot)
    facts_hash = payload_hash(facts_snapshot)
    refs_hash = payload_hash(replay_refs)
    input_descriptor = {
        "schema_version": DECISION_REPLAY_INPUT_SCHEMA_VERSION,
        "decision_kind": decision_kind,
        "decision_at": canonical_decision_at,
        "source_report_id": _optional_text(source_report_id),
        "data_evidence_hash": data_evidence_hash,
        "facts_hash": facts_hash,
        "refs_hash": refs_hash,
    }
    input_hash = payload_hash(input_descriptor)
    variant_manifest = _build_variant_manifest(
        decision_kind=decision_kind,
        model_version=model_version,
        prompt_version=prompt_version,
        prompt_contract=prompt_snapshot,
        strategy_version=strategy_version,
        policy_version=policy_version,
        data_hash=input_hash,
        evidence_hash=data_evidence_hash,
        fee_model_version=fee_model_version,
        fee_policy=fee_snapshot,
    )
    bundle: dict[str, Any] = {
        "schema_version": DECISION_REPLAY_BUNDLE_SCHEMA_VERSION,
        "hash_algorithm": "sha256",
        "canonicalization": "json.sort_keys.compact.utf8.v1",
        "decision_kind": decision_kind,
        "decision_at": canonical_decision_at,
        "recorded_at": canonical_recorded_at,
        "source_report_id": _optional_text(source_report_id),
        "data_evidence_snapshot": data_evidence_snapshot,
        "data_evidence_hash": data_evidence_hash,
        "facts_snapshot": facts_snapshot,
        "facts_hash": facts_hash,
        "replay_refs": replay_refs,
        "refs_hash": refs_hash,
        "input_descriptor": input_descriptor,
        "input_hash": input_hash,
        "prompt_contract_snapshot": prompt_snapshot,
        "fee_policy_snapshot": fee_snapshot,
        "variant_manifest": variant_manifest,
        "variant_hash": variant_manifest["variant_hash"],
    }
    bundle["bundle_hash"] = payload_hash(bundle)
    return bundle


def decision_replay_bundle_error(value: object) -> str | None:
    """Recompute every replay/variant hash; return a stable fail-closed reason."""

    if not isinstance(value, Mapping):
        return "replay_bundle_missing_or_invalid"
    bundle = dict(value)
    if bundle.get("schema_version") != DECISION_REPLAY_BUNDLE_SCHEMA_VERSION:
        return "replay_bundle_schema_invalid"
    if bundle.get("hash_algorithm") != "sha256":
        return "replay_bundle_hash_algorithm_invalid"
    if bundle.get("canonicalization") != "json.sort_keys.compact.utf8.v1":
        return "replay_bundle_canonicalization_invalid"
    decision_kind = _optional_text(bundle.get("decision_kind"))
    if decision_kind not in {"daily", "discovery"}:
        return "replay_bundle_decision_kind_invalid"
    decision_at = _strict_aware_datetime(bundle.get("decision_at"))
    recorded_at = _strict_aware_datetime(bundle.get("recorded_at"))
    if decision_at is None or recorded_at is None:
        return "replay_bundle_receipt_time_invalid"
    if recorded_at < decision_at:
        return "replay_bundle_receipt_before_decision"

    evidence = bundle.get("data_evidence_snapshot")
    facts = bundle.get("facts_snapshot")
    if not isinstance(evidence, Mapping) or not isinstance(facts, Mapping):
        return "replay_bundle_snapshot_invalid"
    frozen_evidence = dict(evidence)
    frozen_facts = dict(facts)
    for field in ("generated_at", "recorded_at", "fetched_at", "first_observed_at"):
        raw_registry_receipt = frozen_evidence.get(field)
        if raw_registry_receipt is None:
            continue
        registry_receipt = _strict_aware_datetime(raw_registry_receipt)
        if registry_receipt is None:
            return f"replay_bundle_registry_receipt_time_invalid:{field}"
        if registry_receipt > recorded_at:
            return f"replay_bundle_receipt_before_registry:{field}"
    facts_evidence = frozen_facts.get("data_evidence")
    if (dict(facts_evidence) if isinstance(facts_evidence, Mapping) else {}) != frozen_evidence:
        return "replay_bundle_evidence_snapshot_conflict"
    if bundle.get("data_evidence_hash") != payload_hash(frozen_evidence):
        return "replay_bundle_evidence_hash_mismatch"
    if bundle.get("facts_hash") != payload_hash(frozen_facts):
        return "replay_bundle_facts_hash_mismatch"

    expected_refs = derive_decision_replay_refs(frozen_evidence)
    if bundle.get("replay_refs") != expected_refs:
        return "replay_bundle_refs_mismatch"
    for ref in expected_refs:
        first_observed_at = _strict_aware_datetime(ref.get("first_observed_at"))
        if first_observed_at is None:
            return "replay_bundle_ref_receipt_time_invalid"
        if first_observed_at > recorded_at:
            return "replay_bundle_receipt_before_evidence"
    if bundle.get("refs_hash") != payload_hash(expected_refs):
        return "replay_bundle_refs_hash_mismatch"
    expected_input = {
        "schema_version": DECISION_REPLAY_INPUT_SCHEMA_VERSION,
        "decision_kind": decision_kind,
        "decision_at": str(bundle.get("decision_at")),
        "source_report_id": _optional_text(bundle.get("source_report_id")),
        "data_evidence_hash": bundle.get("data_evidence_hash"),
        "facts_hash": bundle.get("facts_hash"),
        "refs_hash": bundle.get("refs_hash"),
    }
    if bundle.get("input_descriptor") != expected_input:
        return "replay_bundle_input_descriptor_mismatch"
    expected_input_hash = payload_hash(expected_input)
    if bundle.get("input_hash") != expected_input_hash:
        return "replay_bundle_input_hash_mismatch"

    prompt_contract = bundle.get("prompt_contract_snapshot")
    if prompt_contract is not None and not isinstance(prompt_contract, Mapping):
        return "replay_bundle_prompt_contract_invalid"
    if isinstance(prompt_contract, Mapping) and prompt_contract.get("contract_hash") is not None:
        supplied_contract_hash = _optional_text(prompt_contract.get("contract_hash"))
        expected_contract_hash = payload_hash(
            {key: item for key, item in prompt_contract.items() if key != "contract_hash"}
        )
        if supplied_contract_hash != expected_contract_hash:
            return "replay_bundle_prompt_contract_hash_mismatch"
    fee_policy = bundle.get("fee_policy_snapshot")
    manifest = bundle.get("variant_manifest")
    if not isinstance(fee_policy, Mapping) or not isinstance(manifest, Mapping):
        return "replay_bundle_variant_material_invalid"
    expected_manifest = _build_variant_manifest(
        decision_kind=decision_kind,  # type: ignore[arg-type]
        model_version=str(manifest.get("model_version") or ""),
        prompt_version=str(manifest.get("prompt_version") or ""),
        prompt_contract=(dict(prompt_contract) if isinstance(prompt_contract, Mapping) else None),
        strategy_version=str(manifest.get("strategy_version") or ""),
        policy_version=str(manifest.get("policy_version") or ""),
        data_hash=expected_input_hash,
        evidence_hash=str(bundle.get("data_evidence_hash") or ""),
        fee_model_version=str(manifest.get("fee_model_version") or ""),
        fee_policy=dict(fee_policy),
    )
    if dict(manifest) != expected_manifest:
        return "replay_bundle_variant_manifest_mismatch"
    if bundle.get("variant_hash") != expected_manifest["variant_hash"]:
        return "replay_bundle_variant_hash_mismatch"
    supplied_bundle_hash = _optional_text(bundle.get("bundle_hash"))
    if not _is_sha256(supplied_bundle_hash):
        return "replay_bundle_hash_missing_or_invalid"
    expected_bundle_hash = payload_hash(
        {key: item for key, item in bundle.items() if key != "bundle_hash"}
    )
    if supplied_bundle_hash != expected_bundle_hash:
        return "replay_bundle_hash_mismatch"
    return None


def _build_variant_manifest(
    *,
    decision_kind: Literal["daily", "discovery"],
    model_version: str,
    prompt_version: str,
    prompt_contract: Mapping[str, Any] | None,
    strategy_version: str,
    policy_version: str,
    data_hash: str,
    evidence_hash: str,
    fee_model_version: str,
    fee_policy: Mapping[str, Any],
) -> dict[str, Any]:
    prompt_snapshot = dict(prompt_contract) if isinstance(prompt_contract, Mapping) else None
    prompt_contract_hash = (
        payload_hash(
            {key: item for key, item in prompt_snapshot.items() if key != "contract_hash"}
        )
        if prompt_snapshot is not None
        else payload_hash(
            {
                "status": "unavailable",
                "decision_kind": decision_kind,
                "prompt_version": str(prompt_version),
            }
        )
    )
    manifest: dict[str, Any] = {
        "schema_version": DECISION_VARIANT_MANIFEST_SCHEMA_VERSION,
        "model_version": str(model_version),
        "model_hash": payload_hash({"model_version": str(model_version)}),
        "prompt_version": str(prompt_version),
        "prompt_hash": payload_hash(
            {
                "prompt_version": str(prompt_version),
                "prompt_contract_hash": prompt_contract_hash,
            }
        ),
        "prompt_contract_hash": prompt_contract_hash,
        "strategy_version": str(strategy_version),
        "strategy_hash": payload_hash(
            {
                "strategy_version": str(strategy_version),
                "decision_kind": decision_kind,
            }
        ),
        "policy_version": str(policy_version),
        "policy_hash": payload_hash({"policy_version": str(policy_version)}),
        "data_version": DATA_VERSION,
        "data_hash": data_hash,
        "evidence_hash": evidence_hash,
        "fee_model_version": str(fee_model_version),
        "fee_model_hash": payload_hash(dict(fee_policy)),
    }
    manifest["variant_hash"] = payload_hash(manifest)
    return manifest


def build_report_decision_bundle(
    report: dict[str, Any],
    *,
    decision_kind: Literal["daily", "discovery"],
    store_authority: str = "primary",
) -> dict[str, Any]:
    """Freeze post-guard recommendations into a deterministic persistence bundle.

    The function is intentionally pure. Database writes happen only after the
    report payload, position snapshot and every initial observation have been
    built, so callers can commit the full bundle atomically.
    """

    report_id = str(report.get("id") or "").strip()
    if not report_id:
        raise ValueError("decision report id is required")
    decision_at = _canonical_datetime(report.get("created_at"))
    executable_date = _resolve_executable_date(decision_at)
    facts = _facts(report, decision_kind)
    position_snapshot = _position_snapshot(facts)
    if isinstance(position_snapshot, dict):
        if not position_snapshot.get("captured_at"):
            position_snapshot["captured_at"] = decision_at
        if not position_snapshot.get("snapshot_at"):
            position_snapshot["snapshot_at"] = position_snapshot["captured_at"]
        if not position_snapshot.get("position_as_of"):
            position_snapshot["position_as_of"] = executable_date
        if not position_snapshot.get("snapshot_date"):
            position_snapshot["snapshot_date"] = executable_date
        if not position_snapshot.get("source"):
            position_snapshot["source"] = "legacy_report_context"
    recommendations = _recommendations(report, decision_kind)
    events = _build_events(
        report=report,
        recommendations=recommendations,
        decision_kind=decision_kind,
        decision_at=decision_at,
        executable_date=executable_date,
        facts=facts,
        position_snapshot=position_snapshot,
        store_authority=store_authority,
    )
    observations = [
        observation
        for event in events
        for observation in build_initial_observations(event)
    ]
    contract = {
        "schema_version": DECISION_CONTRACT_SCHEMA_VERSION,
        "persistence": "persisted",
        "store_authority": store_authority,
        "audit_eligible": store_authority == "primary",
        "decision_kind": decision_kind,
        "policy_version": POLICY_VERSION,
        "event_count": len(events),
        "observation_count": len(observations),
        "portfolio_snapshot_id": (
            position_snapshot.get("snapshot_id")
            if isinstance(position_snapshot, dict)
            else None
        ),
        "frozen_at": decision_at,
    }
    return {
        "contract": contract,
        "position_snapshot": position_snapshot,
        "events": events,
        "observations": observations,
    }


def attach_decision_bundle(
    report: dict[str, Any],
    bundle: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(report)
    enriched["decision_contract"] = dict(bundle.get("contract") or {})
    enriched["decision_events"] = [dict(item) for item in bundle.get("events") or []]
    return enriched


def build_initial_observations(event: dict[str, Any]) -> list[dict[str, Any]]:
    evaluation_class = str(event.get("evaluation_class") or "invalid")
    if evaluation_class in {"bullish", "bearish", "buy"}:
        status = "pending"
    elif evaluation_class in {"observation", "watch_only", "conditional_wait"}:
        status = "observation"
    else:
        status = "invalid"
    return [
        {
            "schema_version": OUTCOME_OBSERVATION_SCHEMA_VERSION,
            "observation_id": f"{event['event_id']}:T+{horizon}",
            "event_id": event["event_id"],
            "horizon_trading_days": horizon,
            "target_date": None,
            "status": status,
            "observed_at": None,
            "source_available_at": None,
            "recorded_at": event.get("decision_at"),
            "source": "not_observed",
            "mature": False,
            "backfilled": False,
            "metrics": _empty_metric_set(),
        }
        for horizon in event.get("horizons") or []
    ]


def _build_events(
    *,
    report: dict[str, Any],
    recommendations: list[dict[str, Any]],
    decision_kind: Literal["daily", "discovery"],
    decision_at: str,
    executable_date: str,
    facts: dict[str, Any],
    position_snapshot: dict[str, Any] | None,
    store_authority: str,
) -> list[dict[str, Any]]:
    report_id = str(report["id"])
    model_version = _model_version(report, facts, decision_kind)
    prompt_contract = _prompt_contract(facts)
    prompt_version = _prompt_version(prompt_contract, decision_kind)
    fee_policy = _fee_policy(facts, decision_kind)
    replay_bundle = build_decision_replay_bundle(
        facts=facts,
        decision_kind=decision_kind,
        decision_at=decision_at,
        recorded_at=decision_at,
        source_report_id=report_id,
        model_version=model_version,
        prompt_version=prompt_version,
        prompt_contract=prompt_contract,
        strategy_version=STRATEGY_VERSION,
        policy_version=POLICY_VERSION,
        fee_model_version=FEE_MODEL_VERSION,
        fee_policy=fee_policy,
    )
    variant_manifest = dict(replay_bundle["variant_manifest"])
    benchmark_by_code = _benchmark_specs(facts)
    events: list[dict[str, Any]] = []
    for index, recommendation in enumerate(recommendations):
        code = _fund_code(recommendation.get("fund_code"))
        action = str(recommendation.get("action") or "").strip()
        evaluation_class = _evaluation_class(action, decision_kind)
        event_id = f"{decision_kind}:{report_id}:{index}:{code or 'invalid'}"
        benchmark = dict(benchmark_by_code.get(code or "") or _benchmark_unavailable())
        quant_evidence = _freeze_quant_evidence(
            facts,
            decision_kind=decision_kind,
            fund_code=code,
            frozen_at=decision_at,
        )
        event = {
            "schema_version": DECISION_EVENT_SCHEMA_VERSION,
            "quality_contract_version": DECISION_QUALITY_CONTRACT_VERSION,
            "replay_contract_required": True,
            "event_id": event_id,
            "event_type": (
                "daily_fund_decision"
                if decision_kind == "daily"
                else "fund_discovery_decision"
            ),
            "source_type": decision_kind,
            "decision_kind": decision_kind,
            "report_id": report_id,
            "source_report_id": report_id,
            "recommendation_index": index,
            "decision_at": decision_at,
            "recorded_at": replay_bundle["recorded_at"],
            "decision_date": _local_date(decision_at),
            "executable_calendar_date": executable_date,
            "execution_policy": "first_fund_valuation_on_or_after_executable_date",
            "fund_code": code,
            "fund_name": str(recommendation.get("fund_name") or "").strip(),
            "action": action,
            "proposed_action": action or None,
            "final_action": action,
            "action_source": "post_guard_final",
            "evaluation_class": evaluation_class,
            "eligible": evaluation_class in {"bullish", "bearish", "buy"},
            "horizons": list(
                _DAILY_HORIZONS if decision_kind == "daily" else _DISCOVERY_HORIZONS
            ),
            "portfolio_snapshot_id": (
                position_snapshot.get("snapshot_id")
                if isinstance(position_snapshot, dict)
                else None
            ),
            "ledger_version": (
                position_snapshot.get("ledger_version")
                if isinstance(position_snapshot, dict)
                else None
            ),
            "position_complete": bool(
                isinstance(position_snapshot, dict)
                and position_snapshot.get("position_complete")
            ),
            "position_truth_status": (
                (position_snapshot.get("completeness") or {}).get(
                    "position_truth_status"
                )
                if isinstance(position_snapshot, dict)
                else "unknown"
            ),
            "benchmark": benchmark,
            "fee_policy": fee_policy,
            # Point-in-time factor evidence is part of the immutable event.  It
            # must never be reconstructed from a newer IC snapshot during
            # outcome evaluation or calibration.
            "quant_evidence": quant_evidence,
            "model_version": variant_manifest["model_version"],
            "model_hash": variant_manifest["model_hash"],
            "prompt_version": variant_manifest["prompt_version"],
            "prompt_hash": variant_manifest["prompt_hash"],
            "prompt_contract_hash": variant_manifest["prompt_contract_hash"],
            "strategy_version": variant_manifest["strategy_version"],
            "strategy_hash": variant_manifest["strategy_hash"],
            "policy_version": variant_manifest["policy_version"],
            "policy_hash": variant_manifest["policy_hash"],
            "data_version": variant_manifest["data_version"],
            "data_hash": variant_manifest["data_hash"],
            "evidence_hash": variant_manifest["evidence_hash"],
            "fee_model_version": variant_manifest["fee_model_version"],
            "fee_model_hash": variant_manifest["fee_model_hash"],
            "variant_hash": variant_manifest["variant_hash"],
            "variant_manifest": deepcopy(variant_manifest),
            "replay_refs": deepcopy(replay_bundle["replay_refs"]),
            "replay_bundle": deepcopy(replay_bundle),
            "replay_bundle_hash": replay_bundle["bundle_hash"],
            "fund_type": _event_fund_type(recommendation, facts, code),
            "market_regime": _event_market_regime(facts),
            "data_completeness": _event_data_completeness(facts),
            "store_authority": store_authority,
            "is_backfilled": False,
            "audit_eligible": store_authority == "primary",
            "metric_eligible": store_authority == "primary",
            "action_category": evaluation_class,
            "fee_model": fee_policy,
            "fee_model_index": fee_policy.get("fee_source"),
            "benchmark_mapping_id": benchmark.get("mapping_id"),
            "recommendation": recommendation,
        }
        success_probability = _probability(recommendation.get("success_probability"))
        if success_probability is not None:
            event["success_probability"] = success_probability
        if prompt_contract is not None:
            # Each event is immutable evidence in its own right.  Do not retain
            # a reference either to report facts or to a sibling event.
            event["prompt_contract"] = deepcopy(prompt_contract)
        event["payload_hash"] = payload_hash(event)
        events.append(event)
    return events


def _freeze_quant_evidence(
    facts: dict[str, Any],
    *,
    decision_kind: Literal["daily", "discovery"],
    fund_code: str | None,
    frozen_at: str,
) -> dict[str, Any]:
    """Freeze only the factor evidence already present at decision time.

    The helper intentionally does not call the IC loader or any data provider.
    Missing/stale evidence therefore stays unavailable forever for this event,
    instead of being silently backfilled from a future snapshot.
    """

    source_key = (
        "factor_scores" if decision_kind == "daily" else "candidate_factor_scores"
    )
    raw = facts.get(source_key)
    source = dict(raw) if isinstance(raw, dict) else {}
    status_raw = source.get("ic_status")
    status = dict(status_raw) if isinstance(status_raw, dict) else {}
    normalized_code = _fund_code(fund_code)
    row = next(
        (
            dict(item)
            for item in source.get("holdings") or []
            if isinstance(item, dict)
            and normalized_code is not None
            and _fund_code(item.get("fund_code")) == normalized_code
        ),
        None,
    )

    reliability_source = (
        row.get("factor_reliability")
        if isinstance(row, dict) and isinstance(row.get("factor_reliability"), dict)
        else source.get("factor_reliability")
    )
    reliability = (
        json.loads(canonical_json(reliability_source))
        if isinstance(reliability_source, dict)
        else {}
    )
    percentiles_source = row.get("factor_percentiles") if isinstance(row, dict) else None
    percentiles = (
        json.loads(canonical_json(percentiles_source))
        if isinstance(percentiles_source, dict)
        else {}
    )
    typed_percentiles_source = (
        row.get("typed_factor_percentiles") if isinstance(row, dict) else None
    )
    typed_percentiles = (
        json.loads(canonical_json(typed_percentiles_source))
        if isinstance(typed_percentiles_source, dict)
        else {}
    )
    typed_reliability_source = (
        row.get("typed_factor_reliability") if isinstance(row, dict) else None
    )
    typed_reliability = (
        json.loads(canonical_json(typed_reliability_source))
        if isinstance(typed_reliability_source, dict)
        else {}
    )
    raw_typed_used_keys = row.get("typed_used_keys") if isinstance(row, dict) else None
    typed_used_keys = (
        sorted(
            {
                str(key).strip()
                for key in raw_typed_used_keys
                if str(key).strip()
            }
        )
        if isinstance(raw_typed_used_keys, (list, tuple, set, frozenset))
        else []
    )
    typed_applicable = bool(
        isinstance(row, dict) and row.get("typed_factor_applicable")
    )
    reliability_selection = _factor_reliability_selection(
        percentiles,
        reliability,
        typed_percentiles=typed_percentiles,
        typed_reliability=typed_reliability,
        typed_used_keys=typed_used_keys,
        typed_applicable=typed_applicable,
    )
    snapshot_id = _optional_text(status.get("snapshot_id"))
    factor_model_version = _optional_text(source.get("model_version"))
    model_data_as_of = _optional_text(
        status.get("run_date") or status.get("generated_at")
    )
    model_generated_at = _optional_text(status.get("generated_at"))
    model_published_at = _optional_text(status.get("published_at"))
    target_feature_as_of = (
        _optional_text(row.get("target_feature_as_of"))
        if isinstance(row, dict)
        else None
    )
    target_feature_observed_at = (
        _optional_text(row.get("target_feature_observed_at"))
        if isinstance(row, dict)
        else None
    )
    target_feature_freshness = (
        _optional_text(row.get("target_feature_freshness"))
        if isinstance(row, dict)
        else None
    )
    cohort_mode = _optional_text(status.get("cohort_mode"))
    status_state = str(status.get("state") or "").strip().lower()
    if not status_state:
        status_state = "available" if status.get("available") else "unavailable"

    reason: str | None = None
    if not isinstance(raw, dict) or not source:
        reason = "factor_evidence_not_attached_at_decision_time"
    elif not source.get("available"):
        reason = "factor_scores_unavailable_at_decision_time"
    elif row is None:
        reason = "fund_factor_row_missing_at_decision_time"
    elif status_state != "available" or status.get("stale") is True:
        reason = "factor_ic_snapshot_not_current_at_decision_time"
    elif snapshot_id is None:
        reason = "factor_snapshot_id_missing_at_decision_time"
    elif factor_model_version is None:
        reason = "factor_model_version_missing_at_decision_time"
    elif model_generated_at is None:
        reason = "factor_model_generated_at_missing_at_decision_time"
    elif model_published_at is None:
        reason = "factor_model_published_at_missing_at_decision_time"
    elif not _audit_timestamp_not_after(
        model_generated_at,
        frozen_at,
        tolerance=timedelta(minutes=5),
    ):
        reason = "factor_model_generated_after_decision_time"
    elif not _audit_timestamp_not_after(
        model_published_at,
        frozen_at,
        tolerance=timedelta(minutes=5),
    ):
        reason = "factor_model_published_after_decision_time"
    elif target_feature_as_of is None:
        reason = "target_factor_feature_as_of_missing_at_decision_time"
    elif target_feature_observed_at is None:
        reason = "target_factor_feature_observed_at_missing_at_decision_time"
    elif not _audit_timestamp_not_after(
        target_feature_observed_at,
        frozen_at,
        tolerance=timedelta(minutes=5),
    ):
        reason = "target_factor_feature_observed_after_decision_time"
    elif target_feature_freshness != "fresh":
        reason = "target_factor_feature_not_fresh_at_decision_time"

    row_applicable = bool(row.get("applicable", True)) if isinstance(row, dict) else False
    applicable = reason is None and row_applicable
    if reason is None and not row_applicable:
        reason = "fund_factor_evidence_not_applicable"

    return {
        "schema_version": QUANT_EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
        "state": "available" if reason is None else "unavailable",
        "reason": reason,
        "source": source_key,
        "factor_snapshot_id": snapshot_id,
        "model_version": factor_model_version,
        "schema": status.get("schema_version"),
        "cohort_mode": cohort_mode,
        "peer_group": row.get("peer_group") if isinstance(row, dict) else None,
        "composite_score": row.get("composite_score") if isinstance(row, dict) else None,
        "composite_grade": row.get("composite_grade") if isinstance(row, dict) else None,
        "base_composite_score": (
            row.get("base_composite_score") if isinstance(row, dict) else None
        ),
        "factor_percentiles": percentiles,
        "reliability": reliability,
        "reliability_bucket": reliability_selection["level"],
        "reliability_factor_key": reliability_selection["factor_key"],
        "reliability_factor_family": reliability_selection["factor_family"],
        "reliability_factor_percentile": reliability_selection["percentile"],
        "reliability_factor_direction": reliability_selection["direction"],
        "typed_factor_schema": (
            row.get("typed_factor_schema") if isinstance(row, dict) else None
        ),
        "typed_used_keys": typed_used_keys,
        "typed_factor_percentiles": typed_percentiles,
        "typed_factor_reliability": typed_reliability,
        "typed_factor_applicable": typed_applicable,
        "typed_feature_completeness": (
            row.get("typed_feature_completeness") if isinstance(row, dict) else None
        ),
        "typed_factor_score": (
            row.get("typed_factor_score") if isinstance(row, dict) else None
        ),
        "typed_factor_basis": (
            row.get("typed_factor_basis") if isinstance(row, dict) else None
        ),
        "applicable": applicable,
        # Backward-compatible name now means the target feature date; model time
        # is frozen separately so neither can masquerade as the other.
        "data_as_of": target_feature_as_of,
        "model_data_as_of": model_data_as_of,
        "model_generated_at": model_generated_at,
        "model_published_at": model_published_at,
        "target_feature_as_of": target_feature_as_of,
        "target_feature_observed_at": target_feature_observed_at,
        "target_feature_source": (
            row.get("target_feature_source") if isinstance(row, dict) else None
        ),
        "target_return_coverage": (
            row.get("target_return_coverage") if isinstance(row, dict) else None
        ),
        "target_nav_age_trading_days": (
            row.get("target_nav_age_trading_days")
            if isinstance(row, dict)
            else None
        ),
        "target_feature_freshness": target_feature_freshness,
        "target_feature_max_age_trading_days": (
            row.get("target_feature_max_age_trading_days")
            if isinstance(row, dict)
            else None
        ),
        "frozen_at": frozen_at,
    }


def _audit_timestamp_not_after(
    value: str,
    frozen_at: str,
    *,
    tolerance: timedelta,
) -> bool:
    """Return false for malformed/naive/future audit timestamps."""

    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        frozen = datetime.fromisoformat(frozen_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if observed.tzinfo is None or frozen.tzinfo is None:
        return False
    return observed.astimezone(timezone.utc) <= (
        frozen.astimezone(timezone.utc) + tolerance
    )


def _factor_reliability_selection(
    percentiles: dict[str, Any],
    reliability: dict[str, Any],
    *,
    typed_percentiles: dict[str, Any],
    typed_reliability: dict[str, Any],
    typed_used_keys: list[str],
    typed_applicable: bool,
) -> dict[str, Any]:
    """Select the evidence family whose live contribution is being calibrated."""

    score_by_level = {"高": 3, "中": 2, "低": 1}
    common_candidates: list[tuple[int, float, str, str, str, float, str]] = []
    for key in ("momentum", "risk_adjusted", "drawdown"):
        detail = reliability.get(key)
        if not isinstance(detail, dict):
            continue
        level = str(detail.get("level") or "").strip()
        if level not in score_by_level:
            continue
        try:
            percentile = float(percentiles.get(key))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(percentile):
            continue
        common_candidates.append(
            (
                score_by_level[level],
                abs(percentile - 50.0),
                key,
                "common",
                level,
                percentile,
                str(detail.get("basis") or ""),
            )
        )
    typed_candidates: list[tuple[int, float, str, str, str, float, str]] = []
    if typed_applicable:
        for key in typed_used_keys:
            detail = typed_reliability.get(key)
            if not isinstance(detail, dict):
                continue
            level = str(detail.get("level") or "").strip()
            economic = detail.get("economic_significance")
            if (
                level not in score_by_level
                or detail.get("qualified") is not True
                or detail.get("orientation") != "higher_is_better"
                or not isinstance(economic, dict)
                or economic.get("qualified") is not True
            ):
                continue
            try:
                percentile = float(typed_percentiles.get(key))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(percentile):
                continue
            typed_candidates.append(
                (
                    score_by_level[level],
                    abs(percentile - 50.0),
                    key,
                    "fund_type_specific",
                    level,
                    percentile,
                    str(detail.get("basis") or ""),
                )
            )
    # 类型因子属于这轮要独立校准的新证据：只要它确实进入最终 70/30 分数，
    # 校准分桶就跟随该类型因子，而不是被更成熟的基础因子覆盖；没有实际采用
    # 的类型因子时才回落到基础因子。
    candidates = typed_candidates or common_candidates
    if not candidates:
        return {
            "level": "不足",
            "factor_key": None,
            "factor_family": None,
            "percentile": None,
            "direction": "unknown",
        }
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, key, family, level, percentile, basis = candidates[0]
    direction = (
        "neutral"
        if 45 <= percentile <= 55
        else "positive" if percentile > 55 else "negative"
    )
    if "反向" in basis or "均值回归" in basis:
        direction = {"positive": "negative", "negative": "positive"}.get(
            direction,
            direction,
        )
    return {
        "level": level,
        "factor_key": key,
        "factor_family": family,
        "percentile": round(percentile, 4),
        "direction": direction,
    }


def _recommendations(
    report: dict[str, Any], decision_kind: Literal["daily", "discovery"]
) -> list[dict[str, Any]]:
    key = "fund_recommendations" if decision_kind == "daily" else "recommendations"
    rows = report.get(key) or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _facts(
    report: dict[str, Any], decision_kind: Literal["daily", "discovery"]
) -> dict[str, Any]:
    key = "analysis_facts" if decision_kind == "daily" else "discovery_facts"
    facts = report.get(key)
    return dict(facts) if isinstance(facts, dict) else {}


def _position_snapshot(facts: dict[str, Any]) -> dict[str, Any] | None:
    full = facts.get("portfolio_position_snapshot")
    if isinstance(full, dict):
        return dict(full)
    preflight = facts.get("portfolio_snapshot")
    if not isinstance(preflight, dict):
        return None
    nested = preflight.get("position_snapshot")
    if isinstance(nested, dict):
        return dict(nested)
    # Compatibility with batch two. This is useful for traceability but must not
    # be promoted to a complete shares/cost snapshot.
    return {
        "schema_version": "portfolio_position_snapshot.legacy",
        "snapshot_id": preflight.get("snapshot_id"),
        "position_as_of": preflight.get("as_of_date"),
        "captured_at": preflight.get("captured_at"),
        "source": preflight.get("source"),
        "authoritative": bool(preflight.get("authoritative")),
        "ledger_version": None,
        "position_fingerprint": preflight.get("holdings_fingerprint"),
        "position_complete": False,
        "cash": {"balance_cny": None, "status": "unknown"},
        "positions": [],
        "legacy": True,
    }


def _fee_policy(
    facts: dict[str, Any], decision_kind: Literal["daily", "discovery"]
) -> dict[str, Any]:
    if decision_kind == "daily":
        source = facts.get("portfolio") or {}
    else:
        source = facts.get("profile") or {}
    rate = _non_negative_float(source.get("round_trip_fee_percent"))
    return {
        "model_version": FEE_MODEL_VERSION,
        "status": "available" if rate is not None else "not_frozen",
        "fee_source": "user_assumption" if rate is not None else "unavailable",
        "round_trip_fee_percent": rate,
        "fee_calculation": "initial_principal_haircut" if rate is not None else None,
        "is_actual_cost": False,
        "recurring_fund_expenses": "already_embedded_in_nav",
    }


def _benchmark_specs(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = facts.get("benchmark_specs")
    if isinstance(raw, dict):
        return {
            str(code).strip().zfill(6): dict(spec)
            for code, spec in raw.items()
            if isinstance(spec, dict)
        }
    if isinstance(raw, list):
        return {
            str(spec.get("fund_code") or "").strip().zfill(6): dict(spec)
            for spec in raw
            if isinstance(spec, dict) and spec.get("fund_code")
        }
    return {}


def _benchmark_unavailable() -> dict[str, Any]:
    return {
        "tier": "unavailable",
        "status": "unavailable",
        "formal_excess_eligible": False,
        "reason": "point_in_time_benchmark_not_frozen",
        "components": [],
    }


def _model_version(
    report: dict[str, Any],
    facts: dict[str, Any],
    decision_kind: Literal["daily", "discovery"],
) -> str:
    pipeline = facts.get("pipeline") or {}
    if isinstance(pipeline, dict) and pipeline.get("model"):
        return str(pipeline["model"])
    runtime = facts.get("decision_runtime") or {}
    if isinstance(runtime, dict) and runtime.get("model"):
        return str(runtime["model"])
    provider = str(report.get("provider") or "").strip()
    if provider:
        return provider
    return "unknown"


def _prompt_contract(facts: dict[str, Any]) -> dict[str, Any] | None:
    """Read the A2 prompt provenance while preserving legacy report support."""

    pipeline = facts.get("pipeline")
    if isinstance(pipeline, dict):
        contract = pipeline.get("prompt_contract")
        if isinstance(contract, dict):
            return deepcopy(contract)
    legacy = facts.get("prompt_contract")
    return deepcopy(legacy) if isinstance(legacy, dict) else None


def _prompt_version(
    prompt_contract: dict[str, Any] | None,
    decision_kind: Literal["daily", "discovery"],
) -> str:
    if isinstance(prompt_contract, dict):
        template_version = str(prompt_contract.get("template_version") or "").strip()
        if template_version:
            return template_version
    return (
        ANALYSIS_PROMPT_VERSION
        if decision_kind == "daily"
        else DISCOVERY_PROMPT_VERSION
    )


def _evaluation_class(action: str, decision_kind: str) -> str:
    if decision_kind == "daily":
        if any(token in action for token in ("清仓", "减仓", "暂停追涨", "卖出", "赎回")):
            return "bearish"
        if any(token in action for token in ("加仓", "定投", "买入", "申购", "分批")):
            return "bullish"
        return "observation" if action else "invalid"
    if action in {"分批买入", "建议买入", "买入", "申购"}:
        return "buy"
    if action in {"建议关注", "观察"}:
        return "watch_only"
    if action in {"等待回调"}:
        return "conditional_wait"
    return "invalid"


def _resolve_executable_date(decision_at: str) -> str:
    moment = datetime.fromisoformat(decision_at.replace("Z", "+00:00")).astimezone(_CN_TZ)
    return resolve_confirm_date(moment.strftime("%Y-%m-%d %H:%M:%S"))


def _canonical_datetime(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("decision timestamp is required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _local_date(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(_CN_TZ).date().isoformat()


def _fund_code(value: object) -> str | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    code = text.zfill(6)
    return code if len(code) == 6 and code != "000000" else None


def _event_fund_type(
    recommendation: Mapping[str, Any],
    facts: Mapping[str, Any],
    fund_code: str | None,
) -> str:
    direct = _optional_text(
        recommendation.get("fund_type") or recommendation.get("type")
    )
    if direct is not None:
        return direct
    for key in (
        "holdings",
        "candidates",
        "candidate_pool",
        "funds",
        "recommendations",
    ):
        raw = facts.get(key)
        if isinstance(raw, Mapping):
            raw = raw.get("items") or raw.get("members") or raw.get("candidates")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            continue
        for row in raw:
            if not isinstance(row, Mapping):
                continue
            if fund_code is not None and _fund_code(row.get("fund_code")) != fund_code:
                continue
            candidate = _optional_text(row.get("fund_type") or row.get("type"))
            if candidate is not None:
                return candidate
    return "unknown"


def _event_market_regime(facts: Mapping[str, Any]) -> str:
    for key in ("market_regime", "market_state", "regime"):
        raw = facts.get(key)
        if isinstance(raw, Mapping):
            raw = raw.get("regime") or raw.get("state") or raw.get("label")
        text = _optional_text(raw)
        if text is not None:
            return text
    breadth = facts.get("market_breadth")
    if isinstance(breadth, Mapping):
        text = _optional_text(
            breadth.get("market_regime")
            or breadth.get("regime")
            or breadth.get("state")
        )
        if text is not None:
            return text
    return "unknown"


def _event_data_completeness(facts: Mapping[str, Any]) -> str:
    direct = facts.get("data_completeness")
    if isinstance(direct, Mapping):
        direct = direct.get("status") or direct.get("level")
    text = _optional_text(direct)
    if text is not None:
        return text
    evidence = facts.get("data_evidence")
    if isinstance(evidence, Mapping) and isinstance(evidence.get("decision_ready"), bool):
        return "complete" if evidence.get("decision_ready") is True else "partial"
    guard = facts.get("data_evidence_guard")
    if isinstance(guard, Mapping) and isinstance(guard.get("execution_blocked"), bool):
        return "partial" if guard.get("execution_blocked") is True else "complete"
    return "unknown"


def _probability(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and 0 <= parsed <= 1 else None


def _strict_aware_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_sha256(value: str | None) -> bool:
    return bool(
        value
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _non_negative_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _empty_metric_set() -> dict[str, dict[str, Any]]:
    return {
        name: {"eligible": False, "value_percent": None, "hit": None}
        for name in (
            "gross_direction",
            "positive_net_return",
            "gross_excess",
            "net_excess",
        )
    }


def event_ids(events: Iterable[dict[str, Any]]) -> list[str]:
    return [str(event.get("event_id")) for event in events if event.get("event_id")]
