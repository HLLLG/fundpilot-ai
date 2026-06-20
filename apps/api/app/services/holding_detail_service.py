from __future__ import annotations

from datetime import date, datetime

from app.database import get_fund_profile_by_code, list_portfolio_daily_snapshots, save_fund_profile
from app.models import FundProfile, Holding, HoldingDetailResponse, PortfolioSummary, SectorQuoteMeta
from app.services.fund_code_resolver import lookup_fund_code_by_name
from app.services.fund_data import FundDataService
from app.services.fund_profile import FundProfileService, _aliases_for_name, merge_holding_into_profile
from app.services.holding_estimates import compute_yesterday_profit


def build_holding_detail(
    holdings: list[Holding],
    index: int,
    *,
    portfolio_summary: PortfolioSummary | None = None,
    sector_quote_meta: SectorQuoteMeta | None = None,
) -> HoldingDetailResponse:
    if index < 0 or index >= len(holdings):
        raise ValueError("持仓索引超出范围")

    holding = holdings[index]
    provenance: dict[str, str] = {}
    profile_service = FundProfileService()

    resolved = profile_service.resolve_holding(holding)
    fund_code_source: str | None = None
    if resolved.fund_code != holding.fund_code:
        fund_code_source = "profile"
    elif holding.fund_code == "000000":
        looked_up = lookup_fund_code_by_name(holding.fund_name)
        if looked_up:
            resolved = holding.model_copy(update={"fund_code": looked_up})
            fund_code_source = "akshare"
            existing = get_fund_profile_by_code(looked_up) or profile_service.find_match(holding.fund_name)
            if existing is None:
                save_fund_profile(
                    FundProfile(
                        fund_code=looked_up,
                        fund_name=holding.fund_name,
                        aliases=_aliases_for_name(holding.fund_name),
                        holding_amount=holding.holding_amount,
                        source="akshare-lookup",
                        is_provisional=False,
                    )
                )

    profile = get_fund_profile_by_code(resolved.fund_code)
    if profile is None:
        profile = profile_service.find_match(resolved.fund_name)

    holding_shares = profile.holding_shares if profile else None
    holding_cost = profile.holding_cost if profile else None
    yesterday_profit = profile.yesterday_profit if profile else None
    if holding_shares is not None:
        provenance["holding_shares"] = "ocr_detail"
    if holding_cost is not None:
        provenance["holding_cost"] = "ocr_detail"
    if yesterday_profit is not None:
        provenance["yesterday_profit"] = "ocr_detail"

    latest_nav: float | None = None
    nav_date: str | None = None
    year_return_percent: float | None = None

    if resolved.fund_code != "000000":
        history = FundDataService().get_nav_history(
            resolved.fund_code,
            resolved.fund_name,
            trading_days=252,
        )
        if history.source == "akshare" and history.points:
            latest_nav = history.latest_nav
            nav_date = history.latest_date
            year_return_percent = history.period_change_percent

            if holding_shares is None and latest_nav and latest_nav > 0 and resolved.holding_amount > 0:
                holding_shares = round(resolved.holding_amount / latest_nav, 2)
                provenance["holding_shares"] = "nav"

            if holding_cost is None and holding_shares and holding_shares > 0:
                cost_basis = _cost_basis(resolved)
                if cost_basis is not None:
                    holding_cost = round(cost_basis / holding_shares, 4)
                    provenance["holding_cost"] = "computed"

            if yesterday_profit is None:
                yesterday_profit = compute_yesterday_profit(resolved)
                if yesterday_profit is not None:
                    provenance["yesterday_profit"] = "nav"

    if yesterday_profit is None:
        snapshot_value = _yesterday_profit_from_snapshots(resolved)
        if snapshot_value is not None:
            yesterday_profit = snapshot_value
            provenance["yesterday_profit"] = "snapshot"
        else:
            yesterday_profit = compute_yesterday_profit(resolved)
            if yesterday_profit is not None:
                provenance["yesterday_profit"] = "computed"

    holding_days, holding_days_source = _resolve_holding_days(profile, resolved)
    if holding_days_source is not None:
        provenance["holding_days"] = holding_days_source
    first_purchase_date = profile.first_purchase_date if profile else None

    total_assets = portfolio_summary.total_assets if portfolio_summary else None
    if total_assets is None:
        total_assets = sum(item.holding_amount for item in holdings) or None

    return HoldingDetailResponse(
        index=index,
        holding=resolved,
        holding_shares=holding_shares,
        holding_cost=holding_cost,
        yesterday_profit=yesterday_profit,
        holding_days=holding_days,
        first_purchase_date=first_purchase_date,
        latest_nav=latest_nav,
        nav_date=nav_date,
        year_return_percent=year_return_percent,
        fund_code_resolved=resolved.fund_code != "000000",
        fund_code_source=fund_code_source,
        provenance=provenance,
    )


def _cost_basis(holding: Holding) -> float | None:
    return_percent = holding.holding_return_percent
    if return_percent is None:
        return_percent = holding.return_percent
    if return_percent is None or holding.holding_amount <= 0:
        return None
    return round(holding.holding_amount / (1 + return_percent / 100), 2)


def _yesterday_profit_from_snapshots(holding: Holding) -> float | None:
    snapshots = list_portfolio_daily_snapshots(limit=14)
    if len(snapshots) < 2:
        return None

    today_key = date.today().isoformat()
    for snapshot in snapshots[1:]:
        if snapshot.get("snapshot_date") == today_key:
            continue
        for item in snapshot.get("holdings") or []:
            if _holding_matches(item, holding):
                daily_profit = item.get("daily_profit")
                if daily_profit is not None:
                    return round(float(daily_profit), 2)
    return None


def _resolve_holding_days(
    profile: FundProfile | None,
    holding: Holding,
) -> tuple[int | None, str | None]:
    if profile and profile.first_purchase_date:
        try:
            purchase = date.fromisoformat(profile.first_purchase_date)
            return max(0, (date.today() - purchase).days), "user"
        except ValueError:
            pass

    if profile and profile.first_seen_date:
        try:
            seen = date.fromisoformat(profile.first_seen_date)
            return max(0, (date.today() - seen).days), "first_seen"
        except ValueError:
            pass

    snapshot_days = _holding_days_from_snapshots(holding)
    ocr_days = profile.holding_days if profile else None
    aged_ocr_days: int | None = None

    if ocr_days is not None:
        as_of = _holding_days_as_of_date(profile)
        if as_of is not None:
            aged_ocr_days = ocr_days + max(0, (date.today() - as_of).days)
        else:
            aged_ocr_days = ocr_days

    if snapshot_days is not None and aged_ocr_days is not None:
        if snapshot_days >= aged_ocr_days:
            return snapshot_days, "snapshot"
        return aged_ocr_days, "ocr_detail"
    if aged_ocr_days is not None:
        return aged_ocr_days, "ocr_detail"
    if snapshot_days is not None:
        return snapshot_days, "snapshot"
    return None, None


def _holding_days_as_of_date(profile: FundProfile | None) -> date | None:
    if profile is None or profile.holding_days is None:
        return None
    if profile.holding_days_as_of:
        try:
            return date.fromisoformat(profile.holding_days_as_of)
        except ValueError:
            pass
    # Legacy profiles: anchor aging from today so the value starts growing tomorrow.
    return date.today()


def _holding_days_from_snapshots(holding: Holding) -> int | None:
    snapshots = list_portfolio_daily_snapshots(limit=365)
    if not snapshots:
        return None

    first_date: str | None = None
    for snapshot in reversed(snapshots):
        for item in snapshot.get("holdings") or []:
            if _holding_matches(item, holding):
                first_date = str(snapshot.get("snapshot_date") or "")
                break
    if not first_date:
        return None

    try:
        start = datetime.fromisoformat(first_date).date()
    except ValueError:
        return None
    return max(0, (date.today() - start).days)


def _holding_matches(item: dict, holding: Holding) -> bool:
    code = str(item.get("fund_code") or "")
    if code and code != "000000" and code == holding.fund_code:
        return True
    name = str(item.get("fund_name") or "")
    return _normalize_holding_name(name) == _normalize_holding_name(holding.fund_name)


def _normalize_holding_name(name: str) -> str:
    return (
        name.replace("...", "")
        .replace(".", "")
        .replace("·", "")
        .replace(" ", "")
        .strip()
    )
