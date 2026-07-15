from __future__ import annotations

from collections import Counter

from app.models import AnalysisRequest, FundRecommendation, Holding, InvestorProfile
from app.services.recommendations import suggest_trade_amount
from app.services.risk import holding_weight_percent, resolve_weight_denominator


def simulate_rebalance(
    request: AnalysisRequest,
    fund_recommendations: list[FundRecommendation],
) -> dict:
    total_amount = sum(holding.holding_amount for holding in request.holdings) or 1.0
    weight_denominator = resolve_weight_denominator(request.holdings, request.profile) or 1.0
    recommendations_by_holding = _bind_recommendations_to_holdings(
        request.holdings,
        fund_recommendations,
    )
    rows: list[dict] = []
    simulated_total = 0.0

    for holding, rec in zip(request.holdings, recommendations_by_holding, strict=True):
        weight = holding_weight_percent(holding, request.holdings, request.profile)
        action = rec.action if rec else "观察"
        amount_yuan = rec.amount_yuan if rec else None
        amount_note = rec.amount_note if rec else None

        if amount_yuan is None:
            suggested_yuan, suggested_note = suggest_trade_amount(
                holding,
                weight,
                weight_denominator,
                request.profile,
                action,
            )
            if amount_yuan is None and suggested_yuan is not None:
                amount_yuan = suggested_yuan
            if amount_note is None and suggested_note is not None:
                amount_note = suggested_note

        delta = _delta_for_action(
            action,
            amount_yuan,
            weight_percent=weight,
            concentration_limit=request.profile.concentration_limit_percent,
        )
        new_amount = max(holding.holding_amount + delta, 0.0)
        simulated_total += new_amount
        rows.append(
            {
                "fund_code": holding.fund_code,
                "fund_name": holding.fund_name,
                "action": action,
                "current_amount": round(holding.holding_amount, 2),
                "delta_yuan": round(delta, 2),
                "simulated_amount": round(new_amount, 2),
                "current_weight_percent": round(weight, 2),
                "amount_note": amount_note,
            }
        )

    rebased_total = simulated_total or 1.0
    for row in rows:
        row["simulated_weight_percent"] = round(row["simulated_amount"] / rebased_total * 100, 2)
        row["weight_delta_percent"] = round(
            row["simulated_weight_percent"] - row["current_weight_percent"],
            2,
        )

    return {
        "assumption": "仅按报告示意金额做算术模拟，不含费率、到账日与申赎限制。",
        "current_total": round(total_amount, 2),
        "simulated_total": round(simulated_total, 2),
        "concentration_limit_percent": request.profile.concentration_limit_percent,
        "rows": rows,
        "warnings": _warnings(rows, request.profile),
    }


def _bind_recommendations_to_holdings(
    holdings: list[Holding],
    recommendations: list[FundRecommendation],
) -> list[FundRecommendation | None]:
    """Bind closed recommendations without collapsing duplicate fund codes.

    The daily recommendation canonicalizer emits one item per holding in the
    authoritative holding order. Keep that stable index when the identity at
    the same position agrees. Historical payloads may fall back to a unique
    real code or a complete exact-identity group, but no recommendation is ever
    broadcast through a lossy ``fund_code -> recommendation`` mapping.
    """

    bound: list[FundRecommendation | None] = [None] * len(holdings)
    consumed_recommendation_indexes: set[int] = set()
    holding_identity_counts = Counter(
        (item.fund_code, item.fund_name) for item in holdings
    )
    recommendation_identity_counts = Counter(
        (item.fund_code, item.fund_name) for item in recommendations
    )

    for holding_index, holding in enumerate(holdings):
        if holding_index >= len(recommendations):
            continue
        recommendation = recommendations[holding_index]
        identity = (holding.fund_code, holding.fund_name)
        if (
            _same_holding_identity(holding, recommendation)
            and holding_identity_counts[identity]
            == recommendation_identity_counts[identity]
        ):
            bound[holding_index] = recommendation
            consumed_recommendation_indexes.add(holding_index)

    holding_code_counts = Counter(item.fund_code for item in holdings)
    recommendation_code_counts = Counter(item.fund_code for item in recommendations)
    for holding_index, holding in enumerate(holdings):
        if bound[holding_index] is not None:
            continue
        if not _is_real_fund_code(holding.fund_code):
            continue
        if (
            holding_code_counts[holding.fund_code] != 1
            or recommendation_code_counts[holding.fund_code] != 1
        ):
            continue
        for recommendation_index, recommendation in enumerate(recommendations):
            if recommendation_index in consumed_recommendation_indexes:
                continue
            if recommendation.fund_code != holding.fund_code:
                continue
            bound[holding_index] = recommendation
            consumed_recommendation_indexes.add(recommendation_index)
            break

    for holding_index, holding in enumerate(holdings):
        if bound[holding_index] is not None:
            continue
        matching_recommendation_indexes = [
            recommendation_index
            for recommendation_index, recommendation in enumerate(recommendations)
            if recommendation_index not in consumed_recommendation_indexes
            and _same_holding_identity(holding, recommendation)
        ]
        matching_unbound_holding_count = sum(
            1
            for candidate_index, candidate in enumerate(holdings)
            if bound[candidate_index] is None
            and candidate.fund_code == holding.fund_code
            and candidate.fund_name == holding.fund_name
        )
        if len(matching_recommendation_indexes) != matching_unbound_holding_count:
            continue
        recommendation_index = matching_recommendation_indexes[0]
        bound[holding_index] = recommendations[recommendation_index]
        consumed_recommendation_indexes.add(recommendation_index)

    return bound


def _same_holding_identity(
    holding: Holding,
    recommendation: FundRecommendation,
) -> bool:
    return (
        recommendation.fund_code == holding.fund_code
        and recommendation.fund_name == holding.fund_name
    )


def _is_real_fund_code(code: str) -> bool:
    return code != "000000" and len(code) == 6 and code.isdigit()


def _delta_for_action(
    action: str,
    amount_yuan: float | None,
    *,
    weight_percent: float | None = None,
    concentration_limit: float | None = None,
) -> float:
    amount = amount_yuan or 0.0
    if amount <= 0:
        return 0.0
    if any(token in action for token in ("减仓", "复核")):
        return -abs(amount)
    if any(token in action for token in ("加仓", "定投", "分批")):
        return abs(amount)
    if (
        weight_percent is not None
        and concentration_limit is not None
        and weight_percent > concentration_limit
    ):
        return -abs(amount)
    return 0.0


def _warnings(rows: list[dict], profile: InvestorProfile) -> list[str]:
    warnings: list[str] = []
    for row in rows:
        if row["simulated_weight_percent"] > profile.concentration_limit_percent:
            warnings.append(
                f"{row['fund_name']} 模拟后占比 {row['simulated_weight_percent']:.1f}% "
                f"仍高于上限 {profile.concentration_limit_percent:.0f}%。"
            )
    return warnings
