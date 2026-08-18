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
            cache_only=True,
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

    response = HoldingDetailResponse(
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
    _remember_holding_detail_cache(resolved, response)
    return response


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


def resolve_holding_list_metrics(
    holding: Holding,
    profile: FundProfile | None,
) -> tuple[float | None, float | None, int | None]:
    """列表用份额/成本/天数：详情缓存 > 档案 > 净值缓存推算。不打外网、不扫快照。"""
    shares: float | None = None
    cost: float | None = None
    days: int | None = None

    cached = _list_metrics_from_detail_cache(holding)
    if cached is not None:
        shares, cost, days = cached

    if shares is None and profile is not None:
        shares = profile.holding_shares
    if cost is None and profile is not None:
        cost = profile.holding_cost

    if shares and shares > 0:
        inferred = _infer_purchase_unit_cost(holding, shares)
        if inferred is not None and inferred > 0:
            if cost is None or _is_imputed_market_unit_cost(cost, holding, shares):
                cost = inferred

    if shares is None:
        shares = _shares_from_cached_nav(holding)

    if cost is None and shares and shares > 0:
        cost_basis = _cost_basis(holding)
        if cost_basis is not None:
            cost = round(cost_basis / shares, 4)

    if days is None:
        days = resolve_holding_days_for_list(profile, holding)

    return shares, cost, days


def resolve_holding_days_for_list(
    profile: FundProfile | None,
    holding: Holding,
) -> int | None:
    """列表用持有天数：不扫快照，避免每次 hydrate 持仓都全表回放。"""
    days, _source = _resolve_holding_days(profile, holding, snapshot_loader=lambda: [])
    return days


def _holding_amount_for_metrics(holding: Holding) -> float:
    amount = (
        holding.settled_holding_amount
        if holding.settled_holding_amount is not None
        else holding.holding_amount
    )
    return float(amount or 0)


def _list_metrics_from_detail_cache(
    holding: Holding,
) -> tuple[float | None, float | None, int | None] | None:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000":
        return None
    from app.request_context import try_get_request_user_id
    from app.services.holding_detail_cache import (
        get_cached_holding_detail,
        holding_detail_fingerprint,
    )

    if try_get_request_user_id() is None:
        return None
    payload = get_cached_holding_detail(
        code,
        holding_detail_fingerprint(
            fund_code=code,
            holding_amount=_holding_amount_for_metrics(holding),
        ),
    )
    if not payload:
        return None

    def _as_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _as_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    shares = _as_float(payload.get("holding_shares"))
    cost = _as_float(payload.get("holding_cost"))
    days = _as_int(payload.get("holding_days"))
    if shares is None and cost is None and days is None:
        return None
    return shares, cost, days


def _shares_from_cached_nav(holding: Holding) -> float | None:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000":
        return None
    amount = _holding_amount_for_metrics(holding)
    if amount <= 0:
        return None
    from app.services.fund_data import FundDataService
    from app.services.fund_nav_service import get_latest_unit_nav

    nav = get_latest_unit_nav(code, allow_fetch=False)
    if nav is None or nav <= 0:
        history = FundDataService().get_nav_history(
            code,
            holding.fund_name,
            trading_days=1,
            cache_only=True,
        )
        if history.source == "akshare" and history.latest_nav and history.latest_nav > 0:
            nav = history.latest_nav
    if nav is None or nav <= 0:
        return None
    return round(amount / nav, 2)


def _remember_holding_detail_cache(holding: Holding, response: HoldingDetailResponse) -> None:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000":
        return
    from app.request_context import try_get_request_user_id
    from app.services.holding_detail_cache import (
        holding_detail_fingerprint,
        save_cached_holding_detail,
    )

    if try_get_request_user_id() is None:
        return
    save_cached_holding_detail(
        code,
        holding_detail_fingerprint(
            fund_code=code,
            holding_amount=_holding_amount_for_metrics(holding),
        ),
        response.model_dump(mode="json"),
    )


def _parse_profile_iso_date(value: str | None) -> date | None:
    raw = (value or "").strip()[:10]
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _calendar_holding_days(start: date, *, today: date | None = None) -> int:
    """自然日持有天数：今天减建仓日。"""
    as_of = today or date.today()
    return max(0, (as_of - start).days)


def _ocr_holding_start_date(profile: FundProfile | None) -> date | None:
    if profile is None or profile.holding_days is None:
        return None
    as_of = _holding_days_as_of_date(profile)
    if as_of is None:
        return None
    return as_of - timedelta(days=max(0, int(profile.holding_days)))


def _snapshot_holding_start_date(
    holding: Holding,
    *,
    snapshots: list[dict[str, Any]] | None,
) -> date | None:
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
        if first_date:
            break
    if not first_date:
        return None
    try:
        return datetime.fromisoformat(first_date).date()
    except ValueError:
        return _parse_profile_iso_date(first_date)


def resolve_holding_start_date(
    profile: FundProfile | None,
    holding: Holding,
    *,
    snapshots: list[dict[str, Any]] | None = None,
) -> tuple[date | None, str | None]:
    """持有起点取各来源中最早的一天。

    加仓导入常把最近成交日写成 ``first_purchase_date``，不能压过更早的
    首次出现日 / OCR 回推日 / 日快照。份额基准日会在同步时改写成当天，不用。
    """
    candidates: list[tuple[date, str]] = []
    if profile is not None:
        purchase = _parse_profile_iso_date(profile.first_purchase_date)
        if purchase is not None:
            candidates.append((purchase, "user"))
        seen = _parse_profile_iso_date(profile.first_seen_date)
        if seen is not None:
            candidates.append((seen, "first_seen"))
        ocr_start = _ocr_holding_start_date(profile)
        if ocr_start is not None:
            candidates.append((ocr_start, "ocr_detail"))
    snapshot_start = (
        _snapshot_holding_start_date(holding, snapshots=snapshots)
        if snapshots is not None
        else None
    )
    if snapshot_start is not None:
        candidates.append((snapshot_start, "snapshot"))
    if not candidates:
        return None, None
    start, source = min(candidates, key=lambda item: item[0])
    return start, source


def _resolve_holding_days(
    profile: FundProfile | None,
    holding: Holding,
    *,
    snapshot_loader: Callable[[], list[dict[str, Any]]] | None = None,
    today: date | None = None,
) -> tuple[int | None, str | None]:
    snapshots = snapshot_loader() if snapshot_loader is not None else None
    start, source = resolve_holding_start_date(
        profile,
        holding,
        snapshots=snapshots,
    )
    if start is None:
        return None, None
    return _calendar_holding_days(start, today=today), source


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
