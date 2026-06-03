from __future__ import annotations

from app.database import get_most_recent_portfolio_snapshot, list_fund_profiles
from app.models import FundProfile, Holding
from app.services.fund_profile import FundProfileService, _is_valid_sector_label
from app.services.holding_estimates import enrich_holdings_estimates
from app.services.holding_filters import is_test_holding, without_test_holdings
from app.services.overview_pipeline import enrich_holdings_from_profiles
from app.services.portfolio_persistence import enrich_loaded_holdings, persist_holdings_after_sector_refresh
from app.services.sector_quote_service import refresh_holdings_sector_quotes


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
        intraday_index_name=profile.intraday_index_name,
        daily_profit=profile.daily_profit,
    )


def holdings_from_profiles(*, min_amount: float = 0) -> list[Holding]:
    profiles = list_fund_profiles()
    service = FundProfileService()
    holdings = without_test_holdings(
        [
            profile_to_holding(profile)
            for profile in profiles
            if (profile.holding_amount or 0) > min_amount
        ]
    )
    if not holdings:
        return []
    return service.resolve_holdings(enrich_holdings_from_profiles(holdings))


def _holdings_total(holdings: list[Holding]) -> float:
    return round(sum(holding.holding_amount for holding in holdings), 2)


def _should_recover_from_profiles(
    snapshot_holdings: list[Holding],
    profile_holdings: list[Holding],
    *,
    snapshot_total_assets: float | None = None,
) -> bool:
    if not profile_holdings:
        return False
    if len(snapshot_holdings) < len(profile_holdings):
        return True
    snap_total = _holdings_total(snapshot_holdings)
    profile_total = _holdings_total(profile_holdings)
    if profile_total > snap_total * 1.05:
        return True
    if (
        snapshot_total_assets
        and snap_total > 0
        and float(snapshot_total_assets) > snap_total * 1.2
    ):
        return True
    return False


def _overlay_profile_onto_holding(base: Holding, profile: FundProfile) -> Holding:
    holding_return = profile.holding_return_percent
    patch: dict = {
        "fund_code": profile.fund_code,
        "fund_name": profile.fund_name,
    }
    if profile.holding_amount and profile.holding_amount > 0:
        patch["holding_amount"] = profile.holding_amount
    if profile.holding_profit is not None:
        patch["holding_profit"] = profile.holding_profit
    if holding_return is not None:
        patch["holding_return_percent"] = holding_return
        patch["return_percent"] = holding_return
    if _is_valid_sector_label(profile.sector_name):
        patch["sector_name"] = profile.sector_name
    elif _is_valid_sector_label(base.sector_name):
        patch["sector_name"] = base.sector_name
    if profile.intraday_index_name:
        patch["intraday_index_name"] = profile.intraday_index_name
    if base.sector_return_percent is None and profile.sector_return_percent is not None:
        patch["sector_return_percent"] = profile.sector_return_percent
    return base.model_copy(update=patch)


def merge_holdings_with_profiles(
    snapshot_holdings: list[Holding],
    *,
    profiles: list[FundProfile] | None = None,
) -> list[Holding]:
    """以基金档案为准合并：详情页更新的持有金额会覆盖日快照。"""
    if profiles is None:
        profiles = [
            profile
            for profile in list_fund_profiles()
            if (profile.holding_amount or 0) > 0
            and not is_test_holding(profile_to_holding(profile))
        ]
    if not profiles:
        return snapshot_holdings

    by_code = {
        row.fund_code: row
        for row in snapshot_holdings
        if row.fund_code and row.fund_code != "000000"
    }
    by_name = {row.fund_name: row for row in snapshot_holdings}

    merged: list[Holding] = []
    seen_codes: set[str] = set()

    for profile in profiles:
        existing = by_code.get(profile.fund_code) or by_name.get(profile.fund_name)
        if existing is not None:
            merged.append(_overlay_profile_onto_holding(existing, profile))
        else:
            merged.append(profile_to_holding(profile))
        seen_codes.add(profile.fund_code)

    for row in snapshot_holdings:
        if row.fund_code in seen_codes or is_test_holding(row):
            continue
        if row.fund_name in {item.fund_name for item in merged}:
            continue
        merged.append(row)

    return merged


def sync_portfolio_from_profiles(*, refresh_sectors: bool = True) -> list[Holding]:
    """详情建档后同步今日看板：合并档案 → 刷新板块 → 持久化。"""
    snapshot = get_most_recent_portfolio_snapshot()
    base: list[Holding] = []
    if snapshot and snapshot.get("holdings"):
        base = [Holding.model_validate(item) for item in snapshot["holdings"]]

    merged = without_test_holdings(merge_holdings_with_profiles(base))
    merged = enrich_holdings_from_profiles(merged)

    if refresh_sectors and merged:
        sector_result = refresh_holdings_sector_quotes(merged, force_refresh=False)
        merged = [Holding.model_validate(item) for item in sector_result["holdings"]]

    return persist_holdings_after_sector_refresh(enrich_holdings_estimates(merged))


def load_persisted_holdings() -> tuple[list[Holding], str, str | None]:
    profile_holdings = holdings_from_profiles()
    snapshot = get_most_recent_portfolio_snapshot()

    if snapshot and snapshot.get("holdings"):
        holdings = [Holding.model_validate(item) for item in snapshot["holdings"]]
        if holdings:
            snapshot_total = snapshot.get("total_assets")
            if _should_recover_from_profiles(
                holdings,
                profile_holdings,
                snapshot_total_assets=snapshot_total,
            ):
                if profile_holdings:
                    return (
                        enrich_loaded_holdings(profile_holdings),
                        "profiles_recovered",
                        snapshot.get("snapshot_date"),
                    )
            merged = without_test_holdings(merge_holdings_with_profiles(holdings))
            enriched = enrich_loaded_holdings(enrich_holdings_from_profiles(merged))
            return enriched, "snapshot", snapshot.get("snapshot_date")

    if profile_holdings:
        return enrich_loaded_holdings(profile_holdings), "profiles", None

    return [], "empty", None
