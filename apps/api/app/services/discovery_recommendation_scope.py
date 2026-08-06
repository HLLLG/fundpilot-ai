from __future__ import annotations

"""Deterministic direction-to-fund scope for discovery recommendations.

The candidate pool is intentionally broader than the final recommendation set:
it also contains research candidates for directions that are still forming.  A
language model must not be allowed to use those research rows to fill the
0--3 recommendation slots.  This module derives one auditable whitelist from
the same direction and fund gates used by the deterministic discovery guard.
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

from app.models import DiscoveryRecommendation
from app.services.discovery_sector_identity import candidate_sector_identity_is_executable
from app.services.discovery_selection_strategy import (
    fund_entry_opens_v3_improving_flow_probe,
    fund_entry_opens_v3_probability_probe,
    fund_recovery_overrides_sector_position,
)
from app.services.sector_opportunity_scoring import (
    ENTRY_READY_TO_START,
    MATURITY_POLICY_VERSIONS,
)

RECOMMENDATION_SCOPE_VERSION = "discovery_recommendation_scope.2026-08.v4"
MAX_DISCOVERY_RECOMMENDATIONS = 6
MAX_RECOMMENDATIONS_PER_SECTOR = 2

_ENTRY_PATH_PRIORITY = {
    "confirmed_entry": 4,
    "fund_position_recovery": 3,
    "flow_improving_probe": 2,
    "probability_early_probe": 1,
}


def build_recommendation_candidate_scope(
    candidate_pool: Sequence[Mapping[str, Any]],
    sector_opportunities: Sequence[Mapping[str, Any]],
    *,
    entry_policy_version: str | None = None,
) -> dict[str, Any]:
    """Build the persisted recommendation whitelist and candidate funnel."""

    opportunities = [dict(item) for item in sector_opportunities if isinstance(item, Mapping)]
    opportunity_by_sector = {
        str(item.get("sector_label") or "").strip(): item
        for item in opportunities
        if str(item.get("sector_label") or "").strip()
    }
    policy_enforced = bool(
        str(entry_policy_version or "") in MATURITY_POLICY_VERSIONS
        or any(
            str(item.get("score_policy_version") or "") in MATURITY_POLICY_VERSIONS
            for item in opportunities
        )
    )
    if not policy_enforced:
        return {
            "schema_version": RECOMMENDATION_SCOPE_VERSION,
            "policy_enforced": False,
            "ordered_eligible_fund_codes": [],
            "actionable_sector_labels": [],
            "eligible_sector_labels": [],
            "unmatched_actionable_sector_labels": [],
            "research_sector_labels": [],
            "sector_funnel": [],
            "candidate_decisions": [],
            "conditional_wait_fund_codes": [],
            "watch_only_fund_codes": [],
            "instruction": "历史报告未启用方向成熟度策略，沿用原候选选择逻辑。",
        }

    candidates_by_sector: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for raw in candidate_pool:
        if not isinstance(raw, Mapping):
            continue
        sector = str(raw.get("sector_label") or "").strip()
        if sector:
            candidates_by_sector[sector].append(raw)

    eligible_by_sector: dict[str, list[tuple[Mapping[str, Any], str]]] = defaultdict(list)
    candidate_decisions: list[dict[str, Any]] = []
    decisions_by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evaluated_codes: set[str] = set()
    funnel: list[dict[str, Any]] = []
    for sector, opportunity in opportunity_by_sector.items():
        rows = candidates_by_sector.get(sector, [])
        reason_counts: Counter[str] = Counter()
        for candidate in rows:
            code = _fund_code(candidate)
            reasons = _fund_gate_reasons(candidate)
            entry_path = _candidate_entry_path(candidate, opportunity)
            if entry_path is None:
                reasons.append("direction_entry_not_open")
            decision = _candidate_decision(
                candidate,
                entry_path=entry_path,
                reason_codes=reasons,
            )
            candidate_decisions.append(decision)
            decisions_by_sector[sector].append(decision)
            if code:
                evaluated_codes.add(code)
            if decision["status"] == "actionable":
                eligible_by_sector[sector].append((candidate, entry_path or "confirmed_entry"))
            else:
                reason_counts.update(set(reasons))

        eligible_by_sector[sector].sort(
            key=lambda pair: _candidate_rank_key(pair[0], pair[1], opportunity),
            reverse=True,
        )
        funnel.append(
            {
                "sector_label": sector,
                "entry_state": opportunity.get("entry_state"),
                "direction_path": _direction_path(opportunity),
                "recalled_count": len(rows),
                "eligible_count": len(eligible_by_sector[sector]),
                "rejected_count": max(0, len(rows) - len(eligible_by_sector[sector])),
                "conditional_wait_count": sum(
                    1
                    for item in decisions_by_sector[sector]
                    if item["status"] == "conditional_wait"
                ),
                "watch_only_count": sum(
                    1
                    for item in decisions_by_sector[sector]
                    if item["status"] == "watch_only"
                ),
                "rejected_reason_counts": dict(sorted(reason_counts.items())),
            }
        )

    # A report can retain a broad research candidate even when its direction
    # evidence is absent from a historical/partial opportunity snapshot. Keep
    # that row visible and fail closed instead of silently dropping it from all
    # three user-facing decision buckets.
    for raw in candidate_pool:
        if not isinstance(raw, Mapping):
            continue
        code = _fund_code(raw)
        if code and code in evaluated_codes:
            continue
        reasons = _fund_gate_reasons(raw)
        reasons.append("direction_evidence_unavailable")
        candidate_decisions.append(
            _candidate_decision(raw, entry_path=None, reason_codes=reasons)
        )

    ranked_sectors = sorted(
        eligible_by_sector,
        key=lambda sector: _sector_rank_key(
            sector,
            eligible_by_sector[sector],
            opportunity_by_sector[sector],
        ),
        reverse=True,
    )
    # Keep at most two independently selected fund families per direction. The
    # first pass exposes the best-quality vehicle from each open sector before
    # a second vehicle is added, preserving cross-sector diversity under the
    # global six-recommendation cap.
    ranked_codes: list[str] = []
    for depth in range(MAX_RECOMMENDATIONS_PER_SECTOR):
        for sector in ranked_sectors:
            rows = eligible_by_sector[sector]
            if depth >= len(rows):
                continue
            code = _fund_code(rows[depth][0])
            if code:
                ranked_codes.append(code)
    ordered_codes = ranked_codes[:MAX_DISCOVERY_RECOMMENDATIONS]
    alternate_codes = ranked_codes[MAX_DISCOVERY_RECOMMENDATIONS:]
    for sector in ranked_sectors:
        alternate_codes.extend(
            code
            for candidate, _entry_path in eligible_by_sector[sector][
                MAX_RECOMMENDATIONS_PER_SECTOR:
            ]
            if (code := _fund_code(candidate))
        )

    actionable_labels = [
        sector
        for sector, opportunity in opportunity_by_sector.items()
        if _direction_can_surface_recommendations(opportunity)
    ]
    eligible_labels = [sector for sector in ranked_sectors if eligible_by_sector[sector]]
    unmatched_labels = [
        sector for sector in actionable_labels if not eligible_by_sector.get(sector)
    ]
    research_labels = [
        sector for sector in opportunity_by_sector if sector not in set(eligible_labels)
    ]
    conditional_wait_codes = [
        item["fund_code"]
        for item in candidate_decisions
        if item["status"] == "conditional_wait" and item["fund_code"]
    ]
    watch_only_codes = [
        item["fund_code"]
        for item in candidate_decisions
        if item["status"] == "watch_only" and item["fund_code"]
    ]
    return {
        "schema_version": RECOMMENDATION_SCOPE_VERSION,
        "policy_enforced": True,
        "max_recommendations": MAX_DISCOVERY_RECOMMENDATIONS,
        "ordered_eligible_fund_codes": ordered_codes,
        "alternate_eligible_fund_codes": alternate_codes,
        "maximum_recommendations_per_sector": MAX_RECOMMENDATIONS_PER_SECTOR,
        "actionable_sector_labels": actionable_labels,
        "eligible_sector_labels": eligible_labels,
        "unmatched_actionable_sector_labels": unmatched_labels,
        "research_sector_labels": research_labels,
        "sector_funnel": funnel,
        "candidate_decisions": candidate_decisions,
        "conditional_wait_fund_codes": conditional_wait_codes,
        "watch_only_fund_codes": watch_only_codes,
        "instruction": (
            "candidate_pool 仅保留通过方向动作边界、基金质量、载体质量与板块身份门槛的推荐白名单；"
            "每个方向最多推荐两个综合质量最优的独立基金家族；等待/研究方向不得占用推荐名额，也不得跨方向补位。"
        ),
    }


def _candidate_decision(
    candidate: Mapping[str, Any],
    *,
    entry_path: str | None,
    reason_codes: Sequence[str],
) -> dict[str, Any]:
    fund_reasons = [
        reason
        for reason in reason_codes
        if reason not in {"direction_entry_not_open", "direction_evidence_unavailable"}
    ]
    direction_reasons = [
        reason
        for reason in reason_codes
        if reason in {"direction_entry_not_open", "direction_evidence_unavailable"}
    ]
    status = (
        "watch_only"
        if fund_reasons
        else "conditional_wait"
        if direction_reasons or entry_path is None
        else "actionable"
    )
    return {
        "fund_code": _fund_code(candidate),
        "fund_name": str(candidate.get("fund_name") or ""),
        "sector_label": str(candidate.get("sector_label") or "").strip(),
        "status": status,
        "entry_path": entry_path,
        "fund_gates_passed": not fund_reasons,
        "direction_gate_passed": entry_path is not None,
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }


def ensure_recommendation_candidate_scope(
    discovery_facts: dict[str, Any],
    candidate_pool: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    existing = discovery_facts.get("recommendation_candidate_scope")
    if (
        isinstance(existing, dict)
        and existing.get("schema_version") == RECOMMENDATION_SCOPE_VERSION
    ):
        return existing
    mainline = (
        discovery_facts.get("mainline_snapshot")
        if isinstance(discovery_facts.get("mainline_snapshot"), Mapping)
        else {}
    )
    opportunities = discovery_facts.get("sector_opportunities")
    scope = build_recommendation_candidate_scope(
        candidate_pool,
        opportunities if isinstance(opportunities, list) else [],
        entry_policy_version=str(mainline.get("entry_policy_version") or "") or None,
    )
    discovery_facts["recommendation_candidate_scope"] = scope
    return scope


def project_candidate_decisions_for_report(report: dict[str, Any]) -> dict[str, Any]:
    """Add v2 display decisions to immutable v1 reports without rewriting them.

    The persisted whitelist and historical decision events remain untouched.
    This projection only fills the three candidate-pool display buckets, so a
    report created before v2 no longer renders the misleading 0 / 0 / 0 state.
    """

    facts = report.get("discovery_facts")
    if not isinstance(facts, dict):
        return report
    existing = facts.get("recommendation_candidate_scope")
    if not isinstance(existing, Mapping) or existing.get("policy_enforced") is not True:
        return report
    if isinstance(existing.get("candidate_decisions"), list):
        return report
    pool = report.get("candidate_pool")
    opportunities = facts.get("sector_opportunities")
    if not isinstance(pool, list) or not isinstance(opportunities, list):
        return report
    mainline = facts.get("mainline_snapshot")
    rebuilt = build_recommendation_candidate_scope(
        [item for item in pool if isinstance(item, Mapping)],
        [item for item in opportunities if isinstance(item, Mapping)],
        entry_policy_version=(
            str(mainline.get("entry_policy_version") or "") or None
            if isinstance(mainline, Mapping)
            else None
        ),
    )
    if rebuilt.get("policy_enforced") is not True:
        return report
    projected = dict(existing)
    for key in (
        "candidate_decisions",
        "conditional_wait_fund_codes",
        "watch_only_fund_codes",
    ):
        projected[key] = rebuilt.get(key) or []
    projected["candidate_decision_projection"] = "read_time_compatibility_v1"
    facts["recommendation_candidate_scope"] = projected
    return report


def candidates_in_recommendation_scope(
    candidate_pool: Sequence[Mapping[str, Any]],
    scope: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    if scope.get("policy_enforced") is not True:
        return list(candidate_pool)
    by_code = {_fund_code(item): item for item in candidate_pool if _fund_code(item)}
    return [
        by_code[code]
        for raw in scope.get("ordered_eligible_fund_codes") or []
        if (code := _normalize_code(raw)) in by_code
    ]


def reconcile_recommendations_with_scope(
    recommendations: Sequence[DiscoveryRecommendation],
    *,
    candidate_pool: Sequence[Mapping[str, Any]],
    discovery_facts: dict[str, Any],
    max_recommendations: int = MAX_DISCOVERY_RECOMMENDATIONS,
) -> tuple[list[DiscoveryRecommendation], list[str]]:
    """Drop out-of-scope model picks and deterministically backfill the whitelist."""

    scope = ensure_recommendation_candidate_scope(discovery_facts, candidate_pool)
    if scope.get("policy_enforced") is not True:
        return list(recommendations), []

    pool_by_code = {
        _fund_code(item): item for item in candidate_pool if _fund_code(item)
    }
    ordered_allowed = [
        code
        for raw in scope.get("ordered_eligible_fund_codes") or []
        if (code := _normalize_code(raw)) in pool_by_code
    ]
    allowed = set(ordered_allowed)
    allowed_by_sector: dict[str, list[str]] = defaultdict(list)
    for code in ordered_allowed:
        sector = str(pool_by_code[code].get("sector_label") or "").strip()
        allowed_by_sector[sector].append(code)

    selected: list[DiscoveryRecommendation] = []
    selected_codes: set[str] = set()
    dropped_codes: list[str] = []
    backfilled_codes: list[str] = []
    model_codes: list[str] = []

    def add_code(code: str, *, template: DiscoveryRecommendation | None = None) -> bool:
        if code not in allowed or code in selected_codes or len(selected) >= max_recommendations:
            return False
        candidate = pool_by_code[code]
        if template is None:
            recommendation = _backfill_recommendation(candidate)
        else:
            recommendation = template.model_copy(deep=True)
            recommendation.fund_code = code
            recommendation.fund_name = str(candidate.get("fund_name") or code)
            recommendation.sector_name = str(candidate.get("sector_label") or "")
        selected.append(recommendation)
        selected_codes.add(code)
        return True

    for recommendation in recommendations:
        code = _normalize_code(recommendation.fund_code)
        if code:
            model_codes.append(code)
        if code in allowed:
            add_code(code, template=recommendation)
            continue
        if code:
            dropped_codes.append(code)
        source = pool_by_code.get(code)
        source_sector = str((source or {}).get("sector_label") or recommendation.sector_name or "").strip()
        replacement = next(
            (
                replacement_code
                for replacement_code in allowed_by_sector.get(source_sector, [])
                if replacement_code not in selected_codes
            ),
            None,
        )
        if replacement and add_code(replacement):
            backfilled_codes.append(replacement)

    for code in ordered_allowed:
        if len(selected) >= max_recommendations:
            break
        if add_code(code):
            backfilled_codes.append(code)

    audit = {
        "schema_version": "discovery_recommendation_scope_reconciliation.v3",
        "model_fund_codes": model_codes,
        "dropped_fund_codes": list(dict.fromkeys(dropped_codes)),
        "backfilled_fund_codes": list(dict.fromkeys(backfilled_codes)),
        "final_fund_codes": [item.fund_code for item in selected],
        "cross_direction_fallback_allowed": False,
        "maximum_recommendations_per_sector": MAX_RECOMMENDATIONS_PER_SECTOR,
    }
    discovery_facts["recommendation_scope_reconciliation"] = audit
    caveats: list[str] = []
    if audit["dropped_fund_codes"] or audit["backfilled_fund_codes"]:
        caveats.append(
            "系统已按可布局方向与基金硬门槛校正候选；等待方向未用于凑数，空缺只从同方向或其他可布局方向补选。"
        )
    return selected, caveats


def _candidate_entry_path(
    candidate: Mapping[str, Any],
    opportunity: Mapping[str, Any],
) -> str | None:
    if opportunity.get("opportunity_available") is False:
        return None
    if str(opportunity.get("entry_state") or "") == ENTRY_READY_TO_START:
        return "confirmed_entry"
    if fund_recovery_overrides_sector_position(candidate, opportunity):
        return "fund_position_recovery"
    if fund_entry_opens_v3_improving_flow_probe(candidate, opportunity):
        return "flow_improving_probe"
    if fund_entry_opens_v3_probability_probe(candidate, opportunity):
        return "probability_early_probe"
    return None


def _fund_gate_reasons(candidate: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    code = _fund_code(candidate)
    if not code:
        reasons.append("invalid_fund_code")
    quality_gate = candidate.get("quality_gate")
    quality_status = (
        str(quality_gate.get("status") or "")
        if isinstance(quality_gate, Mapping)
        else ""
    )
    if quality_status != "eligible":
        reasons.append("quality_gate_not_eligible")
    if str(candidate.get("vehicle_quality_status") or "") != "eligible":
        reasons.append("vehicle_quality_not_eligible")
    if not candidate_sector_identity_is_executable(candidate):
        mismatch = candidate.get("sector_identity_mismatch")
        reasons.append(
            "sector_identity_mismatch"
            if isinstance(mismatch, Mapping)
            else "sector_identity_not_verified"
        )
    return reasons


def _candidate_rank_key(
    candidate: Mapping[str, Any],
    entry_path: str,
    opportunity: Mapping[str, Any],
) -> tuple[float, ...]:
    nav = candidate.get("nav_trend") if isinstance(candidate.get("nav_trend"), Mapping) else {}
    signal = (
        candidate.get("fund_entry_signal")
        if isinstance(candidate.get("fund_entry_signal"), Mapping)
        else {}
    )
    return (
        float(_ENTRY_PATH_PRIORITY.get(entry_path, 0)),
        _combined_fund_quality_score(candidate),
        _num(candidate.get("fund_quality_score")) or -999.0,
        _num(candidate.get("vehicle_quality_score")) or -999.0,
        _num(candidate.get("opportunity_score_20_60d")) or -999.0,
        1.0 if signal.get("entry_ready") is True else 0.0,
        1.0 if signal.get("early_probe_ready") is True else 0.0,
        _num(nav.get("annualized_volatility_20d_percent")) or -999.0,
        _num(opportunity.get("selection_priority_score")) or -999.0,
    )


def _combined_fund_quality_score(candidate: Mapping[str, Any]) -> float:
    scores = [
        score
        for raw in (
            candidate.get("fund_quality_score"),
            candidate.get("vehicle_quality_score"),
        )
        if (score := _num(raw)) is not None
    ]
    return sum(scores) / len(scores) if scores else -999.0


def _sector_rank_key(
    sector: str,
    rows: Sequence[tuple[Mapping[str, Any], str]],
    opportunity: Mapping[str, Any],
) -> tuple[float, ...]:
    best_path = max(
        (_ENTRY_PATH_PRIORITY.get(path, 0) for _candidate, path in rows),
        default=0,
    )
    return (
        float(best_path),
        _num(opportunity.get("selection_priority_score"))
        or _num(opportunity.get("score"))
        or -999.0,
        _num(opportunity.get("trend_formation_probability")) or -999.0,
        float(len(rows)),
    )


def _direction_path(opportunity: Mapping[str, Any]) -> str:
    if str(opportunity.get("entry_state") or "") == ENTRY_READY_TO_START:
        return "confirmed_entry"
    if opportunity.get("probability_early_probe_eligible") is True:
        return "probability_early_probe"
    if opportunity.get("flow_improving_probe_eligible") is True:
        return "flow_improving_probe"
    return "research_only"


def _direction_can_surface_recommendations(opportunity: Mapping[str, Any]) -> bool:
    return bool(
        opportunity.get("opportunity_available") is not False
        and (
            str(opportunity.get("entry_state") or "") == ENTRY_READY_TO_START
            or opportunity.get("probability_early_probe_eligible") is True
            or opportunity.get("flow_improving_probe_eligible") is True
        )
    )


def _backfill_recommendation(candidate: Mapping[str, Any]) -> DiscoveryRecommendation:
    sector = str(candidate.get("sector_label") or "")
    return DiscoveryRecommendation(
        fund_code=_fund_code(candidate),
        fund_name=str(candidate.get("fund_name") or _fund_code(candidate)),
        sector_name=sector,
        action="建议关注",
        confidence="中",
        decision_path=(
            f"先确认{sector}进入可布局范围，再在该方向内按基金质量、板块身份与净值修复信号补选。"
        ),
        points=["系统按可布局方向白名单与基金硬门槛自动补选该候选。"],
        risks=["若方向趋势、资金参与度或基金自身修复失效，应停止新增并重新评估。"],
        validation_notes=["该候选由服务端确定性补选，最终动作与金额仍须通过完整交易守卫。"],
    )


def _fund_code(candidate: Mapping[str, Any]) -> str:
    return _normalize_code(candidate.get("fund_code"))


def _normalize_code(value: object) -> str:
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 6:
        return ""
    return text.zfill(6)


def _num(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None
