from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from app.database import (
    get_fund_profile_by_code,
    list_fund_profiles,
    list_portfolio_daily_snapshots,
    save_fund_profile,
)
from app.models import FundProfile, Holding, HoldingDetailResponse, PortfolioSummary, SectorQuoteMeta
from app.services.fund_code_resolver import lookup_fund_code_by_name
from app.services.fund_data import FundDataService
from app.services.fund_profile import (
    FundProfileService,
    _aliases_for_name,
    merge_holding_into_profile,
)
from app.services.fund_primary_sector_service import PrimarySectorBatchContext
from app.services.holding_estimates import compute_yesterday_profit
from app.services.holding_amount_sync import (
    _infer_purchase_unit_cost,
    _is_imputed_market_unit_cost,
)


class HoldingDetailDataContext:
    """Request-local profile/snapshot data shared by holding-detail builds."""

    def __init__(self) -> None:
        self._profiles_loaded = False
        self._profiles: list[FundProfile] = []
        self._profiles_by_code: dict[str, FundProfile] = {}
        self._primary_sector_context: PrimarySectorBatchContext | None = None
        self._primary_sector_loaded_codes: frozenset[str] = frozenset()
        self._snapshots_loaded = False
        self._snapshots: list[dict[str, Any]] = []

    @property
    def profiles_by_code(self) -> dict[str, FundProfile] | None:
        if not self._profiles_loaded:
            return None
        return self._profiles_by_code

    def preload_profiles(self) -> None:
        if self._profiles_loaded:
            return
        profiles = list_fund_profiles()
        self._profiles = profiles
        self._profiles_by_code = {profile.fund_code: profile for profile in profiles}
        self._profiles_loaded = True

    def preload_snapshots(self) -> None:
        if self._snapshots_loaded:
            return
        self._snapshots = list_portfolio_daily_snapshots(limit=365)
        self._snapshots_loaded = True

    def preload_primary_sectors(self, holdings: list[Holding]) -> None:
        if self._primary_sector_context is not None or not self._profiles_loaded:
            return

        profile_service = FundProfileService()
        codes: set[str] = set()
        for holding in holdings:
            direct_code = self._primary_sector_code(holding.fund_code)
            if direct_code is not None:
                codes.add(direct_code)
            profile = profile_service._find_profile_in(
                holding,
                by_code=self._profiles_by_code,
                profiles=self._profiles,
            )
            if profile is not None:
                profile_code = self._primary_sector_code(profile.fund_code)
                if profile_code is not None:
                    codes.add(profile_code)

        primary_context = PrimarySectorBatchContext.load(
            codes,
            profiles=self._profiles,
        )
        # Share the mutable map so a profile saved earlier in the batch is visible
        # to later primary-sector fallbacks without another database read.
        primary_context.profiles_by_code = self._profiles_by_code
        self._primary_sector_context = primary_context
        self._primary_sector_loaded_codes = frozenset(codes)

    def primary_sector_context_for(
        self,
        holding: Holding,
        profile: FundProfile | None,
    ) -> PrimarySectorBatchContext | None:
        if self._primary_sector_context is None:
            return None
        raw_code = holding.fund_code
        if raw_code == "000000" and profile is not None:
            raw_code = profile.fund_code
        code = self._primary_sector_code(raw_code)
        if code is None or code not in self._primary_sector_loaded_codes:
            return None
        return self._primary_sector_context

    @staticmethod
    def _primary_sector_code(raw_code: str | None) -> str | None:
        code = str(raw_code or "").strip().zfill(6)
        if len(code) != 6 or code == "000000":
            return None
        return code

    def find_profile(
        self,
        holding: Holding,
        profile_service: FundProfileService,
    ) -> FundProfile | None:
        if self._profiles_loaded:
            return profile_service._find_profile_in(
                holding,
                by_code=self._profiles_by_code,
                profiles=self._profiles,
            )

        profile = (
            get_fund_profile_by_code(holding.fund_code)
            if holding.fund_code != "000000"
            else None
        )
        if profile is None:
            profile = profile_service.find_match(holding.fund_name)
        return profile

    def remember_profile(self, profile: FundProfile) -> None:
        if not self._profiles_loaded:
            return
        existing = self._profiles_by_code.get(profile.fund_code)
        self._profiles_by_code[profile.fund_code] = profile
        if existing is None:
            self._profiles.append(profile)
            return
        for index, item in enumerate(self._profiles):
            if item.fund_code == profile.fund_code:
                self._profiles[index] = profile
                break

    def snapshots(self) -> list[dict[str, Any]]:
        if not self._snapshots_loaded:
            self.preload_snapshots()
        return self._snapshots


def build_holding_detail(
    holdings: list[Holding],
    index: int,
    *,
    portfolio_summary: PortfolioSummary | None = None,
    sector_quote_meta: SectorQuoteMeta | None = None,
    data_context: HoldingDetailDataContext | None = None,
) -> HoldingDetailResponse:
    if index < 0 or index >= len(holdings):
        raise ValueError("持仓索引超出范围")

    holding = holdings[index]
    provenance: dict[str, str] = {}
    profile_service = FundProfileService()
    context = data_context or HoldingDetailDataContext()

    profile = context.find_profile(holding, profile_service)
    resolved = profile_service._resolve_holding_with_profile(
        holding,
        profile,
        fetch_benchmark=True,
        batch_profiles_by_code=context.profiles_by_code,
        primary_sector_batch_context=context.primary_sector_context_for(
            holding,
            profile,
        ),
    )
    fund_code_source: str | None = None
    if resolved.fund_code != holding.fund_code:
        fund_code_source = "profile"
    elif holding.fund_code == "000000":
        looked_up, lookup_source = lookup_fund_code_by_name(holding.fund_name)
        if looked_up:
            resolved = holding.model_copy(update={"fund_code": looked_up})
            fund_code_source = lookup_source or "akshare"
            profile = context.find_profile(resolved, profile_service)
            if profile is None:
                profile = save_fund_profile(
                    FundProfile(
                        fund_code=looked_up,
                        fund_name=holding.fund_name,
                        aliases=_aliases_for_name(holding.fund_name),
                        holding_amount=holding.holding_amount,
                        source="akshare-lookup",
                        is_provisional=False,
                    )
                )
                context.remember_profile(profile)

    holding_shares = profile.holding_shares if profile else None
    holding_cost = profile.holding_cost if profile else None
    yesterday_profit = profile.yesterday_profit if profile else None
    if holding_shares is not None:
        provenance["holding_shares"] = "ocr_detail"
    if holding_cost is not None:
        provenance["holding_cost"] = "ocr_detail"
    if yesterday_profit is not None:
        provenance["yesterday_profit"] = "ocr_detail"

    if holding_shares and holding_shares > 0:
        inferred = _infer_purchase_unit_cost(resolved, holding_shares)
        if inferred is not None and inferred > 0:
            if (
                holding_cost is None
                or _is_imputed_market_unit_cost(holding_cost, resolved, holding_shares)
            ):
                holding_cost = inferred
                provenance["holding_cost"] = "computed"

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
        snapshot_value = _yesterday_profit_from_snapshots(
            resolved,
            snapshots=context.snapshots(),
        )
        if snapshot_value is not None:
            yesterday_profit = snapshot_value
            provenance["yesterday_profit"] = "snapshot"
        else:
            yesterday_profit = compute_yesterday_profit(resolved)
            if yesterday_profit is not None:
                provenance["yesterday_profit"] = "computed"

    holding_days, holding_days_source = _resolve_holding_days(
        profile,
        resolved,
        snapshot_loader=context.snapshots,
    )
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


def _yesterday_profit_from_snapshots(
    holding: Holding,
    *,
    snapshots: list[dict[str, Any]] | None = None,
) -> float | None:
    recent_snapshots = (
        snapshots[:14]
        if snapshots is not None
        else list_portfolio_daily_snapshots(limit=14)
    )
    if len(recent_snapshots) < 2:
        return None

    today_key = date.today().isoformat()
    for snapshot in recent_snapshots[1:]:
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
    *,
    snapshot_loader: Callable[[], list[dict[str, Any]]] | None = None,
) -> tuple[int | None, str | None]:
    if profile and profile.first_purchase_date:
        try:
            purchase = date.fromisoformat(profile.first_purchase_date)
            return max(0, (date.today() - purchase).days), "user"
        except ValueError:
            pass

    anchor = _first_seen_anchor_date(profile)
    if anchor is not None:
        try:
            seen = date.fromisoformat(anchor)
            return max(0, (date.today() - seen).days), "first_seen"
        except ValueError:
            pass

    snapshots = snapshot_loader() if snapshot_loader is not None else None
    snapshot_days = _holding_days_from_snapshots(holding, snapshots=snapshots)
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


def _first_seen_anchor_date(profile: FundProfile | None) -> str | None:
    if profile is None:
        return None
    if profile.first_seen_date:
        anchor = profile.first_seen_date
        if profile.shares_baseline_date:
            try:
                baseline = date.fromisoformat(profile.shares_baseline_date)
                seen = date.fromisoformat(anchor)
                if baseline < seen:
                    anchor = profile.shares_baseline_date
            except ValueError:
                pass
        return anchor
    if profile.shares_baseline_date and not profile.first_purchase_date:
        return profile.shares_baseline_date
    return None


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


def _holding_days_from_snapshots(
    holding: Holding,
    *,
    snapshots: list[dict[str, Any]] | None = None,
) -> int | None:
    if snapshots is None:
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
