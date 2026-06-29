from __future__ import annotations

from datetime import datetime, timezone

from app.database import get_most_recent_portfolio_snapshot, get_portfolio_summary, save_portfolio_summary
from app.models import Holding, PortfolioSummary
from app.services.holding_amount_sync import sync_holding_amounts_from_shares
from app.services.holding_estimates import (
    enrich_holdings_estimates,
    overlay_official_nav_returns,
    sum_daily_profit,
)
from app.services.fund_profile import _is_valid_sector_label
from app.services.fund_profile import _looks_like_index_name
from app.services.holding_filters import without_inactive_holdings, without_placeholder_holdings, without_test_holdings
from app.services.portfolio_profit_analysis import persist_intraday_curve
from app.services.portfolio_snapshot import save_daily_snapshot


def _overlay_sector_fields(base: Holding, patch: Holding) -> Holding:
    updates: dict = {}
    if _is_valid_sector_label(patch.sector_name):
        updates["sector_name"] = patch.sector_name
    if patch.intraday_index_name and _looks_like_index_name(patch.intraday_index_name):
        updates["intraday_index_name"] = patch.intraday_index_name
    if patch.sector_return_percent is not None:
        updates["sector_return_percent"] = patch.sector_return_percent
        # 板块刷新与当日收益同源写回；盘中无官方净值时 patch 显式置 None，须覆盖快照残留。
        updates["daily_return_percent"] = patch.daily_return_percent
        updates["daily_profit"] = patch.daily_profit
        updates["daily_return_percent_source"] = patch.daily_return_percent_source
    elif patch.daily_return_percent_source == "official_nav":
        updates["daily_return_percent"] = patch.daily_return_percent
        updates["daily_profit"] = patch.daily_profit
        updates["daily_return_percent_source"] = patch.daily_return_percent_source
    if patch.sector_return_percent_source is not None:
        updates["sector_return_percent_source"] = patch.sector_return_percent_source
    elif patch.daily_profit is not None:
        updates["daily_profit"] = patch.daily_profit
    elif patch.daily_return_percent is not None:
        updates["daily_return_percent"] = patch.daily_return_percent
    elif patch.daily_return_percent_source is not None:
        updates["daily_return_percent_source"] = patch.daily_return_percent_source
    if patch.yesterday_profit is not None:
        updates["yesterday_profit"] = patch.yesterday_profit
    return base.model_copy(update=updates) if updates else base


def merge_holdings_with_snapshot(incoming: list[Holding]) -> list[Holding]:
    """板块刷新写回：以请求持仓为成员名单；从快照补全同码/同名的非板块字段。

    仅当请求里只有测试/占位行、没有有效持仓时，才保留上一版快照（防误覆盖）。
    删除基金后客户端会发送更短的完整列表，不能把已删基金从快照里捞回来。
    """
    snapshot = get_most_recent_portfolio_snapshot()
    if not snapshot:
        return incoming
    previous = [Holding.model_validate(item) for item in snapshot.get("holdings", [])]
    if not previous:
        return incoming

    meaningful_incoming = without_placeholder_holdings(without_test_holdings(incoming))
    meaningful_previous = without_placeholder_holdings(without_test_holdings(previous))
    if not meaningful_incoming and meaningful_previous:
        return previous

    by_code = {
        holding.fund_code: holding
        for holding in previous
        if holding.fund_code != "000000"
    }
    by_name = {holding.fund_name: holding for holding in previous}

    merged: list[Holding] = []
    for item in incoming:
        prev = by_code.get(item.fund_code) or by_name.get(item.fund_name)
        merged.append(_overlay_sector_fields(prev, item) if prev else item)
    return merged


def enrich_loaded_holdings(
    holdings: list[Holding],
    *,
    with_network: bool = False,
) -> list[Holding]:
    """恢复持仓时补全展示字段。

    默认 ``with_network=False``：仅用快照/档案已有字段做估算，避免 AkShare 子进程拖慢
    ``GET /api/portfolio/holdings``。板块涨跌由服务端现货缓存叠加（后台每 3min 刷新）。
    """
    if not holdings:
        return holdings
    if not with_network:
        from app.services.fund_primary_sector_service import apply_primary_sector_to_holdings

        return apply_primary_sector_to_holdings(enrich_holdings_estimates(holdings), fetch_benchmark=False)
    from app.services.transaction_ledger import confirm_and_compute_overrides

    overrides = confirm_and_compute_overrides(holdings)
    synced = sync_holding_amounts_from_shares(holdings, shares_override=overrides)
    return enrich_holdings_estimates(overlay_official_nav_returns(synced))


def persist_holdings_after_sector_refresh(
    holdings: list[Holding],
    *,
    fetched_at: datetime | None = None,
    with_official_nav: bool = True,
) -> list[Holding]:
    """板块刷新成功后写回日快照与账户汇总，重启后保留最新当日收益。

    ``with_official_nav=False`` 时跳过逐只 AkShare 官方净值覆盖（fast 刷新用），
    避免 CloudBase 网关 ~60s 超时；accurate 刷新仍走官方净值。
    """
    merged = without_inactive_holdings(
        without_placeholder_holdings(
            without_test_holdings(merge_holdings_with_snapshot(holdings))
        )
    )
    from app.services.transaction_ledger import confirm_and_compute_overrides

    overrides = confirm_and_compute_overrides(merged)
    synced = sync_holding_amounts_from_shares(
        merged,
        shares_override=overrides,
        estimate_quotes={} if not with_official_nav else None,
        allow_nav_fetch=with_official_nav,
    )
    if with_official_nav:
        synced = overlay_official_nav_returns(synced)
    enriched = enrich_holdings_estimates(synced)
    if not enriched:
        return enriched

    summary = get_portfolio_summary()
    total_assets = round(
        sum(
            (h.settled_holding_amount or h.holding_amount) + (h.daily_profit or 0)
            for h in enriched
        ),
        2,
    )
    daily_profit = sum_daily_profit(enriched)
    daily_return_percent = None
    if total_assets > daily_profit > 0:
        previous = total_assets - daily_profit
        if previous > 0:
            daily_return_percent = round(daily_profit / previous * 100, 2)

    if summary is None:
        summary = PortfolioSummary(
            total_assets=total_assets,
            daily_profit=daily_profit,
            daily_return_percent=daily_return_percent,
            holding_count=len(enriched),
            updated_at=fetched_at or datetime.now(timezone.utc),
        )
    else:
        patch: dict = {
            "total_assets": total_assets,
            "holding_count": len(enriched),
            "daily_profit": daily_profit,
            "daily_return_percent": daily_return_percent,
            "updated_at": fetched_at or datetime.now(timezone.utc),
        }
        summary = summary.model_copy(update=patch)

    save_portfolio_summary(summary)
    save_daily_snapshot(enriched, summary)
    from app.database import get_fund_profile_by_code
    from app.models import FundProfile

    profiles_by_code: dict[str, FundProfile] = {}
    for holding in enriched:
        if holding.fund_code and holding.fund_code != "000000":
            profile = get_fund_profile_by_code(holding.fund_code)
            if profile is not None:
                profiles_by_code[holding.fund_code] = profile
    persist_intraday_curve(enriched, profiles_by_code)
    return enriched
