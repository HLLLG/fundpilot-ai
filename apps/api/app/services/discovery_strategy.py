from __future__ import annotations

from typing import Any, Mapping

from app.models import DiscoveryStrategy, InvestorProfile
from app.services.fund_tradeability import resolve_profile_min_holding_days


DEFAULT_DISCOVERY_STRATEGY: DiscoveryStrategy = "opportunity_first"
LEGACY_DISCOVERY_STRATEGY: DiscoveryStrategy = "risk_first"
OPPORTUNITY_HORIZON_LABEL = "1-3个月"
# Fee schedules use calendar days. Thirty calendar days is the conservative
# execution proxy for the lower bound of a 20-trading-day opportunity window.
OPPORTUNITY_MIN_HOLDING_DAYS = 30
OPPORTUNITY_WINDOW_TRADING_DAYS = (20, 60)


def normalize_discovery_strategy(
    value: object,
    *,
    default: DiscoveryStrategy = DEFAULT_DISCOVERY_STRATEGY,
) -> DiscoveryStrategy:
    normalized = str(value or "").strip()
    if normalized in {"opportunity_first", "risk_first"}:
        return normalized  # type: ignore[return-value]
    return default


def strategy_from_facts(
    facts: Mapping[str, Any] | None,
    *,
    legacy_default: bool = True,
) -> DiscoveryStrategy:
    """Read the persisted strategy without changing historical report semantics."""

    if isinstance(facts, Mapping):
        effective = facts.get("effective_configuration")
        if isinstance(effective, Mapping) and effective.get("discovery_strategy"):
            return normalize_discovery_strategy(
                effective.get("discovery_strategy"),
                default=(
                    LEGACY_DISCOVERY_STRATEGY
                    if legacy_default
                    else DEFAULT_DISCOVERY_STRATEGY
                ),
            )
        if facts.get("discovery_strategy"):
            return normalize_discovery_strategy(
                facts.get("discovery_strategy"),
                default=(
                    LEGACY_DISCOVERY_STRATEGY
                    if legacy_default
                    else DEFAULT_DISCOVERY_STRATEGY
                ),
            )
    return LEGACY_DISCOVERY_STRATEGY if legacy_default else DEFAULT_DISCOVERY_STRATEGY


def discovery_horizon_label(
    strategy: DiscoveryStrategy | str,
    profile: InvestorProfile,
) -> str:
    if normalize_discovery_strategy(strategy) == "opportunity_first":
        return OPPORTUNITY_HORIZON_LABEL
    return profile.horizon or "1-3个月"


def discovery_minimum_holding_days(
    strategy: DiscoveryStrategy | str,
    profile: InvestorProfile,
) -> int | None:
    if normalize_discovery_strategy(strategy) == "opportunity_first":
        return OPPORTUNITY_MIN_HOLDING_DAYS
    return resolve_profile_min_holding_days(profile)


def strategy_contract(strategy: DiscoveryStrategy | str) -> dict[str, Any]:
    normalized = normalize_discovery_strategy(strategy)
    if normalized == "opportunity_first":
        return {
            "id": normalized,
            "label": "机会优先",
            "target_horizon": OPPORTUNITY_HORIZON_LABEL,
            "signal_windows_trading_days": list(OPPORTUNITY_WINDOW_TRADING_DAYS),
            "candidate_drawdown_policy": "position_sizing_until_quality_watch_only",
            "quant_coverage_policy": "confidence_modifier_not_universal_whitelist",
            "account_loss_review_policy": "separate_not_candidate_eligibility",
        }
    return {
        "id": normalized,
        "label": "稳健筛选",
        "target_horizon": "follow_investor_profile",
        "signal_windows_trading_days": [],
        "candidate_drawdown_policy": "style_suitability_gate",
        "quant_coverage_policy": "execution_whitelist",
        "account_loss_review_policy": "legacy_profile_comparison",
    }
