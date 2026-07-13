from __future__ import annotations

from app.models import FundProfile, Holding


class _ProfileNotProvided:
    pass


_PROFILE_NOT_PROVIDED = _ProfileNotProvided()
_ProfileArg = FundProfile | None | _ProfileNotProvided


def serialize_holding_for_client(
    holding: Holding,
    *,
    profile: _ProfileArg = _PROFILE_NOT_PROVIDED,
) -> dict:
    """API 持仓 JSON：持久化字段 + 展示层口径（与 holding_estimates / holding_metrics 一致）。"""
    from app.services.holding_estimates import build_holding_display_metrics
    from app.services.holding_metrics import (
        compute_estimated_daily_return_percent,
        holding_daily_return_is_estimated,
    )
    from app.services.holding_amount_sync import resolve_display_settled_amount
    from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred

    if isinstance(profile, _ProfileNotProvided):
        profile = get_profile_for_holding(holding)

    payload = holding.model_dump()
    display = build_holding_display_metrics(holding, profile=profile)
    settled = resolve_display_settled_amount(holding, profile=profile)
    # 养基宝口径：持有金额=上一交易日结算额；当日收益单独一列。总资产才用 settled+daily。
    payload["settled_holding_amount"] = settled
    payload["display_holding_amount"] = settled
    payload["holding_amount"] = settled
    payload["estimated_holding_return_percent"] = display["estimated_holding_return_percent"]
    payload["estimated_holding_profit"] = display["estimated_holding_profit"]
    payload["holding_return_is_estimated"] = display["holding_return_is_estimated"]
    payload["estimated_daily_return_percent"] = compute_estimated_daily_return_percent(holding)
    payload["daily_return_is_estimated"] = holding_daily_return_is_estimated(
        holding,
        profile=profile,
    )
    payload["profit_accrual_deferred"] = is_profit_accrual_deferred(profile)
    return payload


def serialize_holdings_for_client(
    holdings: list[Holding],
    *,
    matched_profiles: list[FundProfile | None] | None = None,
) -> list[dict]:
    if not holdings:
        return []
    if matched_profiles is None:
        from app.database import list_fund_profiles
        from app.services.fund_profile import match_profiles_to_holdings

        profiles_snapshot = list_fund_profiles()
        matched_profiles = match_profiles_to_holdings(holdings, profiles_snapshot)
    if len(matched_profiles) != len(holdings):
        raise ValueError("matched_profiles must align one-to-one with holdings")
    return [
        serialize_holding_for_client(holding, profile=profile)
        for holding, profile in zip(holdings, matched_profiles, strict=True)
    ]
