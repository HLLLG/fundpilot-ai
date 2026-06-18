from __future__ import annotations

from datetime import datetime, timezone

from app.database import get_most_recent_portfolio_snapshot, get_portfolio_summary, list_fund_profiles
from app.models import FundProfile, Holding
from app.services.fund_code_resolver import reconcile_holding_fund_codes
from app.services.fund_profile import FundProfileService, _is_valid_sector_label
from app.services.holding_amount_sync import sync_holding_amounts_from_shares
from app.services.holding_estimates import enrich_holdings_estimates, sum_daily_profit
from app.services.holding_filters import is_test_holding, without_test_holdings
from app.services.overview_pipeline import enrich_holdings_from_profiles
from app.services.portfolio_persistence import enrich_loaded_holdings, persist_holdings_after_sector_refresh
from app.services.sector_quote_service import refresh_holdings_sector_quotes


def _coerce_utc_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _refreshed_at_from_summary() -> datetime | None:
    summary = get_portfolio_summary()
    if summary is None:
        return None
    return _coerce_utc_datetime(summary.updated_at)


def build_portfolio_holdings_response(
    holdings: list[Holding],
    *,
    source: str,
    snapshot_date: str | None,
    refreshed_at: datetime | None,
) -> dict:
    holdings = reconcile_holding_fund_codes(holdings)
    holdings = FundProfileService().resolve_holdings(holdings)
    summary = get_portfolio_summary()
    profiles = FundProfileService().list_profiles()
    payload = summary.model_dump(mode="json") if summary else {}
    total_from_holdings = round(sum(holding.holding_amount for holding in holdings), 2)
    if total_from_holdings:
        payload["total_assets"] = total_from_holdings
    if holdings:
        payload["daily_profit"] = sum_daily_profit(holdings)
        if total_from_holdings > (payload["daily_profit"] or 0):
            previous = total_from_holdings - float(payload["daily_profit"])
            if previous > 0:
                payload["daily_return_percent"] = round(
                    float(payload["daily_profit"]) / previous * 100,
                    2,
                )
    payload["holding_count"] = len(holdings)
    return {
        "holdings": [holding.model_dump() for holding in holdings],
        "source": source,
        "snapshot_date": snapshot_date,
        "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
        "portfolio_summary": payload or None,
        "profile_count": len(profiles),
    }


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
        yesterday_profit=profile.yesterday_profit,
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
    """合并档案中的结构性字段；金额/收益由份额×净值与官方净值自动推算，不用 OCR 快照覆盖。"""
    patch: dict = {
        "fund_code": profile.fund_code,
        "fund_name": profile.fund_name,
    }
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
    """以基金档案为准合并持仓列表；金额/收益不在此覆盖，由自动同步负责。"""
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
    merged = sync_holding_amounts_from_shares(merged)

    if refresh_sectors and merged:
        sector_result = refresh_holdings_sector_quotes(merged, force_refresh=False)
        merged = [Holding.model_validate(item) for item in sector_result["holdings"]]

    return persist_holdings_after_sector_refresh(merged)


def load_persisted_holdings() -> tuple[list[Holding], str, str | None, datetime | None]:
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
                        _refreshed_at_from_summary(),
                    )
            merged = without_test_holdings(merge_holdings_with_profiles(holdings))
            enriched = enrich_loaded_holdings(enrich_holdings_from_profiles(merged))
            return (
                enriched,
                "snapshot",
                snapshot.get("snapshot_date"),
                _coerce_utc_datetime(snapshot.get("captured_at")),
            )

    if profile_holdings:
        return (
            enrich_loaded_holdings(profile_holdings),
            "profiles",
            None,
            _refreshed_at_from_summary(),
        )

    return [], "empty", None, None
