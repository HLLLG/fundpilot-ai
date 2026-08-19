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
from app.services.sector_labels import normalize_sector_label
from app.services.sector_opportunity_scoring import (
    ENTRY_READY_TO_START,
    MATURITY_POLICY_VERSIONS,
)
from app.services.sector_registry import resolve_theme_sector_label

RECOMMENDATION_SCOPE_VERSION = "discovery_recommendation_scope.2026-08.v7"
# 一次扫描最多给出四个买入建议，且每个方向只保留质量最好的那一只。六个建议里
# 有一半是同方向的第二只载体，用户拿到的其实是重复的方向暴露，而不是四个独立
# 判断；收敛到"每方向最优 1 只"后，剩余同方向候选仍留在 alternate 名单里可查。
MAX_DISCOVERY_RECOMMENDATIONS = 4
MAX_RECOMMENDATIONS_PER_SECTOR = 1
# 方向身份保持独立（黄金 ≠ 黄金股），只在「可布局方向没有过门载体」时
# 借用同主题可交易基金执行，并在白名单里写明回退来源。
# 组合里已经有黄金敞口时，不得再把黄金股回退说成「缺少过门载体」。
THEME_VEHICLE_FALLBACKS: tuple[tuple[str, str], ...] = (("黄金", "黄金股"),)

_ENTRY_PATH_PRIORITY = {
    "confirmed_entry": 4,
    "fund_position_recovery": 3,
    "theme_vehicle_fallback": 3,
    "flow_improving_probe": 2,
    "probability_early_probe": 1,
}


def build_recommendation_candidate_scope(
    candidate_pool: Sequence[Mapping[str, Any]],
    sector_opportunities: Sequence[Mapping[str, Any]],
    *,
    entry_policy_version: str | None = None,
    held_sector_labels: Sequence[str] | None = None,
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

    theme_vehicle_fallbacks = _apply_theme_vehicle_fallbacks(
        eligible_by_sector=eligible_by_sector,
        candidates_by_sector=candidates_by_sector,
        opportunity_by_sector=opportunity_by_sector,
        candidate_decisions=candidate_decisions,
        funnel=funnel,
        held_sector_labels=held_sector_labels,
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
    # Keep at most ``MAX_RECOMMENDATIONS_PER_SECTOR`` independently selected fund
    # families per direction.  The depth loop walks every open sector once before
    # it adds a second vehicle anywhere, so the global cap is spent on distinct
    # directions first.  With the per-sector cap at 1 this collapses to "one best
    # vehicle per direction", ranked by direction, then truncated to the global cap.
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
        "theme_vehicle_fallbacks": theme_vehicle_fallbacks,
        "instruction": (
            "candidate_pool 仅保留通过方向动作边界、基金质量、载体质量与板块身份门槛的推荐白名单；"
            f"每个方向只推荐 {MAX_RECOMMENDATIONS_PER_SECTOR} 个综合质量最优的独立基金家族，"
            f"全局最多 {MAX_DISCOVERY_RECOMMENDATIONS} 只；"
            "等待/研究方向不得占用推荐名额，也不得跨方向补位；"
            "黄金等方向缺少过门载体时，可用同主题可交易基金回退，板块身份仍保持独立；"
            "组合已有黄金敞口时，不得再把黄金股当作黄金回退开仓。"
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
        held_sector_labels=held_sector_labels_from_discovery_facts(discovery_facts),
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
        held_sector_labels=held_sector_labels_from_discovery_facts(facts),
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
        fallback = None
        fallbacks = scope.get("theme_vehicle_fallbacks")
        if isinstance(fallbacks, Mapping):
            raw_fallback = fallbacks.get(code)
            fallback = raw_fallback if isinstance(raw_fallback, Mapping) else None
        if template is None:
            recommendation = _backfill_recommendation(candidate, fallback=fallback)
        else:
            recommendation = template.model_copy(deep=True)
            recommendation.fund_code = code
            recommendation.fund_name = str(candidate.get("fund_name") or code)
            recommendation.sector_name = str(candidate.get("sector_label") or "")
            if fallback:
                _annotate_theme_vehicle_fallback(recommendation, fallback)
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


def companion_vehicle_sector_labels(
    target_sectors: Sequence[str],
    sector_opportunities: Sequence[Mapping[str, Any]] | None = None,
) -> list[str]:
    """可布局方向若可能缺载体，把同主题执行板块一并纳入召回，不合并方向身份。"""

    actionable = {
        str(item.get("sector_label") or "").strip()
        for item in (sector_opportunities or [])
        if isinstance(item, Mapping) and _direction_can_surface_recommendations(item)
    }
    existing = {str(label or "").strip() for label in target_sectors if str(label or "").strip()}
    extra: list[str] = []
    for thesis, vehicle in THEME_VEHICLE_FALLBACKS:
        if thesis in existing and thesis in actionable and vehicle not in existing:
            extra.append(vehicle)
    return extra


def resolve_theme_vehicle_fallback_opportunity(
    *,
    fund_code: str,
    vehicle_sector: str,
    opportunity_by_sector: Mapping[str, Mapping[str, Any]],
    theme_vehicle_fallbacks: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """执行守卫用：回退基金仍保留载体板块名，入场授权改看主题方向。"""

    code = _normalize_code(fund_code)
    if not code or not isinstance(theme_vehicle_fallbacks, Mapping):
        return None
    meta = theme_vehicle_fallbacks.get(code)
    if not isinstance(meta, Mapping):
        return None
    thesis = str(meta.get("thesis_sector_label") or "").strip()
    expected_vehicle = str(meta.get("vehicle_sector_label") or "").strip()
    if not thesis or (expected_vehicle and expected_vehicle != vehicle_sector):
        return None
    thesis_opportunity = opportunity_by_sector.get(thesis)
    if not isinstance(thesis_opportunity, Mapping):
        return None
    return {
        **dict(thesis_opportunity),
        "theme_vehicle_fallback": True,
        "vehicle_sector_label": vehicle_sector,
        "thesis_sector_label": thesis,
    }


def held_sector_labels_from_discovery_facts(
    discovery_facts: Mapping[str, Any] | None,
) -> list[str]:
    """Collect holding sector labels from discovery portfolio facts."""

    if not isinstance(discovery_facts, Mapping):
        return []
    gap = discovery_facts.get("portfolio_gap")
    if not isinstance(gap, Mapping):
        return []
    labels: list[str] = []
    for key in ("held_sectors", "holdings_slim"):
        rows = gap.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            label = str(row.get("sector_name") or row.get("sector_label") or "").strip()
            if label:
                labels.append(label)
    return list(dict.fromkeys(labels))


def theme_pair_conflict_label(
    candidate_sector: str,
    held_sector_labels: Sequence[str] | None,
    *,
    fallback_thesis: str | None = None,
) -> str | None:
    """If holdings already express this gold-theme pair, return the held label."""

    held_keys = _theme_keys_from_labels(held_sector_labels)
    if not held_keys:
        return None
    candidate_keys = _theme_keys(candidate_sector)
    if fallback_thesis:
        candidate_keys |= _theme_keys(fallback_thesis)
    for thesis, vehicle in THEME_VEHICLE_FALLBACKS:
        pair_keys = _theme_keys(thesis) | _theme_keys(vehicle)
        if not (candidate_keys & pair_keys):
            continue
        overlap = held_keys & pair_keys
        if overlap:
            return sorted(overlap)[0]
    return None


def _theme_keys(label: str | None) -> set[str]:
    raw = normalize_sector_label(label)
    keys = {raw} if raw else set()
    theme = resolve_theme_sector_label(label)
    if theme:
        keys.add(normalize_sector_label(theme))
    return {key for key in keys if key}


def _theme_keys_from_labels(labels: Sequence[str] | None) -> set[str]:
    keys: set[str] = set()
    for label in labels or []:
        keys.update(_theme_keys(str(label)))
    return keys


def _theme_pair_occupied(
    thesis: str,
    vehicle: str,
    held_sector_labels: Sequence[str] | None,
) -> bool:
    return theme_pair_conflict_label(vehicle, held_sector_labels, fallback_thesis=thesis) is not None


def _apply_theme_vehicle_fallbacks(
    *,
    eligible_by_sector: dict[str, list[tuple[Mapping[str, Any], str]]],
    candidates_by_sector: Mapping[str, Sequence[Mapping[str, Any]]],
    opportunity_by_sector: Mapping[str, Mapping[str, Any]],
    candidate_decisions: list[dict[str, Any]],
    funnel: list[dict[str, Any]],
    held_sector_labels: Sequence[str] | None = None,
) -> dict[str, dict[str, str]]:
    fallbacks: dict[str, dict[str, str]] = {}
    decisions_by_code = {
        str(item.get("fund_code") or ""): item
        for item in candidate_decisions
        if item.get("fund_code")
    }
    for thesis, vehicle in THEME_VEHICLE_FALLBACKS:
        thesis_opportunity = opportunity_by_sector.get(thesis)
        if not isinstance(thesis_opportunity, Mapping):
            continue
        if not _direction_can_surface_recommendations(thesis_opportunity):
            continue
        if _theme_pair_occupied(thesis, vehicle, held_sector_labels):
            continue
        if eligible_by_sector.get(thesis):
            continue
        if eligible_by_sector.get(vehicle):
            continue
        attached: list[tuple[Mapping[str, Any], str]] = []
        for candidate in candidates_by_sector.get(vehicle, []):
            if _fund_gate_reasons(candidate):
                continue
            code = _fund_code(candidate)
            if not code:
                continue
            attached.append((candidate, "theme_vehicle_fallback"))
            fallbacks[code] = {
                "thesis_sector_label": thesis,
                "vehicle_sector_label": vehicle,
                "entry_path": "theme_vehicle_fallback",
            }
            decision = decisions_by_code.get(code)
            if decision is not None:
                decision["status"] = "actionable"
                decision["entry_path"] = "theme_vehicle_fallback"
                decision["direction_gate_passed"] = True
                decision["theme_vehicle_fallback"] = True
                decision["reason_codes"] = [
                    reason
                    for reason in decision.get("reason_codes") or []
                    if reason != "direction_entry_not_open"
                ]
        if not attached:
            continue
        attached.sort(
            key=lambda pair: _candidate_rank_key(pair[0], pair[1], thesis_opportunity),
            reverse=True,
        )
        eligible_by_sector[thesis] = attached
        for row in funnel:
            if row.get("sector_label") != thesis:
                continue
            row["eligible_count"] = len(attached)
            row["theme_vehicle_fallback_from"] = vehicle
            row["rejected_count"] = max(0, int(row.get("recalled_count") or 0) - len(attached))
    return fallbacks


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


def _backfill_recommendation(
    candidate: Mapping[str, Any],
    *,
    fallback: Mapping[str, Any] | None = None,
) -> DiscoveryRecommendation:
    sector = str(candidate.get("sector_label") or "")
    recommendation = DiscoveryRecommendation(
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
    if fallback:
        _annotate_theme_vehicle_fallback(recommendation, fallback)
    return recommendation


def _annotate_theme_vehicle_fallback(
    recommendation: DiscoveryRecommendation,
    fallback: Mapping[str, Any],
) -> None:
    thesis = str(fallback.get("thesis_sector_label") or "").strip()
    vehicle = str(fallback.get("vehicle_sector_label") or recommendation.sector_name).strip()
    if not thesis or not vehicle:
        return
    recommendation.entry_path = "theme_vehicle_fallback"
    recommendation.decision_path = (
        f"{thesis}方向可布局但缺少过门载体，本次用同主题可交易的{vehicle}基金执行；"
        f"板块身份仍是{vehicle}，不是把{thesis}与{vehicle}合成一个方向。"
    )
    note = (
        f"{thesis}缺少过门载体，本次回退到{vehicle}可交易基金；"
        "两者方向身份保持独立，只共享执行席。"
    )
    if note not in recommendation.points:
        recommendation.points = [note, *recommendation.points]
    validation = "该候选是同主题载体回退，不是合并两个板块身份。"
    if validation not in recommendation.validation_notes:
        recommendation.validation_notes = [
            validation,
            *recommendation.validation_notes,
        ]


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
