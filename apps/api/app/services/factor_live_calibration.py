"""Read-only live shadow calibration for frozen factor evidence.

Only immutable DecisionEvent v2 evidence and terminal OutcomeObservation v2 rows
are admitted.  The service never reads the latest factor snapshot and never
changes weights, prompts, policies, or model configuration.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.services.decision_repository import (
    list_decision_events,
    list_outcome_observations,
)


CALIBRATION_SCHEMA_VERSION = "factor_live_calibration.v1"
MIN_SHADOW_DATES = 20
MIN_MANUAL_REVIEW_DATES = 60
MIN_RELATIVE_COVERAGE_PERCENT = 80.0
REPOSITORY_READ_LIMIT = 10_000


class FactorLiveCalibrationStorageUnavailable(RuntimeError):
    """The authoritative evidence repository is unavailable."""


def build_factor_live_calibration(
    events: Iterable[Mapping[str, Any]],
    observations: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    input_truncated: bool = False,
) -> dict[str, Any]:
    """Aggregate calibration evidence after first collapsing duplicate dates."""

    event_rows = [_materialize(row) for row in events]
    observation_rows = [_materialize(row) for row in observations]
    formal_events = {
        str(event.get("event_id")): event
        for event in event_rows
        if _is_formal_event(event)
    }
    terminal_rows = [row for row in observation_rows if _is_terminal_v2(row)]

    excluded = defaultdict(int)
    daily_groups: dict[
        tuple[str, str, str, str, str, str, str, int],
        dict[str, dict[str, Any]],
    ] = {}
    included_observations = 0
    for observation in terminal_rows:
        event_id = str(
            observation.get("decision_event_id") or observation.get("event_id") or ""
        )
        event = formal_events.get(event_id)
        if event is None:
            excluded["missing_formal_decision_event"] += 1
            continue
        evidence = event.get("quant_evidence")
        if not isinstance(evidence, dict):
            excluded["missing_frozen_quant_evidence"] += 1
            continue
        if evidence.get("state") != "available" or evidence.get("applicable") is not True:
            excluded["quant_evidence_unavailable_or_inapplicable"] += 1
            continue
        model_version = _text(evidence.get("model_version"))
        peer_group = _text(evidence.get("peer_group"))
        reliability = _text(evidence.get("reliability_bucket"))
        factor_family = _text(evidence.get("reliability_factor_family"))
        factor_key = _text(evidence.get("reliability_factor_key"))
        factor_direction = _text(evidence.get("reliability_factor_direction"))
        factor_percentile = _finite_float(
            evidence.get("reliability_factor_percentile")
        )
        snapshot_id = _text(evidence.get("factor_snapshot_id"))
        decision_date = _iso_date(event.get("decision_date"))
        horizon = _positive_int(
            observation.get("horizon_trading_days")
            or observation.get("horizon")
        )
        if not all(
            (
                model_version,
                peer_group,
                reliability,
                factor_family,
                factor_key,
                factor_direction,
                snapshot_id,
                decision_date,
                horizon,
            )
        ) or factor_percentile is None:
            excluded["incomplete_frozen_quant_identity"] += 1
            continue

        action_direction = _action_direction(event)
        if factor_direction not in {"positive", "negative"}:
            excluded["factor_direction_not_actionable"] += 1
            continue
        if action_direction != factor_direction:
            excluded["factor_direction_not_aligned_with_final_action"] += 1
            continue
        percentile_bucket = _percentile_bucket(factor_percentile)

        key = (
            model_version,
            peer_group,
            reliability,
            factor_family,
            factor_key,
            factor_direction,
            percentile_bucket,
            int(horizon),
        )
        by_date = daily_groups.setdefault(key, {})
        day = by_date.setdefault(
            str(decision_date),
            {
                "terminal_count": 0,
                "direction_eligible": 0,
                "direction_mature": 0,
                "direction_hits": 0,
                "relative_eligible": 0,
                "relative_mature": 0,
                "relative_hits": 0,
                "relative_values": [],
                "snapshot_ids": set(),
                "cohort_modes": set(),
            },
        )
        day["terminal_count"] += 1
        day["snapshot_ids"].add(snapshot_id)
        if cohort := _text(evidence.get("cohort_mode")):
            day["cohort_modes"].add(cohort)
        metrics = observation.get("metrics")
        metrics = metrics if isinstance(metrics, dict) else {}
        _accumulate_metric(day, metrics.get("gross_direction"), prefix="direction")
        _accumulate_metric(
            day,
            metrics.get("gross_excess"),
            prefix="relative",
            capture_value=True,
        )
        included_observations += 1

    groups = [
        _summarize_group(key, by_date, input_truncated=input_truncated)
        for key, by_date in sorted(daily_groups.items())
    ]
    state = _overall_state(groups)
    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    mature_dates = {
        date_value
        for by_date in daily_groups.values()
        for date_value, row in by_date.items()
        if int(row["direction_mature"]) > 0
    }
    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "state": state,
        "mode": "shadow_read_only",
        "attribution": "factor_conditioned_association_not_causal",
        "auto_tuning_eligible": False,
        "manual_review_required": state == "ready_for_manual_review",
        "thresholds": {
            "minimum_shadow_decision_dates": MIN_SHADOW_DATES,
            "minimum_manual_review_decision_dates": MIN_MANUAL_REVIEW_DATES,
            "minimum_relative_return_coverage_percent": MIN_RELATIVE_COVERAGE_PERCENT,
        },
        "formal_event_count": len(formal_events),
        "terminal_observation_count": len(terminal_rows),
        "included_observation_count": included_observations,
        "mature_decision_date_count": len(mature_dates),
        "group_count": len(groups),
        "input_truncated": bool(input_truncated),
        "excluded": dict(sorted(excluded.items())),
        "groups": groups,
        "message": _state_message(state, groups, input_truncated=input_truncated),
        "guardrail": (
            "该结果只统计因子方向与最终动作一致时的条件关联，不证明因子单独导致收益；"
            "即使达到门槛，也不会自动修改因子权重、LLM 提示词或决策策略。"
        ),
    }


def build_factor_live_calibration_status(
    *,
    user_id: int,
    connection: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    owned_connection = connection is None
    if connection is None:
        from app.database import _connect

        connection = _connect()
    try:
        from app.config import get_settings

        if get_settings().uses_mysql and getattr(connection, "dialect", None) != "mysql":
            raise FactorLiveCalibrationStorageUnavailable(
                "量化校准主证据库不可用，拒绝回落 SQLite"
            )
        events = list_decision_events(
            user_id=user_id,
            metric_eligible_only=True,
            limit=REPOSITORY_READ_LIMIT,
            connection=connection,
        )
        observations = list_outcome_observations(
            user_id=user_id,
            limit=REPOSITORY_READ_LIMIT,
            connection=connection,
        )
    finally:
        if owned_connection and connection is not None:
            connection.close()
    truncated = (
        len(events) >= REPOSITORY_READ_LIMIT
        or len(observations) >= REPOSITORY_READ_LIMIT
    )
    return build_factor_live_calibration(
        events,
        observations,
        now=now,
        input_truncated=truncated,
    )


def _materialize(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    materialized = dict(payload) if isinstance(payload, Mapping) else dict(row)
    # Indexed storage fields are authoritative for persistence state, while all
    # decision-time evidence remains in the immutable payload.
    for key in (
        "event_id",
        "decision_event_id",
        "schema_version",
        "decision_date",
        "horizon_trading_days",
        "status",
        "is_terminal",
        "metric_eligible",
        "eligible",
        "is_backfilled",
    ):
        if key in row and row.get(key) is not None:
            materialized[key] = row.get(key)
    return materialized


def _is_formal_event(event: Mapping[str, Any]) -> bool:
    return (
        str(event.get("schema_version") or "") == "decision_event.v2"
        and event.get("metric_eligible") is True
        and event.get("eligible") is True
        and event.get("is_backfilled") is not True
        and event.get("audit_eligible") is True
        and str(event.get("store_authority") or "") == "primary"
    )


def _is_terminal_v2(observation: Mapping[str, Any]) -> bool:
    return (
        str(observation.get("schema_version") or "") == "outcome_observation.v2"
        and observation.get("is_terminal") is True
    )


def _accumulate_metric(
    day: dict[str, Any],
    raw: object,
    *,
    prefix: str,
    capture_value: bool = False,
) -> None:
    metric = raw if isinstance(raw, dict) else {}
    if not metric.get("eligible"):
        return
    day[f"{prefix}_eligible"] += 1
    if not metric.get("mature"):
        return
    hit = metric.get("hit")
    if not isinstance(hit, bool):
        return
    day[f"{prefix}_mature"] += 1
    if hit:
        day[f"{prefix}_hits"] += 1
    if capture_value:
        value = _finite_float(metric.get("value_percent"))
        if value is not None:
            day[f"{prefix}_values"].append(value)


def _summarize_group(
    key: tuple[str, str, str, str, str, str, str, int],
    by_date: dict[str, dict[str, Any]],
    *,
    input_truncated: bool,
) -> dict[str, Any]:
    (
        model_version,
        peer_group,
        reliability,
        factor_family,
        factor_key,
        factor_direction,
        percentile_bucket,
        horizon,
    ) = key
    mature_days = [row for row in by_date.values() if row["direction_mature"] > 0]
    relative_days = [row for row in mature_days if row["relative_mature"] > 0]
    daily_direction_rates = [
        row["direction_hits"] / row["direction_mature"] * 100.0
        for row in mature_days
    ]
    daily_relative_rates = [
        row["relative_hits"] / row["relative_mature"] * 100.0
        for row in relative_days
    ]
    daily_relative_returns = [
        sum(row["relative_values"]) / len(row["relative_values"])
        for row in relative_days
        if row["relative_values"]
    ]
    mature_date_count = len(mature_days)
    # Equal-weight each decision date.  Within a date, retain the fraction of
    # recommendations with a formal benchmark; one covered fund must not make
    # an otherwise uncovered multi-fund day look 100% complete.
    daily_relative_coverage = [
        (
            row["relative_mature"] / row["relative_eligible"] * 100.0
            if row["relative_eligible"] > 0
            else 0.0
        )
        for row in mature_days
    ]
    relative_coverage = _mean(daily_relative_coverage)
    state, reason = _group_state(
        mature_date_count,
        relative_coverage,
        input_truncated=input_truncated,
    )
    snapshot_ids = sorted(
        {
            snapshot_id
            for row in by_date.values()
            for snapshot_id in row["snapshot_ids"]
        }
    )
    cohort_modes = sorted(
        {
            cohort
            for row in by_date.values()
            for cohort in row["cohort_modes"]
        }
    )
    return {
        "model_version": model_version,
        "peer_group": peer_group,
        "reliability": reliability,
        "factor_family": factor_family,
        "factor_key": factor_key,
        "factor_direction": factor_direction,
        "factor_percentile_bucket": percentile_bucket,
        "action_alignment": "aligned",
        "attribution": "factor_conditioned_association_not_causal",
        "horizon_trading_days": horizon,
        "state": state,
        "reason": reason,
        "auto_tuning_eligible": False,
        "decision_date_count": len(by_date),
        "mature_decision_date_count": mature_date_count,
        "terminal_observation_count": sum(row["terminal_count"] for row in by_date.values()),
        "factor_snapshot_ids": snapshot_ids,
        "cohort_modes": cohort_modes,
        "direction": {
            "mature_decision_date_count": mature_date_count,
            "hit_rate_percent": _mean(daily_direction_rates),
            "aggregation": "equal_weight_by_decision_date",
        },
        "relative_return": {
            "metric": "gross_excess",
            "covered_decision_date_count": len(relative_days),
            "coverage_percent": relative_coverage,
            "mean_percent": _mean(daily_relative_returns, digits=4),
            "hit_rate_percent": _mean(daily_relative_rates),
            "aggregation": "equal_weight_by_decision_date",
        },
    }


def _group_state(
    mature_date_count: int,
    relative_coverage: float | None,
    *,
    input_truncated: bool,
) -> tuple[str, str]:
    if mature_date_count < MIN_SHADOW_DATES:
        return "insufficient", "mature_decision_dates_below_20"
    if mature_date_count < MIN_MANUAL_REVIEW_DATES:
        return "shadow", "collecting_until_60_mature_decision_dates"
    if input_truncated:
        return "shadow", "repository_read_limit_reached"
    if relative_coverage is None or relative_coverage < MIN_RELATIVE_COVERAGE_PERCENT:
        return "shadow", "relative_return_coverage_below_80_percent"
    return "ready_for_manual_review", "thresholds_met_manual_review_only"


def _overall_state(groups: list[dict[str, Any]]) -> str:
    states = {str(group.get("state") or "") for group in groups}
    if "ready_for_manual_review" in states:
        return "ready_for_manual_review"
    if "shadow" in states:
        return "shadow"
    return "insufficient"


def _state_message(
    state: str, groups: list[dict[str, Any]], *, input_truncated: bool
) -> str:
    if input_truncated:
        return "持久化证据达到读取上限，已保守停留在影子评估，需扩大审计读取范围。"
    if state == "ready_for_manual_review":
        ready = sum(group.get("state") == state for group in groups)
        return f"{ready} 个分组达到人工复核门槛；仍禁止自动调权或改提示词。"
    if state == "shadow":
        if any(
            group.get("reason") == "relative_return_coverage_below_80_percent"
            for group in groups
        ):
            return "成熟决策日已达观察门槛，但正式相对收益覆盖不足 80%，继续影子评估。"
        return "已有至少 20 个成熟决策日，继续影子观察至 60 日并补足相对收益覆盖。"
    return "成熟决策日不足 20 个，当前量化线上校准证据不足。"


def _mean(values: list[float], *, digits: int = 1) -> float | None:
    return round(sum(values) / len(values), digits) if values else None


def _finite_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _action_direction(event: Mapping[str, Any]) -> str | None:
    action_class = str(
        event.get("evaluation_class") or event.get("action_category") or ""
    ).strip().lower()
    if action_class in {"bullish", "buy"}:
        return "positive"
    if action_class == "bearish":
        return "negative"
    return None


def _percentile_bucket(value: float) -> str:
    bounded = max(0.0, min(100.0, value))
    if bounded <= 20:
        return "q1_0_20"
    if bounded <= 40:
        return "q2_20_40"
    if bounded <= 60:
        return "q3_40_60"
    if bounded <= 80:
        return "q4_60_80"
    return "q5_80_100"


def _iso_date(value: object) -> str | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date().isoformat()
    except ValueError:
        return None


__all__ = [
    "CALIBRATION_SCHEMA_VERSION",
    "FactorLiveCalibrationStorageUnavailable",
    "build_factor_live_calibration",
    "build_factor_live_calibration_status",
]
