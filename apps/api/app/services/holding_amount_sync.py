from __future__ import annotations

import logging

from app.database import get_fund_profile_by_code, save_fund_profile
from app.models import FundProfile, Holding
from app.services.fund_estimate_provider import fetch_fund_estimate_quotes
from app.services.fund_nav_service import get_latest_unit_nav, get_official_nav_return
from app.services.trading_session import get_effective_trade_date

logger = logging.getLogger(__name__)


def bootstrap_holding_baselines(
    holdings: list[Holding],
    *,
    estimate_quotes: dict[str, dict] | None = None,
    persist_profiles: bool = True,
    force_reset_shares: bool = False,
    skip_network: bool = False,
) -> list[Holding]:
    """用户确认 OCR 后：用截图金额锁定份额与成本，供后续按交易日自动推算。"""
    if not holdings:
        return holdings

    quotes = estimate_quotes
    if quotes is None and not skip_network:
        quotes = fetch_fund_estimate_quotes(holdings, timeout_seconds=6.0)
    elif quotes is None:
        quotes = {}

    updated: list[Holding] = []
    for holding in holdings:
        _bootstrap_profile_baseline(
            holding,
            estimate_quote=quotes.get(holding.fund_code or ""),
            persist_profile=persist_profiles,
            force_reset_shares=force_reset_shares,
            skip_network=skip_network,
        )
        updated.append(
            holding.model_copy(
                update={
                    "settled_holding_amount": holding.holding_amount,
                    "amount_includes_today": False,
                }
            )
        )
    return updated


def _bootstrap_profile_baseline(
    holding: Holding,
    *,
    estimate_quote: dict | None,
    persist_profile: bool,
    force_reset_shares: bool,
    skip_network: bool = False,
) -> None:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000" or holding.holding_amount <= 0:
        return

    profile = get_fund_profile_by_code(code)
    if profile is None:
        return

    from app.services.profit_accrual_defer import is_profit_accrual_deferred, resolve_profile_defer_patch

    pending_defer = resolve_profile_defer_patch(holding, profile).get(
        "profit_accrual_deferred_until"
    )
    if pending_defer or is_profit_accrual_deferred(profile):
        patch = {
            "settled_holding_amount": holding.holding_amount,
            "holding_amount": holding.holding_amount,
            "holding_shares": None,
        }
        if pending_defer:
            patch["profit_accrual_deferred_until"] = pending_defer
        if holding.holding_profit is not None:
            patch["holding_profit"] = holding.holding_profit
        return_percent = holding.holding_return_percent
        if return_percent is None:
            return_percent = holding.return_percent
        if return_percent is not None:
            patch["holding_return_percent"] = return_percent
        if persist_profile:
            save_fund_profile(profile.model_copy(update=patch))
        return

    if profile.holding_shares is not None and not force_reset_shares:
        return

    unit_nav = _resolve_bootstrap_unit_nav(code, estimate_quote, skip_network=skip_network)
    if unit_nav is None or unit_nav <= 0:
        return

    shares = round(holding.holding_amount / unit_nav, 2)
    if shares <= 0:
        return

    patch: dict = {
        "holding_shares": shares,
        "shares_baseline_date": get_effective_trade_date(),
        "settled_holding_amount": holding.holding_amount,
        "holding_amount": holding.holding_amount,
    }
    cost_basis = _cost_basis(holding, profile)
    if cost_basis is not None and cost_basis > 0:
        patch["holding_cost"] = round(cost_basis / shares, 4)
    if holding.holding_profit is not None:
        patch["holding_profit"] = holding.holding_profit
    return_percent = holding.holding_return_percent
    if return_percent is None:
        return_percent = holding.return_percent
    if return_percent is not None:
        patch["holding_return_percent"] = return_percent

    if persist_profile:
        save_fund_profile(profile.model_copy(update=patch))


def _resolve_bootstrap_unit_nav(
    fund_code: str,
    estimate_quote: dict | None,
    *,
    skip_network: bool = False,
) -> float | None:
    """OCR 锁定份额时用最近已公布官方净值，不用盘中估值（避免份额漂移）。"""
    from app.services.fund_nav_service import peek_cached_unit_nav

    if skip_network:
        official = peek_cached_unit_nav(fund_code)
    else:
        official = get_latest_unit_nav(fund_code)
    if official is not None and official > 0:
        return official
    if estimate_quote:
        previous = estimate_quote.get("previous_nav")
        if previous is not None and float(previous) > 0:
            return round(float(previous), 4)
        estimated = estimate_quote.get("estimated_nav")
        if estimated is not None and float(estimated) > 0:
            return round(float(estimated), 4)
    return None


def sync_holding_amounts_from_shares(
    holdings: list[Holding],
    *,
    estimate_quotes: dict[str, dict] | None = None,
    persist_profiles: bool = True,
    shares_override: dict[str, float] | None = None,
) -> list[Holding]:
    """按档案份额同步结算持有金额：盘中保持上一交易日结算值，官方净值公布后滚入。

    ``shares_override``：账本算出的有效份额覆盖表（基线 + 事件流）；命中则优先使用，
    不写回档案（基线不可变）。
    """
    if not holdings:
        return holdings

    codes = {
        holding.fund_code.strip()
        for holding in holdings
        if (holding.fund_code or "").strip() and holding.fund_code != "000000"
    }
    if not codes:
        return holdings

    quotes = estimate_quotes
    if quotes is None:
        quotes = fetch_fund_estimate_quotes(holdings, timeout_seconds=6.0)

    trade_date = get_effective_trade_date()
    updated: list[Holding] = []
    for holding in holdings:
        updated.append(
            _sync_one_holding(
                holding,
                trade_date=trade_date,
                estimate_quote=quotes.get(holding.fund_code or ""),
                persist_profile=persist_profiles,
                shares_override=shares_override,
            )
        )
    return updated


def _resolve_settled_amount(holding: Holding, profile: FundProfile | None) -> float:
    """养基宝口径：盘中展示上一交易日结算额，不用 profile.holding_amount（可能被旧 sync 污染）。"""
    if holding.settled_holding_amount is not None:
        return holding.settled_holding_amount
    if profile and profile.settled_holding_amount is not None:
        return profile.settled_holding_amount
    return holding.holding_amount


def resolve_display_settled_amount(holding: Holding) -> float:
    """API/展示层：上一交易日结算持有金额。"""
    profile = get_fund_profile_by_code(holding.fund_code) if holding.fund_code else None
    return _resolve_settled_amount(holding, profile)


def _pin_intraday_settled(
    holding: Holding,
    profile: FundProfile | None,
    settled: float,
    *,
    persist_profile: bool,
) -> Holding:
    """盘中锁定 holding/settled；顺带修复档案里被污染的 holding_amount。"""
    patch = {
        "holding_amount": settled,
        "settled_holding_amount": settled,
        "amount_includes_today": False,
    }
    if persist_profile and profile is not None:
        save_fund_profile(
            profile.model_copy(
                update={
                    "settled_holding_amount": settled,
                    "holding_amount": settled,
                }
            )
        )
    return holding.model_copy(update=patch)


def _sync_one_holding(
    holding: Holding,
    *,
    trade_date: str,
    estimate_quote: dict | None,
    persist_profile: bool,
    shares_override: dict[str, float] | None = None,
) -> Holding:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000" or holding.holding_amount <= 0:
        return holding

    profile = get_fund_profile_by_code(code)
    override_value = shares_override.get(code) if shares_override else None
    if override_value is not None:
        shares = override_value
    else:
        shares = profile.holding_shares if profile else None

    # 清仓：账本有效份额 ≤ 0 → 金额归零（不写回档案，由展示层过滤）。
    if override_value is not None and override_value <= 0:
        if holding.holding_amount == 0:
            return holding
        return holding.model_copy(
            update={
                "holding_amount": 0.0,
                "settled_holding_amount": 0.0,
                "amount_includes_today": False,
            }
        )

    if override_value is None and shares is None and profile and profile.holding_shares is None:
        from app.services.profit_accrual_defer import is_profit_accrual_deferred

        if not is_profit_accrual_deferred(profile):
            unit_nav = _resolve_unit_nav(code, trade_date, estimate_quote)
            if unit_nav and unit_nav > 0 and holding.holding_amount > 0:
                shares = round(holding.holding_amount / unit_nav, 2)
                if persist_profile:
                    save_fund_profile(profile.model_copy(update={"holding_shares": shares}))

    settled = _resolve_settled_amount(holding, profile)
    from app.services.profit_accrual_defer import is_profit_accrual_deferred

    if is_profit_accrual_deferred(profile):
        locked = holding.holding_amount if holding.holding_amount > 0 else settled
        if abs(locked - holding.holding_amount) > 0.01 or holding.amount_includes_today is not False:
            return holding.model_copy(
                update={
                    "holding_amount": locked,
                    "settled_holding_amount": locked,
                    "amount_includes_today": False,
                }
            )
        return holding

    official_return = get_official_nav_return(code, trade_date)
    official_unit_nav = get_latest_unit_nav(code)

    # 交易账本有效份额变化：按最新官方净值重算结算基线（用户确认加减仓）。
    if override_value is not None and shares and official_unit_nav and official_unit_nav > 0:
        new_settled = round(shares * official_unit_nav, 2)
        if persist_profile and profile is not None:
            save_fund_profile(
                profile.model_copy(
                    update={
                        "settled_holding_amount": new_settled,
                        "holding_amount": new_settled,
                    }
                )
            )
        return holding.model_copy(
            update={
                "holding_amount": new_settled,
                "settled_holding_amount": new_settled,
                "amount_includes_today": False,
            }
        )

    if shares and official_unit_nav and official_unit_nav > 0:
        new_settled = round(shares * official_unit_nav, 2)
        # 仅当日官方净值已公布时滚入；盘中保持上一交易日结算额（不因 shares×昨净值漂移抬升）
        should_roll = official_return is not None
        if should_roll:
            profit_patch = _profit_patch_from_rolled_settled(
                new_settled,
                shares,
                profile,
                holding,
            )
            profile_patch = {
                "settled_holding_amount": new_settled,
                "holding_amount": new_settled,
                **profit_patch,
            }
            if persist_profile and profile is not None:
                save_fund_profile(profile.model_copy(update=profile_patch))
            return holding.model_copy(
                update={
                    "holding_amount": new_settled,
                    "settled_holding_amount": new_settled,
                    "amount_includes_today": False,
                    **profit_patch,
                }
            )

    if abs(settled - holding.holding_amount) > 0.01 or holding.amount_includes_today is not False:
        return _pin_intraday_settled(holding, profile, settled, persist_profile=persist_profile)
    if official_return is None:
        return _pin_intraday_settled(holding, profile, settled, persist_profile=persist_profile)
    return holding


def _resolve_unit_nav(
    fund_code: str,
    trade_date: str,
    estimate_quote: dict | None,
) -> float | None:
    official_return = get_official_nav_return(fund_code, trade_date)
    if official_return is not None:
        return get_latest_unit_nav(fund_code)

    official = get_latest_unit_nav(fund_code)
    if official is not None and official > 0:
        return official

    if estimate_quote:
        previous = estimate_quote.get("previous_nav")
        if previous is not None and float(previous) > 0:
            return round(float(previous), 4)

    return official


_COST_BASIS_DRIFT_TOLERANCE = 0.05


def _cost_basis_from_return(holding: Holding) -> float | None:
    return_percent = holding.holding_return_percent
    if return_percent is None:
        return_percent = holding.return_percent
    settled = _resolve_settled_amount(holding, None)
    if return_percent is None or settled <= 0:
        return None
    return round(settled / (1 + return_percent / 100), 2)


def _profit_patch_from_rolled_settled(
    new_settled: float,
    shares: float,
    profile: FundProfile | None,
    holding: Holding,
) -> dict:
    """份额×最近官方净值后，按成本重算昨日结算持有收益（对齐支付宝持有收益列）。"""
    if profile and profile.holding_cost and profile.holding_cost > 0 and shares > 0:
        cost = round(profile.holding_cost * shares, 2)
        if cost > 0:
            profit = round(new_settled - cost, 2)
            return_percent = round(profit / cost * 100, 2)
            return {
                "holding_profit": profit,
                "holding_return_percent": return_percent,
                "return_percent": return_percent,
            }
    if holding.holding_profit is not None:
        return {
            "holding_profit": holding.holding_profit,
            "holding_return_percent": holding.holding_return_percent or holding.return_percent,
            "return_percent": holding.holding_return_percent or holding.return_percent,
        }
    return {}


def _cost_basis(holding: Holding, profile: FundProfile | None) -> float | None:
    """成本基数：档案单位成本×份额须与 OCR 昨日结算收益率一致，否则以收益率为准。"""
    derived = _cost_basis_from_return(holding)
    if profile and profile.holding_cost and profile.holding_shares:
        profile_basis = round(profile.holding_cost * profile.holding_shares, 2)
        if derived is not None and derived > 0:
            drift = abs(profile_basis - derived) / derived
            if drift > _COST_BASIS_DRIFT_TOLERANCE:
                logger.info(
                    "cost basis drift for %s: profile=%.2f derived=%.2f, using derived",
                    holding.fund_code,
                    profile_basis,
                    derived,
                )
                return derived
        return profile_basis
    return derived
