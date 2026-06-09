from __future__ import annotations

from app.models import AnalysisRequest, FundRecommendation, InvestorProfile
from app.services.recommendations import suggest_trade_amount
from app.services.risk import holding_weight_percent, resolve_weight_denominator


def simulate_rebalance(
    request: AnalysisRequest,
    fund_recommendations: list[FundRecommendation],
) -> dict:
    total_amount = sum(holding.holding_amount for holding in request.holdings) or 1.0
    weight_denominator = resolve_weight_denominator(request.holdings, request.profile) or 1.0
    rec_by_code = {rec.fund_code: rec for rec in fund_recommendations}
    rows: list[dict] = []
    simulated_total = 0.0

    for holding in request.holdings:
        weight = holding_weight_percent(holding, request.holdings, request.profile)
        rec = rec_by_code.get(holding.fund_code)
        action = rec.action if rec else "观察"
        amount_yuan = rec.amount_yuan if rec else None
        amount_note = rec.amount_note if rec else None

        if amount_yuan is None and rec is None:
            amount_yuan, amount_note = suggest_trade_amount(
                holding,
                weight,
                weight_denominator,
                request.profile,
                action,
            )

        delta = _delta_for_action(action, amount_yuan)
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


def _delta_for_action(action: str, amount_yuan: float | None) -> float:
    amount = amount_yuan or 0.0
    if any(token in action for token in ("减仓", "复核")):
        return -abs(amount)
    if any(token in action for token in ("加仓", "定投", "分批")):
        return abs(amount)
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
