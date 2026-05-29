from __future__ import annotations

from app.models import Holding, InvestorProfile, RiskAlert, RiskAssessment


def evaluate_portfolio_risk(
    holdings: list[Holding],
    profile: InvestorProfile,
) -> RiskAssessment:
    total_amount = sum(holding.holding_amount for holding in holdings)
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

    if total_amount > 0:
        for holding in holdings:
            weight = holding.holding_amount / total_amount * 100
            if weight > profile.concentration_limit_percent:
                alerts.append(
                    RiskAlert(
                        code="CONCENTRATION",
                        severity="medium",
                        message=(
                            f"{holding.fund_name} 当前占比 {weight:.1f}%，"
                            f"超过 {profile.concentration_limit_percent:.1f}% 集中度上限。"
                        ),
                        evidence=f"{holding.holding_amount:.2f} / {total_amount:.2f}",
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
