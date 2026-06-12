from __future__ import annotations

from app.models import Holding, PortfolioSummary
from app.services.fund_profile import FundProfileService, _is_valid_sector_label
from app.services.holding_amount_sync import bootstrap_holding_baselines, sync_holding_amounts_from_shares
from app.services.holding_estimates import (
    enrich_holdings_estimates,
    overlay_official_nav_returns,
    sum_daily_profit,
)
from app.services.sector_quote_service import refresh_holdings_sector_quotes


def enrich_holdings_from_profiles(holdings: list[Holding]) -> list[Holding]:
    service = FundProfileService()
    enriched: list[Holding] = []
    for holding in holdings:
        resolved = service.resolve_holding(holding)
        profile = service._find_profile_for_holding(resolved)
        if profile is None:
            enriched.append(resolved)
            continue

        patch: dict = {}
        if not _is_valid_sector_label(resolved.sector_name) and _is_valid_sector_label(
            profile.sector_name
        ):
            patch["sector_name"] = profile.sector_name
        elif not _is_valid_sector_label(resolved.sector_name):
            from app.services.fund_primary_sector_service import resolve_primary_sector

            code = resolved.fund_code if resolved.fund_code != "000000" else profile.fund_code
            if code and code != "000000":
                record = resolve_primary_sector(
                    code,
                    fund_name=resolved.fund_name or profile.fund_name,
                    allow_name_infer=False,
                )
                if record:
                    patch["sector_name"] = record.sector_name
                    if record.intraday_index_name and not resolved.intraday_index_name:
                        patch["intraday_index_name"] = record.intraday_index_name
        if not resolved.intraday_index_name and profile.intraday_index_name:
            patch["intraday_index_name"] = profile.intraday_index_name
        if resolved.sector_return_percent is None and profile.sector_return_percent is not None:
            patch["sector_return_percent"] = profile.sector_return_percent
        if resolved.holding_return_percent is None and profile.holding_return_percent is not None:
            patch["holding_return_percent"] = profile.holding_return_percent
            patch["return_percent"] = profile.holding_return_percent
        elif resolved.return_percent == 0 and profile.holding_return_percent is not None:
            patch["return_percent"] = profile.holding_return_percent
            patch["holding_return_percent"] = profile.holding_return_percent
        if resolved.holding_profit is None and profile.holding_profit is not None:
            patch["holding_profit"] = profile.holding_profit
        if (
            resolved.fund_code == "000000"
            and profile.fund_code != "000000"
            and not profile.is_provisional
        ):
            patch["fund_code"] = profile.fund_code
            patch["fund_name"] = profile.fund_name

        enriched.append(resolved.model_copy(update=patch) if patch else resolved)
    return enriched


def process_overview_holdings(
    holdings: list[Holding],
    *,
    portfolio_summary: PortfolioSummary | None = None,
    force_sector_refresh: bool = True,
    from_user_upload: bool = False,
) -> tuple[list[Holding], dict, PortfolioSummary | None]:
    """支付宝总览 OCR 后：锁定份额基线 → 刷新板块 → 按净值/板块自动推算全部金额。"""
    if not holdings:
        return holdings, {"ok": False, "message": "无持仓", "items": []}, portfolio_summary

    merged = enrich_holdings_from_profiles(holdings)
    if from_user_upload:
        merged = bootstrap_holding_baselines(merged, force_reset_shares=True)
    sector_result = refresh_holdings_sector_quotes(merged, force_refresh=force_sector_refresh)
    refreshed = [Holding.model_validate(item) for item in sector_result["holdings"]]
    synced = sync_holding_amounts_from_shares(refreshed, persist_profiles=False)
    estimated = enrich_holdings_estimates(overlay_official_nav_returns(synced))

    updated_summary = portfolio_summary
    if updated_summary is not None:
        total_assets = round(sum(holding.holding_amount for holding in estimated), 2)
        row_sum = sum_daily_profit(estimated)
        patch: dict = {
            "total_assets": total_assets,
            "daily_profit": row_sum if estimated else None,
            "holding_count": len(estimated),
        }
        if total_assets > (row_sum or 0) > 0:
            previous = total_assets - row_sum
            if previous > 0:
                patch["daily_return_percent"] = round(row_sum / previous * 100, 2)
        updated_summary = updated_summary.model_copy(update=patch)

    sector_result["holdings"] = [holding.model_dump() for holding in estimated]
    return estimated, sector_result, updated_summary
