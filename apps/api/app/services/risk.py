from __future__ import annotations

from app.models import Holding, InvestorProfile, RiskAlert, RiskAssessment
from app.services.holding_estimates import resolve_effective_holding_return_percent
from app.services.holding_profile_batch import (
    MatchedProfilesArg,
    PROFILES_NOT_PROVIDED,
    ProfilesSnapshotArg,
    resolve_matched_profiles,
)


def resolve_weight_denominator(
    holdings: list[Holding],
    profile: InvestorProfile,
    *,
    actual_total: float | None = None,
) -> float:
    """持仓占比分母：优先用期望投入额（减仓后仍按计划规模算集中度）。"""
    if actual_total is None:
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
    *,
    profiles_snapshot: ProfilesSnapshotArg = PROFILES_NOT_PROVIDED,
    matched_profiles: MatchedProfilesArg = PROFILES_NOT_PROVIDED,
) -> RiskAssessment:
    resolved_profiles = resolve_matched_profiles(
        holdings,
        profiles_snapshot=profiles_snapshot,
        matched_profiles=matched_profiles,
    )
    total_amount = sum(holding.holding_amount for holding in holdings)
    weight_denominator = resolve_weight_denominator(
        holdings,
        profile,
        actual_total=total_amount,
    )
    effective_returns = [
        resolve_effective_holding_return_percent(holding, profile=holding_profile)
        for holding, holding_profile in zip(
            holdings,
            resolved_profiles,
            strict=True,
        )
    ]
    weighted_return = _weighted_return_percent(
        holdings,
        total_amount,
        effective_returns,
    )
    alerts: list[RiskAlert] = []

    if weighted_return <= -abs(profile.max_drawdown_percent):
        alerts.append(
            RiskAlert(
                code="MAX_DRAWDOWN",
                severity="high",
                message=f"组合浮亏 {weighted_return:.2f}% 已触及 {profile.max_drawdown_percent:.1f}% 风险复核线。",
                evidence="按持仓金额加权，使用与界面一致的估算持有收益率。",
            )
        )

    drawdown_limit = abs(profile.max_drawdown_percent)
    for holding, effective_return in zip(holdings, effective_returns, strict=True):
        if effective_return <= -drawdown_limit:
            alerts.append(
                RiskAlert(
                    code="HOLDING_DRAWDOWN",
                    severity="medium",
                    message=(
                        f"{holding.fund_name} 估算持有收益 {effective_return:.2f}% 已触及"
                        f" {profile.max_drawdown_percent:.1f}% 单只浮亏复核线。"
                    ),
                    evidence="与界面「持有」列一致（盘中含板块估算）。",
                )
            )

    if weight_denominator > 0:
        for holding in holdings:
            weight = holding.holding_amount / weight_denominator * 100
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


def _weighted_return_percent(
    holdings: list[Holding],
    total_amount: float,
    effective_returns: list[float],
) -> float:
    if total_amount <= 0:
        return 0
    return sum(
        holding.holding_amount * effective_return
        for holding, effective_return in zip(
            holdings,
            effective_returns,
            strict=True,
        )
    ) / total_amount
