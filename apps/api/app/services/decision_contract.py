from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal
from zoneinfo import ZoneInfo

from app.services.trading_session import resolve_confirm_date


DECISION_CONTRACT_SCHEMA_VERSION = "decision_contract.v1"
DECISION_EVENT_SCHEMA_VERSION = "decision_event.v2"
OUTCOME_OBSERVATION_SCHEMA_VERSION = "outcome_observation.v2"
POLICY_VERSION = "decision_policy.2026-07.v3"
FEE_MODEL_VERSION = "fee_assumption.initial_principal_haircut.v1"
ANALYSIS_PROMPT_VERSION = "analysis_prompt.2026-07.v3"
DISCOVERY_PROMPT_VERSION = "discovery_prompt.2026-07.v3"
QUANT_EVIDENCE_SNAPSHOT_SCHEMA_VERSION = "quant_evidence.v2"

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
    prompt_version = (
        ANALYSIS_PROMPT_VERSION if decision_kind == "daily" else DISCOVERY_PROMPT_VERSION
    )
    evidence_hash = payload_hash(facts.get("data_evidence") or {})
    fee_policy = _fee_policy(facts, decision_kind)
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
            "model_version": model_version,
            "prompt_version": prompt_version,
            "policy_version": POLICY_VERSION,
            "fee_model_version": FEE_MODEL_VERSION,
            "evidence_hash": evidence_hash,
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
