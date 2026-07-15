from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from math import isfinite
from typing import Any

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_allocation_risk import build_discovery_risk_context
from app.services.discovery_allocator import allocate_discovery_candidates
from app.services.discovery_guard import finalize_discovery_allocation_projection
from app.services.discovery_strategy import (
    discovery_horizon_label,
    discovery_minimum_holding_days,
    strategy_from_facts,
)
from app.services.fund_tradeability import (
    assess_tradeability_for_amount,
    build_tradeability_gate,
)


def prepare_recommendations_for_deterministic_allocation(
    recommendations: Sequence[DiscoveryRecommendation],
    *,
    candidate_pool: Sequence[Mapping[str, Any]],
) -> list[DiscoveryRecommendation]:
    """Erase every model amount and provide only a guard-validation probe.

    The legacy guard still expects a positive amount to retain a proposed buy.
    We therefore probe each candidate at its deterministic initial minimum and
    later replace that probe with the multi-candidate allocation. Sorting by
    fund code also prevents model list order from influencing legacy sequential
    checks while the migration remains in place.
    """

    by_code = {
        str(item.get("fund_code") or "").zfill(6): item
        for item in candidate_pool
        if isinstance(item, Mapping)
    }
    prepared: list[DiscoveryRecommendation] = []
    for recommendation in recommendations:
        copy = recommendation.model_copy(deep=True)
        code = copy.fund_code.strip().zfill(6)
        pool_item = by_code.get(code) or {}
        tradeability = (
            pool_item.get("tradeability")
            if isinstance(pool_item.get("tradeability"), Mapping)
            else None
        )
        gate = build_tradeability_gate(tradeability)
        probe = _positive_number(
            gate.get("effective_initial_min_purchase_yuan")
        )
        # Invalid/missing transaction evidence is rejected by the guard. The
        # neutral fallback exists only so a model's null/hostile amount cannot
        # decide whether a semantically valid buy proposal survives to it.
        copy.suggested_amount_yuan = probe or 100.0
        copy.amount_note = None
        prepared.append(copy)
    return sorted(prepared, key=lambda item: item.fund_code.strip().zfill(6))


def apply_deterministic_discovery_allocation(
    recommendations: Sequence[DiscoveryRecommendation],
    *,
    candidate_pool: Sequence[Mapping[str, Any]],
    discovery_facts: dict[str, Any],
    profile: InvestorProfile,
    budget_yuan: float,
    decision_at: datetime | None,
) -> tuple[list[DiscoveryRecommendation], dict[str, Any], dict[str, Any], list[str]]:
    """Allocate one verified tranche and project it back onto recommendations."""

    pool_by_code = {
        str(item.get("fund_code") or "").zfill(6): dict(item)
        for item in candidate_pool
        if isinstance(item, Mapping)
    }
    discovery_strategy = strategy_from_facts(discovery_facts)
    strategy_horizon = discovery_horizon_label(discovery_strategy, profile)
    minimum_holding_days = discovery_minimum_holding_days(
        discovery_strategy,
        profile,
    )
    proposed_buys = [
        item
        for item in recommendations
        if item.action == "分批买入"
        and item.fund_code.strip().zfill(6) in pool_by_code
    ]
    allocator_rows = [
        _allocator_candidate(item, pool_by_code[item.fund_code.strip().zfill(6)])
        for item in proposed_buys
    ]
    portfolio_gap = (
        discovery_facts.get("portfolio_gap")
        if isinstance(discovery_facts.get("portfolio_gap"), Mapping)
        else {}
    )
    holdings_slim = portfolio_gap.get("holdings_slim")
    holdings_for_risk: Sequence[Mapping[str, Any]] = (
        holdings_slim
        if isinstance(holdings_slim, list)
        and all(isinstance(item, Mapping) for item in holdings_slim)
        else []
    )

    if not allocator_rows:
        risk_context = {
            "schema_version": "discovery_risk_context.v1",
            "status": "unqualified",
            "qualified": False,
            "reason_codes": ["no_buy_candidates"],
            "max_drawdown_percent_by_code": {},
            "covariance_by_code": {},
            "positive_correlation_penalty_to_current_holdings_by_code": {},
        }
    else:
        risk_context = build_discovery_risk_context(
            allocator_rows,
            holdings_for_risk,
            decision_at=decision_at,  # type: ignore[arg-type]
        )
        risk_context = apply_reported_holdings_overlap_guard(
            risk_context,
            discovery_facts=discovery_facts,
            candidate_codes=[
                str(row.get("fund_code") or "").strip().zfill(6)
                for row in allocator_rows
            ],
        )

    cash = _confirmed_cash(discovery_facts)
    exposures = _sector_exposures(holdings_slim)
    denominator = _positive_number(portfolio_gap.get("weight_denominator_yuan"))
    if denominator is None:
        denominator = _positive_number(profile.expected_investment_amount)
    active_rows = list(allocator_rows)
    rejected_cost_rows: list[dict[str, Any]] = []
    cost_by_code: dict[str, dict[str, Any]] = {}
    plan: dict[str, Any] = {}
    for _attempt in range(len(active_rows) + 1):
        plan = allocate_discovery_candidates(
            active_rows,
            requested_budget_yuan=budget_yuan,
            confirmed_cash_yuan=cash,
            existing_sector_exposure_yuan=exposures,
            concentration_denominator_yuan=denominator,
            concentration_limit_percent=profile.concentration_limit_percent,
            prefer_dca=profile.prefer_dca,
            decision_style=profile.decision_style,
            risk_context=risk_context,
            priority_inputs=None,
            amount_step_yuan=100,
        )
        invalid_cost_codes: set[str] = set()
        for allocation in plan.get("allocations") or []:
            code = str(allocation.get("fund_code") or "").zfill(6)
            amount = _positive_number(allocation.get("suggested_amount_yuan"))
            source = pool_by_code.get(code) or {}
            tradeability = (
                source.get("tradeability")
                if isinstance(source.get("tradeability"), Mapping)
                else None
            )
            assessment = assess_tradeability_for_amount(
                tradeability,
                amount_yuan=amount,
                hold_horizon=(
                    f"荐基策略最短持有期 {minimum_holding_days} 天"
                    if minimum_holding_days is not None
                    else strategy_horizon
                ),
                minimum_holding_days=minimum_holding_days,
            )
            cost_by_code[code] = assessment
            if assessment.get("executable") is not True:
                invalid_cost_codes.add(code)
                rejected_cost_rows.append(
                    {
                        "fund_code": code,
                        "sector_name": allocation.get("sector_name"),
                        "reason_codes": ["final_amount_cost_gate_not_executable"],
                    }
                )
        if not invalid_cost_codes:
            break
        active_rows = [
            row
            for row in active_rows
            if str(row.get("fund_code") or "").zfill(6) not in invalid_cost_codes
        ]

    if rejected_cost_rows:
        existing = list(plan.get("excluded_candidates") or [])
        existing.extend(rejected_cost_rows)
        plan["excluded_candidates"] = sorted(
            existing,
            key=lambda item: str(item.get("fund_code") or ""),
        )

    allocation_rows = {
        str(item.get("fund_code") or "").zfill(6): dict(item)
        for item in plan.get("allocations") or []
        if isinstance(item, Mapping)
    }
    plan_reasons = [
        str(value)
        for value in (plan.get("unallocated_budget") or {}).get("reason_codes") or []
    ]
    risk_reasons = [str(value) for value in risk_context.get("reason_codes") or []]
    projected: list[DiscoveryRecommendation] = []
    caveats: list[str] = []
    for recommendation in recommendations:
        copy = recommendation.model_copy(deep=True)
        code = copy.fund_code.strip().zfill(6)
        if copy.action != "分批买入":
            copy.suggested_amount_yuan = None
            copy.allocation = {}
            # Any cost object left by the legacy guard was calculated against
            # its minimum-amount probe, not an executable final allocation.
            copy.cost_assessment = {}
            projected.append(finalize_discovery_allocation_projection(copy))
            continue
        allocation = allocation_rows.get(code)
        if allocation is None:
            copy.action = "建议关注"
            copy.suggested_amount_yuan = None
            copy.allocation = {}
            copy.cost_assessment = {}
            copy.amount_note = "未通过组合风险与统一金额分配，本次仅保留研究观察。"
            reasons = risk_reasons or plan_reasons or ["未获得满足全部硬约束的首批额度"]
            copy.validation_notes = [
                *copy.validation_notes,
                "确定性分配阻断：" + "、".join(reasons[:3]),
            ]
            projected.append(finalize_discovery_allocation_projection(copy))
            continue

        amount = _positive_number(allocation.get("suggested_amount_yuan"))
        if amount is None:
            copy.action = "建议关注"
            copy.suggested_amount_yuan = None
            copy.allocation = {}
            copy.cost_assessment = {}
            copy.amount_note = "统一分配结果没有可执行正数金额，本次仅保留研究观察。"
            projected.append(finalize_discovery_allocation_projection(copy))
            continue
        copy.suggested_amount_yuan = amount
        copy.allocation = allocation
        copy.cost_assessment = cost_by_code.get(code) or {}
        copy.amount_note = (
            "当前首批金额由系统按已确认现金、总预算、已有板块敞口、"
            "集中度、候选历史回撤、波动、相关性、购买起点和单日限额统一计算；"
            "风险越高首批权重越低，后续批次不预先承诺金额。"
        )
        total_cost = _finite_number(
            copy.cost_assessment.get("estimated_total_cost_upper_bound_percent")
        )
        if total_cost is not None:
            copy.amount_note += f" 按未折扣标准费率估算成本上限约 {total_cost:.2f}%。"
        projected.append(finalize_discovery_allocation_projection(copy))

    order = {
        str(item.get("fund_code") or "").zfill(6): index
        for index, item in enumerate(plan.get("allocations") or [])
        if isinstance(item, Mapping)
    }
    projected.sort(
        key=lambda item: (
            0 if item.fund_code.strip().zfill(6) in order else 1,
            order.get(item.fund_code.strip().zfill(6), 10**9),
            item.fund_code.strip().zfill(6),
        )
    )
    if proposed_buys and not allocation_rows:
        caveats.append(
            "候选未形成合格的组合风险上下文或统一首批额度，系统已清除全部买入金额。"
        )
    elif plan.get("status") == "partial":
        caveats.append("统一分配受交易、现金或集中度上限约束，部分首批预算保持未分配。")
    return projected, plan, risk_context, caveats


def apply_reported_holdings_overlap_guard(
    risk_context: Mapping[str, Any],
    *,
    discovery_facts: Mapping[str, Any],
    candidate_codes: Sequence[str],
) -> dict[str, Any]:
    """Apply qualified disclosed overlap as a one-way concentration penalty.

    Periodic holdings disclosures are never an allocation authorization.  This
    overlay therefore only raises an already-qualified NAV risk penalty; absent,
    zero, cross-vintage, or otherwise ineligible evidence returns an equal risk
    context and can never make a candidate look safer.
    """

    base = dict(risk_context)
    if base.get("status") != "qualified":
        return base
    raw_penalties = base.get(
        "positive_correlation_penalty_to_current_holdings_by_code"
    )
    if not isinstance(raw_penalties, Mapping):
        return base
    lookthrough = discovery_facts.get("fund_lookthrough")
    if not isinstance(lookthrough, Mapping):
        return base
    raw_candidates = lookthrough.get("candidates")
    if not isinstance(raw_candidates, Sequence) or isinstance(
        raw_candidates, (str, bytes)
    ):
        return base

    requested_codes = {
        code
        for code in (_normalized_fund_code(value) for value in candidate_codes)
        if code is not None
    }
    candidates_by_code: dict[str, Mapping[str, Any]] = {}
    duplicate_codes: set[str] = set()
    for raw in raw_candidates:
        if not isinstance(raw, Mapping):
            continue
        code = _normalized_fund_code(raw.get("fund_code"))
        if code is None or code not in requested_codes:
            continue
        if code in candidates_by_code:
            duplicate_codes.add(code)
            continue
        candidates_by_code[code] = raw

    effective_penalties = {
        str(code): value for code, value in raw_penalties.items()
    }
    applied: dict[str, dict[str, Any]] = {}
    for code in sorted(requested_codes):
        if code in duplicate_codes:
            continue
        candidate = candidates_by_code.get(code)
        if candidate is None or candidate.get("status") != "qualified":
            continue
        decision_use = candidate.get("decision_use")
        if not isinstance(decision_use, Mapping):
            continue
        if (
            decision_use.get("concentration_risk_guard_eligible") is not True
            or decision_use.get("allocation_authorization_eligible") is not False
            or candidate.get("overlap_evidence_state")
            != "positive_same_vintage_reported_overlap"
        ):
            continue
        overlap_percent = _finite_number(
            candidate.get("reported_as_of_disclosed_overlap_percent")
        )
        base_penalty = _finite_number(effective_penalties.get(code))
        if (
            overlap_percent is None
            or overlap_percent <= 0
            or overlap_percent > 100
            or base_penalty is None
            or base_penalty < 0
            or base_penalty > 1
        ):
            continue
        disclosed_penalty = round(overlap_percent / 100.0, 8)
        effective_penalty = max(base_penalty, disclosed_penalty)
        if effective_penalty <= base_penalty:
            continue
        effective_penalties[code] = effective_penalty
        applied[code] = {
            "reported_as_of_disclosed_overlap_percent": overlap_percent,
            "base_nav_correlation_penalty": base_penalty,
            "effective_concentration_penalty": effective_penalty,
            "overlap_evidence_state": "positive_same_vintage_reported_overlap",
            "allocation_authorization_eligible": False,
        }

    # Exact no-op is intentional: missing or non-positive evidence must not
    # change either allocation behavior or the persisted risk-context hash.
    if not applied:
        return base

    previous_hash = str(base.get("snapshot_hash") or "").strip() or None
    base[
        "positive_correlation_penalty_to_current_holdings_by_code"
    ] = dict(sorted(effective_penalties.items()))
    base["reported_holdings_overlap_guard"] = {
        "schema_version": "reported_holdings_overlap_guard.v1",
        "policy": "qualified_positive_same_vintage_overlap_can_only_raise_penalty",
        "source_research_hash": lookthrough.get("research_hash"),
        "base_risk_snapshot_hash": previous_hash,
        "applied_by_code": applied,
        "allocation_authorization_eligible": False,
    }
    return _rehash_risk_context(base)


def _rehash_risk_context(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    payload.pop("snapshot_hash", None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    payload["snapshot_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _normalized_fund_code(value: object) -> str | None:
    text = str(value or "").strip()
    return text.zfill(6) if text.isdigit() and 1 <= len(text) <= 6 else None


def _allocator_candidate(
    recommendation: DiscoveryRecommendation,
    pool_item: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(pool_item)
    row["fund_code"] = recommendation.fund_code.strip().zfill(6)
    row["fund_name"] = recommendation.fund_name
    row["sector_name"] = recommendation.sector_name
    quality_gate = row.get("quality_gate")
    row["quality_action"] = (
        quality_gate.get("status") if isinstance(quality_gate, Mapping) else None
    )
    tradeability = row.get("tradeability")
    row["tradeability_gate"] = build_tradeability_gate(
        tradeability if isinstance(tradeability, Mapping) else None
    )
    return row


def _confirmed_cash(facts: Mapping[str, Any]) -> float | None:
    truth = facts.get("portfolio_position_truth")
    if not isinstance(truth, Mapping):
        return None
    cash = truth.get("cash")
    if not isinstance(cash, Mapping) or cash.get("known") is not True:
        return None
    return _nonnegative_number(cash.get("balance_yuan"))


def _sector_exposures(value: Any) -> dict[str, float] | None:
    if not isinstance(value, list):
        return None
    result: dict[str, float] = {}
    for row in value:
        if not isinstance(row, Mapping):
            return None
        sector = " ".join(str(row.get("sector_name") or "").strip().split()).casefold()
        amount = _nonnegative_number(row.get("holding_amount"))
        if not sector or sector in {"unknown", "未知", "未分类"} or amount is None:
            return None
        result[sector] = result.get(sector, 0.0) + amount
    return result


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if isfinite(parsed) else None


def _positive_number(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative_number(value: Any) -> float | None:
    parsed = _finite_number(value)
    return parsed if parsed is not None and parsed >= 0 else None
