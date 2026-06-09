from __future__ import annotations

from app.models import Holding, InvestorProfile, RiskAlert, RiskAssessment


def resolve_weight_denominator(
    holdings: list[Holding],
    profile: InvestorProfile,
) -> float:
    """持仓占比分母：优先用期望投入额（减仓后仍按计划规模算集中度）。"""
    actual_total = sum(holding.holding_amount for holding in holdings)
    expected = profile.expected_investment_amount
    if expected is not None and expected > 0:
        return expected
    return actual_total


def holding_weight_percent(
    holding: Holding,
    holdings: list[Holding],
    profile: InvestorProfile,
) -> float:
    denominator = resolve_weight_denominator(holdings, profile)
    if denominator <= 0:
        return 0.0
    return holding.holding_amount / denominator * 100


def evaluate_portfolio_risk(
    holdings: list[Holding],
    profile: InvestorProfile,
) -> RiskAssessment:
    total_amount = sum(holding.holding_amount for holding in holdings)
    weight_denominator = resolve_weight_denominator(holdings, profile)
    weighted_return = _weighted_return_percent(holdings, total_amount)
    alerts: list[RiskAlert] = []

    if weighted_return <= -abs(profile.max_drawdown_percent):
        alerts.append(
            RiskAlert(
                code="MAX_DRAWDOWN",
                severity="high",
                message=f"组合浮亏 {weighted_return:.2f}% 已触及 {profile.max_drawdown_percent:.1f}% 风险复核线。",
                evidence="按当前录入持仓金额加权计算。",
            )
        )

    if weight_denominator > 0:
        for holding in holdings:
            weight = holding_weight_percent(holding, holdings, profile)
            if weight > profile.concentration_limit_percent:
                evidence = f"{holding.holding_amount:.2f} / {weight_denominator:.2f}"
                if (
                    profile.expected_investment_amount
                    and profile.expected_investment_amount > 0
                    and abs(weight_denominator - total_amount) > 0.01
                ):
                    evidence += f"（期望投入 {profile.expected_investment_amount:.2f}）"
                alerts.append(
                    RiskAlert(
                        code="CONCENTRATION",
                        severity="medium",
                        message=(
                            f"{holding.fund_name} 当前占比 {weight:.1f}%，"
                            f"超过 {profile.concentration_limit_percent:.1f}% 集中度上限。"
                        ),
                        evidence=evidence,
                    )
                )

    high_alerts = [alert for alert in alerts if alert.severity == "high"]
    if high_alerts:
        return RiskAssessment(
            level="high",
            suggested_action="risk_review",
            weighted_return_percent=round(weighted_return, 2),
            alerts=alerts,
        )

    if alerts or holdings:
        return RiskAssessment(
            level="medium",
            suggested_action="watch",
            weighted_return_percent=round(weighted_return, 2),
            alerts=alerts,
        )

    return RiskAssessment(
        level="low",
        suggested_action="watch",
        weighted_return_percent=0,
        alerts=[],
    )


def _weighted_return_percent(holdings: list[Holding], total_amount: float) -> float:
    if total_amount <= 0:
        return 0
    return sum(
        holding.holding_amount * holding.return_percent for holding in holdings
    ) / total_amount
