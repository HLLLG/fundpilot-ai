from __future__ import annotations

from app.database import get_most_recent_portfolio_snapshot, list_fund_profiles
from app.models import FundProfile, Holding
from app.services.fund_profile import FundProfileService
from app.services.overview_pipeline import enrich_holdings_from_profiles


def profile_to_holding(profile: FundProfile) -> Holding:
    holding_return = profile.holding_return_percent
    return Holding(
        fund_code=profile.fund_code,
        fund_name=profile.fund_name,
        holding_amount=profile.holding_amount or 0,
        return_percent=holding_return or 0,
        holding_return_percent=holding_return,
        holding_profit=profile.holding_profit,
        sector_name=profile.sector_name,
        sector_return_percent=profile.sector_return_percent,
        daily_profit=profile.daily_profit,
    )


def holdings_from_profiles(*, min_amount: float = 0) -> list[Holding]:
    profiles = list_fund_profiles()
    service = FundProfileService()
    holdings = [
        profile_to_holding(profile)
        for profile in profiles
        if (profile.holding_amount or 0) > min_amount
    ]
    if not holdings:
        return []
    return service.resolve_holdings(enrich_holdings_from_profiles(holdings))


def load_persisted_holdings() -> tuple[list[Holding], str, str | None]:
    """返回 (holdings, source, snapshot_date)。优先最近快照，否则从基金档案重建。"""
    snapshot = get_most_recent_portfolio_snapshot()
    if snapshot and snapshot.get("holdings"):
        holdings = [Holding.model_validate(item) for item in snapshot["holdings"]]
        if holdings:
            enriched = enrich_holdings_from_profiles(holdings)
            return enriched, "snapshot", snapshot.get("snapshot_date")

    profile_holdings = holdings_from_profiles()
    if profile_holdings:
        return profile_holdings, "profiles", None

    return [], "empty", None
