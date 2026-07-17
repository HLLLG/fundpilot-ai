from __future__ import annotations

from datetime import datetime, timezone

from app.database import (
    get_most_recent_portfolio_snapshot,
    get_portfolio_summary,
    list_fund_profiles,
    save_portfolio_summary,
)
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


def merge_holdings_with_snapshot(
    incoming: list[Holding],
    *,
    allow_membership_additions: bool = False,
) -> list[Holding]:
    """Rebase a non-membership refresh on the latest persisted membership.

    Quote/NAV refreshes are field patches, never membership commands. An older
    browser tab or a background task may therefore update only rows that still
    exist in the latest snapshot; it cannot delete a newly-added fund or revive
    a removed one. Authoritative profile/transaction sync may opt in to append
    genuinely new positions with ``allow_membership_additions=True``.
    """
    snapshot = get_most_recent_portfolio_snapshot()
    if not snapshot:
        return incoming
    previous = [Holding.model_validate(item) for item in snapshot.get("holdings", [])]
    if not previous:
        return list(incoming) if allow_membership_additions else []

    meaningful_incoming = without_placeholder_holdings(without_test_holdings(incoming))
    meaningful_previous = without_placeholder_holdings(without_test_holdings(previous))
    if not meaningful_incoming and meaningful_previous:
        return previous

    incoming_by_code = {
        holding.fund_code: holding
        for holding in incoming
        if holding.fund_code and holding.fund_code != "000000"
    }
    incoming_by_name = {holding.fund_name: holding for holding in incoming}

    merged: list[Holding] = []
    seen_incoming: set[int] = set()
    for prev in previous:
        patch = incoming_by_code.get(prev.fund_code) or incoming_by_name.get(prev.fund_name)
        if patch is None:
            merged.append(prev)
            continue
        merged.append(_overlay_sector_fields(prev, patch))
        seen_incoming.add(id(patch))

    if allow_membership_additions:
        for item in incoming:
            if id(item) not in seen_incoming:
                merged.append(item)
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


def _drop_holdings_removed_during_refresh(
    enriched: list[Holding],
    *,
    allow_membership_additions: bool = False,
) -> list[Holding]:
    """写回快照前最后一次对账，防止"删除基金"与"慢速板块刷新"互相踩踏。

    本函数上面从读取快照到这里，中间经过份额同步、官方净值、天天基金估值等
    多次可能耗时数秒的网络调用；如果用户在这段时间里删除了基金，快照早就变了，
    但这里手上的 ``enriched`` 仍然是按刷新开始时那份旧持仓算出来的。如果直接把它
    整份写回快照，就会把用户刚删除的基金重新写回去（即"缓存污染，删除的基金又
    出现了"）。这里只做成员资格过滤（是否还在最新快照里），不覆盖任何已经算好的
    金额/收益字段，避免影响本函数原本要更新的净值同步结果。
    """
    if allow_membership_additions:
        return enriched

    latest_snapshot = get_most_recent_portfolio_snapshot()
    if latest_snapshot is None or latest_snapshot.get("holdings") is None:
        return enriched

    latest_holdings = latest_snapshot.get("holdings") or []
    latest_codes = {
        (item.get("fund_code") or "").strip()
        for item in latest_holdings
        if (item.get("fund_code") or "").strip() and item.get("fund_code") != "000000"
    }
    latest_names = {item.get("fund_name") for item in latest_holdings if item.get("fund_name")}
    if not latest_codes and not latest_names:
        return []

    return [
        holding
        for holding in enriched
        if (holding.fund_code and holding.fund_code != "000000" and holding.fund_code in latest_codes)
        or (holding.fund_name and holding.fund_name in latest_names)
    ]


def persist_holdings_after_sector_refresh(
    holdings: list[Holding],
    *,
    fetched_at: datetime | None = None,
    with_official_nav: bool = True,
    allow_membership_additions: bool = False,
) -> list[Holding]:
    from app.services.portfolio_mutation_guard import portfolio_mutation_guard

    # Network fetches finish before this function is called. Hold the account
    # lock only for the final rebase + read-modify-write transaction boundary,
    # closing the former gap between the last membership check and snapshot
    # commit across multiple Uvicorn workers.
    with portfolio_mutation_guard():
        return _persist_holdings_after_sector_refresh_unlocked(
            holdings,
            fetched_at=fetched_at,
            with_official_nav=with_official_nav,
            allow_membership_additions=allow_membership_additions,
        )


def _persist_holdings_after_sector_refresh_unlocked(
    holdings: list[Holding],
    *,
    fetched_at: datetime | None = None,
    with_official_nav: bool = True,
    allow_membership_additions: bool = False,
) -> list[Holding]:
    """板块刷新成功后写回日快照与账户汇总，重启后保留最新当日收益。

    ``with_official_nav=False`` 时跳过逐只 AkShare 官方净值覆盖（fast 刷新用），
    避免 CloudBase 网关 ~60s 超时；accurate 刷新仍走官方净值。
    """
    merged = without_inactive_holdings(
        without_placeholder_holdings(
            without_test_holdings(
                merge_holdings_with_snapshot(
                    holdings,
                    allow_membership_additions=allow_membership_additions,
                )
            )
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

    enriched = _drop_holdings_removed_during_refresh(
        enriched,
        allow_membership_additions=allow_membership_additions,
    )
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
    # 2026-07-04 修复：此前用 `total_assets > daily_profit > 0` 做门槛，要求 daily_profit
    # 严格大于 0——平盘（daily_profit=0）或亏损（daily_profit<0）的交易日会被整个跳过，
    # daily_return_percent 永久写成 None（而不是正确算出 0 或负的收益率）。只要分母
    # （昨日结算总资产）为正，任何符号的 daily_profit 都应该能算出收益率；对齐
    # official_nav_settlement.py::_persist_settlement_holdings 的正确写法。
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
    active_codes = {
        holding.fund_code
        for holding in enriched
        if holding.fund_code and holding.fund_code != "000000"
    }
    profiles_by_code = {
        profile.fund_code: profile
        for profile in list_fund_profiles()
        if profile.fund_code in active_codes
    }
    persist_intraday_curve(enriched, profiles_by_code)
    return enriched
