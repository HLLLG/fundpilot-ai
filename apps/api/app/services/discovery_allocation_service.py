from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from math import isfinite
from typing import Any

from app.models import DiscoveryRecommendation, InvestorProfile
from app.services.discovery_allocation_risk import build_discovery_risk_context
from app.services.discovery_allocator import allocate_discovery_candidates
from app.services.discovery_guard import finalize_discovery_allocation_projection
from app.services.discovery_selection_strategy import (
    fund_entry_opens_v3_improving_flow_probe,
    fund_entry_opens_v3_probability_probe,
    fund_recovery_overrides_sector_position,
)
from app.services.sector_opportunity_scoring import (
    ENTRY_POLICY_VERSION,
    ENTRY_POLICY_VERSION_V3,
    ENTRY_READY_TO_START,
    MATURITY_POLICY_VERSIONS,
    V3_FIRST_TRANCHE_SCALE_CROWDED,
)


def prepare_recommendations_for_deterministic_allocation(
    recommendations: Sequence[DiscoveryRecommendation],
    *,
    candidate_pool: Sequence[Mapping[str, Any]],
) -> list[DiscoveryRecommendation]:
    """Erase every model amount and provide only a neutral guard probe.

    The legacy guard still expects a positive amount to retain a proposed buy.
    We therefore use one neutral 100-yuan probe and later replace it with the
    multi-candidate advisory allocation. Sorting by
    fund code also prevents model list order from influencing legacy sequential
    checks while the migration remains in place.
    """

    _ = candidate_pool
    prepared: list[DiscoveryRecommendation] = []
    for recommendation in recommendations:
        copy = recommendation.model_copy(deep=True)
        copy.suggested_amount_yuan = 100.0
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
    """Allocate one advisory tranche and project it back onto recommendations."""

    pool_by_code = {
        str(item.get("fund_code") or "").zfill(6): dict(item)
        for item in candidate_pool
        if isinstance(item, Mapping)
    }
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

    cash = _confirmed_cash(discovery_facts)
    exposures = _sector_exposures(holdings_slim)
    denominator = _positive_number(portfolio_gap.get("weight_denominator_yuan"))
    if denominator is None:
        denominator = _positive_number(profile.expected_investment_amount)
    plan = allocate_discovery_candidates(
        allocator_rows,
        requested_budget_yuan=budget_yuan,
        confirmed_cash_yuan=cash,
        existing_sector_exposure_yuan=exposures,
        concentration_denominator_yuan=denominator,
        concentration_limit_percent=profile.concentration_limit_percent,
        prefer_dca=profile.prefer_dca,
        decision_style=profile.decision_style,
        risk_context=risk_context,
        priority_inputs=None,
        current_tranche_ratio_cap=_entry_maturity_tranche_ratio_cap(
            discovery_facts,
            proposed_buys,
        ),
        amount_step_yuan=100,
        require_tradeability_gate=False,
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
        copy.cost_assessment = {}
        copy.amount_note = (
            "首批参考金额由系统按已确认现金、总预算、已有板块敞口、"
            "集中度、候选历史回撤、波动和相关性统一计算；风险越高首批"
            "权重越低，后续批次不预先承诺金额。"
        )
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
        caveats.append("统一分配受现金、风险或集中度上限约束，部分首批预算保持未分配。")
    return projected, plan, risk_context, caveats


def _entry_maturity_tranche_ratio_cap(
    discovery_facts: Mapping[str, Any],
    recommendations: Sequence[DiscoveryRecommendation],
) -> float | None:
    opportunities = discovery_facts.get("sector_opportunities")
    if not isinstance(opportunities, list):
        return None
    maturity_by_sector = {
        str(item.get("sector_label") or "").strip(): item
        for item in opportunities
        if isinstance(item, Mapping)
        and str(item.get("score_policy_version") or "") in MATURITY_POLICY_VERSIONS
        and str(item.get("sector_label") or "").strip()
    }
    if not maturity_by_sector or not recommendations:
        return None
    candidate_pool = discovery_facts.get("candidate_pool")
    pool_by_code = {
        str(item.get("fund_code") or "").zfill(6): item
        for item in candidate_pool
        if isinstance(item, Mapping)
    } if isinstance(candidate_pool, list) else {}

    qualified_rows: list[Mapping[str, Any]] = []
    for recommendation in recommendations:
        sector = str(recommendation.sector_name or "").strip()
        opportunity = maturity_by_sector.get(sector)
        if not isinstance(opportunity, Mapping):
            return None
        raw_ready = str(opportunity.get("entry_state") or "") == ENTRY_READY_TO_START
        candidate = pool_by_code.get(recommendation.fund_code.strip().zfill(6)) or {}
        if (
            not raw_ready
            and not fund_recovery_overrides_sector_position(candidate, opportunity)
            and not fund_entry_opens_v3_improving_flow_probe(candidate, opportunity)
            and not fund_entry_opens_v3_probability_probe(candidate, opportunity)
        ):
            return None
        qualified_rows.append({**dict(opportunity), "candidate": candidate})

    # A newly matured direction starts with at most one fifth of the verified
    # spendable budget. V3 then applies its observed overheat scale to that first
    # tranche. The allocator currently has one portfolio-level cap, so multiple
    # ready directions use the smallest scale rather than letting a hot direction
    # borrow another direction's unscaled budget.
    scales: list[float] = []
    for opportunity in qualified_rows:
        if str(opportunity.get("score_policy_version") or "") != ENTRY_POLICY_VERSION_V3:
            scales.append(1.0)
            continue
        scale = _finite_number(opportunity.get("first_tranche_scale"))
        if scale is None or not 0 < scale <= 1:
            scale = V3_FIRST_TRANCHE_SCALE_CROWDED
        candidate = opportunity.get("candidate")
        fund_signal = (
            candidate.get("fund_entry_signal")
            if isinstance(candidate, Mapping)
            else None
        )
        fund_scale = (
            _finite_number(fund_signal.get("first_tranche_scale"))
            if isinstance(fund_signal, Mapping)
            else None
        )
        if fund_scale is not None and 0 < fund_scale <= 1:
            scale = min(scale, fund_scale)
        scales.append(scale)
    return round(0.20 * min(scales), 4)


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
