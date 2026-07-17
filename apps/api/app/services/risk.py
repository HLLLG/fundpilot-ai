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
    """持仓占比分母：有实际持仓时只使用当前组合市值。"""
    if actual_total is None:
        actual_total = sum(holding.holding_amount for holding in holdings)
    if actual_total > 0:
        return actual_total
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
                code="PORTFOLIO_COST_BASIS_LOSS",
                severity="high",
                message=(
                    f"组合相对持仓成本浮亏 {weighted_return:.2f}% 已触及 "
                    f"{profile.max_drawdown_percent:.1f}% 成本浮亏复核线。"
                ),
                evidence=(
                    "按当前持仓金额加权的估算持有收益率；该指标以持仓成本为基准，"
                    "不是组合历史峰值到谷值的最大回撤。"
                ),
            )
        )

    drawdown_limit = abs(profile.max_drawdown_percent)
    for holding, effective_return in zip(holdings, effective_returns, strict=True):
        if effective_return <= -drawdown_limit:
            alerts.append(
                RiskAlert(
                    code="HOLDING_COST_BASIS_LOSS",
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
