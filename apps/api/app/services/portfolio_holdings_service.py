from __future__ import annotations

import time
from datetime import datetime, timezone

from app.database import get_most_recent_portfolio_snapshot, get_portfolio_summary, list_fund_profiles, save_portfolio_summary
from app.models import FundProfile, Holding, PortfolioSummary
from app.services.fund_code_resolver import reconcile_holding_fund_codes
from app.services.fund_name_utils import is_fund_name_match
from app.services.fund_nav_service import get_cached_official_nav_return
from app.services.fund_profile import FundProfileService, _is_valid_sector_label
from app.services.holding_amount_sync import sync_holding_amounts_from_shares
from app.services.holding_estimates import (
    _amount_includes_today_return,
    compute_daily_profit_from_rate,
    enrich_holdings_estimates,
    sum_daily_profit,
)
from app.services.holding_filters import is_inactive_holding, is_test_holding, without_inactive_holdings, without_test_holdings
from app.services.overview_pipeline import enrich_holdings_from_profiles
from app.services.portfolio_persistence import enrich_loaded_holdings, persist_holdings_after_sector_refresh
from app.services.sector_quote_service import refresh_holdings_sector_quotes
from app.services.portfolio_snapshot import save_daily_snapshot
from app.services.transaction_ledger import confirm_and_compute_overrides
from app.services.trading_session import get_effective_trade_date


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


def _sector_cache_miss(result: dict) -> bool:
    summary = result.get("summary") or {}
    if summary.get("provider_path") == "cache_miss":
        return True
    return result.get("message") == "板块缓存未命中，后台将刷新"


def _intraday_sector_window() -> bool:
    from app.services.trading_session import build_trading_session

    return build_trading_session().get("session_kind") in {
        "trading_day_intraday",
        "trading_day_pre_close",
    }


def apply_server_sector_cache_to_holdings(
    holdings: list[Holding],
    *,
    network_fallback: bool = True,
) -> list[Holding]:
    """读路径：优先服务端板块现货缓存；可选盘中缓存未命中时做一次快速拉取。"""
    from app.config import get_settings

    if not holdings or not get_settings().sector_quotes_enabled:
        return holdings
    result = refresh_holdings_sector_quotes(holdings, cache_only=True)
    if network_fallback and _sector_cache_miss(result) and _intraday_sector_window():
        result = refresh_holdings_sector_quotes(
            holdings,
            cache_only=False,
            timeout_seconds=8.0,
            force_refresh=False,
        )
    if not result.get("holdings"):
        return holdings
    updated = [Holding.model_validate(item) for item in result["holdings"]]
    return enrich_holdings_estimates(updated)


def build_portfolio_holdings_response(
    holdings: list[Holding],
    *,
    source: str,
    snapshot_date: str | None,
    refreshed_at: datetime | None,
    fetch_benchmark: bool = True,
) -> dict:
    holdings = without_inactive_holdings(reconcile_holding_fund_codes(holdings))
    holdings = FundProfileService().resolve_holdings(
        holdings,
        fetch_benchmark=fetch_benchmark,
    )
    summary = get_portfolio_summary()
    profiles = FundProfileService().list_profiles()
    payload = summary.model_dump(mode="json") if summary else {}
    total_from_holdings = round(
        sum(
            (holding.settled_holding_amount or holding.holding_amount)
            + (holding.daily_profit or 0)
            for holding in holdings
        ),
        2,
    )
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
    from app.services.holding_client import serialize_holdings_for_client

    return {
        "holdings": serialize_holdings_for_client(holdings),
        "source": source,
        "snapshot_date": snapshot_date,
        "refreshed_at": refreshed_at.isoformat() if refreshed_at else None,
        "portfolio_summary": payload or None,
        "profile_count": len(profiles),
    }


def _fast_round2(value: float) -> float:
    return round(value, 2)


def _fast_trusted_sector_return(holding: Holding) -> float | None:
    if holding.sector_return_percent_source in {"realtime", "closing_estimate"}:
        return holding.sector_return_percent
    return None


def _fast_overlay_cached_official_nav(holding: Holding, trade_date: str | None) -> Holding:
    if (
        not trade_date
        or not holding.fund_code
        or holding.fund_code == "000000"
        or holding.daily_return_percent_source == "pending_accrual"
    ):
        return holding
    nav_return = get_cached_official_nav_return(holding.fund_code, trade_date)
    if nav_return is None:
        return holding
    amount = holding.settled_holding_amount or holding.holding_amount
    if (
        holding.daily_profit is not None
        and holding.daily_return_percent_source == "official_nav"
    ):
        return holding.model_copy(
            update={
                "daily_return_percent": nav_return,
                "daily_return_percent_source": "official_nav",
            }
        )
    return holding.model_copy(
        update={
            "daily_return_percent": nav_return,
            "daily_profit": compute_daily_profit_from_rate(
                amount,
                nav_return,
                amount_includes_today=_amount_includes_today_return(holding),
            ),
            "daily_return_percent_source": "official_nav",
        }
    )


def _fast_daily_profit(holding: Holding) -> float | None:
    if holding.daily_profit is not None:
        return holding.daily_profit
    rate = (
        holding.daily_return_percent
        if holding.daily_return_percent is not None
        else _fast_trusted_sector_return(holding)
    )
    if rate is None:
        return None
    amount = holding.settled_holding_amount or holding.holding_amount
    if amount <= 0:
        return None
    if holding.daily_return_percent_source == "official_nav" and holding.amount_includes_today:
        return _fast_round2(amount * rate / (100 + rate))
    return _fast_round2(amount * rate / 100)


def _fast_serialize_holding_for_client(holding: Holding) -> dict:
    payload = holding.model_dump()
    settled = holding.settled_holding_amount or holding.holding_amount
    sector_return = _fast_trusted_sector_return(holding)
    daily_rate = (
        holding.daily_return_percent
        if holding.daily_return_percent is not None
        else sector_return
    )
    settled_return = (
        holding.holding_return_percent
        if holding.holding_return_percent is not None
        else holding.return_percent
    )
    daily_profit = _fast_daily_profit(holding)
    estimated_holding_return = settled_return
    if (
        holding.daily_return_percent_source not in {"official_nav", "pending_accrual"}
        and settled_return is not None
        and daily_rate is not None
    ):
        estimated_holding_return = _fast_round2(settled_return + daily_rate)
    payload["settled_holding_amount"] = settled
    payload["display_holding_amount"] = settled
    payload["holding_amount"] = settled
    payload["sector_return_percent"] = sector_return
    payload["sector_return_percent_source"] = (
        holding.sector_return_percent_source if sector_return is not None else None
    )
    payload["daily_profit"] = daily_profit
    payload["estimated_daily_return_percent"] = daily_rate
    payload["daily_return_is_estimated"] = (
        holding.daily_return_percent_source not in {"official_nav", "pending_accrual"}
        and daily_rate is not None
    )
    payload["estimated_holding_return_percent"] = estimated_holding_return
    payload["estimated_holding_profit"] = holding.holding_profit
    payload["holding_return_is_estimated"] = (
        holding.daily_return_percent_source not in {"official_nav", "pending_accrual"}
        and daily_rate is not None
    )
    payload["profit_accrual_deferred"] = holding.daily_return_percent_source == "pending_accrual"
    return payload


def build_fast_snapshot_holdings_response() -> dict | None:
    """Cold-start GET path: return the latest persisted snapshot without slow enrichment."""
    snapshot = get_most_recent_portfolio_snapshot()
    if not snapshot or not snapshot.get("holdings"):
        return None
    holdings = [
        Holding.model_validate(item)
        for item in snapshot.get("holdings", [])
    ]
    holdings = without_inactive_holdings(without_test_holdings(holdings))
    if not holdings:
        return None
    from app.services.fund_primary_sector_service import repair_stale_cross_market_sectors

    holdings = repair_stale_cross_market_sectors(holdings)
    trade_date = get_effective_trade_date()
    fund_codes = [
        holding.fund_code
        for holding in holdings
        if (holding.fund_code or "").strip() and holding.fund_code != "000000"
    ]
    from app.services.fund_nav_service import prime_official_nav_cache

    prime_official_nav_cache(fund_codes, trade_date, cache_only=True)
    holdings = sync_holding_amounts_from_shares(
        holdings,
        persist_profiles=False,
        allow_nav_fetch=False,
        estimate_quotes={},
    )
    holdings = [_fast_overlay_cached_official_nav(holding, trade_date) for holding in holdings]
    serialized = [_fast_serialize_holding_for_client(holding) for holding in holdings]
    daily_profit = snapshot.get("daily_profit")
    if daily_profit is None:
        daily_profit = _fast_round2(
            sum(float(item.get("daily_profit") or 0) for item in serialized)
        )
    total_assets = snapshot.get("total_assets")
    official_nav_settled = serialized and all(
        item.get("daily_return_percent_source") == "official_nav" for item in serialized
    )
    if total_assets is None:
        if official_nav_settled:
            total_assets = _fast_round2(
                sum(
                    float(item.get("settled_holding_amount") or item.get("holding_amount") or 0)
                    for item in serialized
                )
            )
        else:
            total_assets = _fast_round2(
                sum(
                    float(item.get("settled_holding_amount") or item.get("holding_amount") or 0)
                    + float(item.get("daily_profit") or 0)
                    for item in serialized
                )
            )
    summary = {
        "total_assets": total_assets,
        "daily_profit": daily_profit,
        "daily_return_percent": snapshot.get("daily_return_percent"),
        "holding_count": len(serialized),
    }
    captured_at = _coerce_utc_datetime(snapshot.get("captured_at"))
    return {
        "holdings": serialized,
        "source": "snapshot",
        "snapshot_date": snapshot.get("snapshot_date"),
        "refreshed_at": captured_at.isoformat() if captured_at else None,
        "portfolio_summary": summary,
        "profile_count": None,
        "fast_snapshot": True,
    }


def _lightweight_profile_holdings(*, min_amount: float = 0) -> list[Holding]:
    """仅用于快照恢复判断：读档案金额，不做 resolve / 外网 benchmark。"""
    return without_test_holdings(
        [
            profile_to_holding(profile)
            for profile in list_fund_profiles()
            if (profile.holding_amount or 0) > min_amount
        ]
    )


def load_dashboard_holdings() -> tuple[list[Holding], str, str | None, datetime | None]:
    """Dashboard 读路径：优先最新快照 + 本地估算，跳过全量 profile resolve。"""
    snapshot = get_most_recent_portfolio_snapshot()
    if not snapshot or not snapshot.get("holdings"):
        return load_persisted_holdings(fetch_benchmark=False)

    holdings = [
        Holding.model_validate(item)
        for item in snapshot.get("holdings", [])
    ]
    holdings = without_inactive_holdings(without_test_holdings(holdings))
    if not holdings:
        return [], "empty", None, None

    profile_holdings = _lightweight_profile_holdings()
    if _should_recover_from_profiles(
        holdings,
        profile_holdings,
        snapshot_total_assets=snapshot.get("total_assets"),
    ):
        return load_persisted_holdings(fetch_benchmark=False)

    trade_date = get_effective_trade_date()
    holdings = [_fast_overlay_cached_official_nav(holding, trade_date) for holding in holdings]
    from app.services.holding_estimates import enrich_holdings_estimates

    enriched = enrich_holdings_estimates(holdings)
    captured_at = _coerce_utc_datetime(snapshot.get("captured_at"))
    return (
        enriched,
        "snapshot",
        snapshot.get("snapshot_date"),
        captured_at,
    )


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


def holdings_from_profiles(
    *,
    min_amount: float = 0,
    fetch_benchmark: bool = True,
) -> list[Holding]:
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
    return service.resolve_holdings(
        enrich_holdings_from_profiles(holdings, fetch_benchmark=fetch_benchmark),
        fetch_benchmark=fetch_benchmark,
    )


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
    allow_profile_only = not snapshot_holdings

    for profile in profiles:
        existing = by_code.get(profile.fund_code) or by_name.get(profile.fund_name)
        if existing is not None:
            merged.append(_overlay_profile_onto_holding(existing, profile))
            seen_codes.add(profile.fund_code)
        elif allow_profile_only:
            merged.append(profile_to_holding(profile))
            seen_codes.add(profile.fund_code)

    for row in snapshot_holdings:
        if row.fund_code in seen_codes or is_test_holding(row):
            continue
        if is_inactive_holding(row):
            continue
        if row.fund_code and row.fund_code != "000000":
            from app.database import get_fund_profile_by_code

            profile = get_fund_profile_by_code(row.fund_code)
            if profile is not None and (profile.holding_amount or 0) <= 0:
                continue
        if row.fund_name in {item.fund_name for item in merged}:
            continue
        merged.append(row)

    return without_inactive_holdings(merged)


def sync_portfolio_from_profiles(*, refresh_sectors: bool = True) -> list[Holding]:
    """详情建档后同步今日看板：合并档案 → 刷新板块 → 持久化。"""
    snapshot = get_most_recent_portfolio_snapshot()
    base: list[Holding] = []
    if snapshot and snapshot.get("holdings"):
        base = [Holding.model_validate(item) for item in snapshot["holdings"]]

    merged = without_test_holdings(merge_holdings_with_profiles(base))
    merged = enrich_holdings_from_profiles(merged)
    overrides = confirm_and_compute_overrides(merged)
    merged = sync_holding_amounts_from_shares(merged, shares_override=overrides)

    if refresh_sectors and merged:
        sector_result = refresh_holdings_sector_quotes(merged, force_refresh=False)
        merged = [Holding.model_validate(item) for item in sector_result["holdings"]]

    return persist_holdings_after_sector_refresh(merged)


def load_persisted_holdings(
    *,
    fetch_benchmark: bool = True,
) -> tuple[list[Holding], str, str | None, datetime | None]:
    snapshot = get_most_recent_portfolio_snapshot()

    def _finalize(holdings: list[Holding], source: str, snap_date: str | None, refreshed: datetime | None):
        cleaned = without_inactive_holdings(holdings)
        if cleaned and len(cleaned) < len(holdings):
            _repair_snapshot_holdings(cleaned)
        return cleaned, source, snap_date, refreshed

    if snapshot and "holdings" in snapshot:
        holdings = [Holding.model_validate(item) for item in snapshot.get("holdings") or []]
        if holdings:
            snapshot_total = snapshot.get("total_assets")
            profile_holdings = _lightweight_profile_holdings()
            if _should_recover_from_profiles(
                holdings,
                profile_holdings,
                snapshot_total_assets=snapshot_total,
            ):
                full_profile_holdings = holdings_from_profiles(fetch_benchmark=fetch_benchmark)
                if full_profile_holdings:
                    return _finalize(
                        enrich_loaded_holdings(full_profile_holdings),
                        "profiles_recovered",
                        snapshot.get("snapshot_date"),
                        _refreshed_at_from_summary(),
                    )
            if fetch_benchmark:
                merged = without_test_holdings(merge_holdings_with_profiles(holdings))
                enriched = enrich_loaded_holdings(
                    enrich_holdings_from_profiles(
                        merged,
                        fetch_benchmark=fetch_benchmark,
                    )
                )
            else:
                merged = without_test_holdings(holdings)
                enriched = enrich_loaded_holdings(merged)
            return _finalize(
                enriched,
                "snapshot",
                snapshot.get("snapshot_date"),
                _coerce_utc_datetime(snapshot.get("captured_at")),
            )
        return _finalize(
            [],
            "snapshot",
            snapshot.get("snapshot_date"),
            _coerce_utc_datetime(snapshot.get("captured_at")),
        )

    profile_holdings = holdings_from_profiles(fetch_benchmark=fetch_benchmark)
    if profile_holdings:
        return _finalize(
            enrich_loaded_holdings(profile_holdings),
            "profiles",
            None,
            _refreshed_at_from_summary(),
        )

    return [], "empty", None, None


def _repair_snapshot_holdings(holdings: list[Holding]) -> None:
    """自愈：快照里残留已删除基金（金额 0 / 档案停用）时写回干净列表。"""
    if not holdings:
        return
    summary = get_portfolio_summary()
    if summary is None:
        summary = PortfolioSummary(
            total_assets=round(sum(h.holding_amount for h in holdings), 2),
            holding_count=len(holdings),
        )
    else:
        total_assets = round(
            sum((h.settled_holding_amount or h.holding_amount) + (h.daily_profit or 0) for h in holdings),
            2,
        )
        summary = summary.model_copy(
            update={
                "total_assets": total_assets,
                "holding_count": len(holdings),
                "daily_profit": sum_daily_profit(holdings),
            }
        )
    save_portfolio_summary(summary)
    save_daily_snapshot(holdings, summary)


def _holding_matches_target(
    holding: Holding,
    fund_code: str,
    fund_name: str | None,
) -> bool:
    code = (fund_code or "").strip()
    if code and code != "000000" and holding.fund_code == code:
        return True
    if fund_name:
        if holding.fund_name == fund_name:
            return True
        if is_fund_name_match(fund_name, holding.fund_name):
            return True
    return False


def _purge_fund_profile(fund_code: str, fund_name: str | None = None) -> None:
    """删除基金档案及用户级主关联板块映射（历史日快照仍保留）。"""
    from app.database import (
        delete_fund_primary_sector,
        delete_fund_profile,
        get_fund_profile_by_code,
    )

    code = (fund_code or "").strip()
    profile = get_fund_profile_by_code(code) if code and code != "000000" else None
    if profile is None and fund_name:
        profile = FundProfileService().find_match(fund_name)
    if profile is None:
        return
    purge_code = (profile.fund_code or "").strip()
    if not purge_code or purge_code == "000000":
        return
    delete_fund_primary_sector(purge_code)
    delete_fund_profile(purge_code)


def _reconcile_fund_profiles_with_snapshot(
    *, before: list[Holding], after: list[Holding]
) -> None:
    """删除持仓后，清掉任何"快照里已经没有、但档案表里还残留"的基金档案。

    `_purge_fund_profile` 按传入的 fund_code/fund_name 精确匹配删除，遇到历史数据
    code 对不上、名称有细微差异等情况会静默失败、留下一条孤儿档案。这条孤儿档案
    不影响当次响应，但下次冷启动（服务端内存缓存过期或重启）重新走
    `load_persisted_holdings` / `load_dashboard_holdings` 时，
    `_should_recover_from_profiles` 一看到"档案表基金数 > 快照基金数"，就会误判成
    "快照被截断"，直接用档案表整个重建持仓——相当于把刚手动删除的基金复活回来。
    这里在每次删除后做一次全量对账：只要某条档案对应的基金曾经出现在删除前的快照
    里、但不再出现在删除后的快照里，就直接清掉，避免两边数据来源长期不一致。
    """
    before_codes = {item.fund_code for item in before if item.fund_code and item.fund_code != "000000"}
    after_codes = {item.fund_code for item in after if item.fund_code and item.fund_code != "000000"}
    stale_codes = before_codes - after_codes
    if not stale_codes:
        return

    from app.database import delete_fund_primary_sector, delete_fund_profile

    for profile in list_fund_profiles():
        profile_code = (profile.fund_code or "").strip()
        if profile_code and profile_code in stale_codes:
            delete_fund_primary_sector(profile_code)
            delete_fund_profile(profile_code)


def remove_holding_from_portfolio(
    fund_code: str,
    *,
    fund_name: str | None = None,
) -> dict:
    """从当前账户汇总移除一只基金，并删除对应 fund_profiles / 板块映射。"""
    code = (fund_code or "").strip()
    if not code:
        raise ValueError("fund_code 不能为空")

    snapshot = get_most_recent_portfolio_snapshot()
    if not snapshot:
        raise LookupError("当前没有可删除的持仓快照")

    current = [Holding.model_validate(item) for item in snapshot.get("holdings", [])]
    if not current:
        raise LookupError("当前持仓为空")

    matched_in_snapshot = [item for item in current if _holding_matches_target(item, code, fund_name)]

    if matched_in_snapshot:
        remaining = [item for item in current if not _holding_matches_target(item, code, fund_name)]
    else:
        from app.database import get_fund_profile_by_code

        profile = get_fund_profile_by_code(code) if code != "000000" else None
        if profile is None and fund_name:
            profile = FundProfileService().find_match(fund_name)
        if profile is None:
            raise LookupError("未找到要删除的持仓")
        remaining = current

    remaining = without_test_holdings(remaining)

    total_assets = round(
        sum(
            (item.settled_holding_amount or item.holding_amount) + (item.daily_profit or 0)
            for item in remaining
        ),
        2,
    )
    daily_profit = sum_daily_profit(remaining) if remaining else 0.0
    daily_return_percent = None
    if remaining and total_assets > daily_profit > 0:
        previous = total_assets - daily_profit
        if previous > 0:
            daily_return_percent = round(daily_profit / previous * 100, 2)

    summary = get_portfolio_summary()
    if summary is None:
        summary = PortfolioSummary(
            total_assets=total_assets,
            daily_profit=daily_profit,
            daily_return_percent=daily_return_percent,
            holding_count=len(remaining),
        )
    else:
        summary = summary.model_copy(
            update={
                "total_assets": total_assets,
                "daily_profit": daily_profit,
                "daily_return_percent": daily_return_percent,
                "holding_count": len(remaining),
                "updated_at": datetime.now(timezone.utc),
            }
        )

    save_portfolio_summary(summary)
    save_daily_snapshot(remaining, summary)

    _purge_fund_profile(code, fund_name)
    _reconcile_fund_profiles_with_snapshot(before=current, after=remaining)

    return build_portfolio_holdings_response(
        remaining,
        source="snapshot",
        snapshot_date=snapshot.get("snapshot_date"),
        refreshed_at=summary.updated_at,
    )
