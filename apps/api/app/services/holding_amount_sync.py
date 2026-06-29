from __future__ import annotations

import logging

from app.database import get_fund_profile_by_code, save_fund_profile
from app.models import FundProfile, Holding
from app.services.fund_estimate_provider import fetch_fund_estimate_quotes
from app.services.fund_nav_service import (
    get_cached_official_nav_return,
    get_latest_unit_nav,
    get_official_nav_return,
)
from app.services.trading_session import get_effective_trade_date

logger = logging.getLogger(__name__)


def _compute_rolled_settled_amount(
    *,
    baseline: float,
    official_return: float,
    shares: float | None,
    official_unit_nav: float | None,
) -> float | None:
    """官方净值公布后滚入结算额：优先 shares×单位净值，否则用昨结算×(1+日涨跌%)。"""
    if shares and official_unit_nav and official_unit_nav > 0:
        return round(shares * official_unit_nav, 2)
    if baseline > 0:
        return round(baseline * (1 + official_return / 100), 2)
    return None


def _resolve_cumulative_return_percent(
    holding: Holding,
    profile: FundProfile | None,
) -> float | None:
    cumulative = holding.holding_return_percent
    if cumulative is None:
        cumulative = holding.return_percent
    if cumulative is None and profile is not None:
        cumulative = profile.holding_return_percent
    return cumulative


def _imputed_market_unit_cost(holding: Holding, shares: float) -> float | None:
    """昨结算/份额（非支付宝持仓成本价）。"""
    if shares <= 0:
        return None
    settled = holding.settled_holding_amount or holding.holding_amount
    if settled and settled > 0:
        return round(settled / shares, 4)
    return None


def _is_imputed_market_unit_cost(
    unit_cost: float,
    holding: Holding,
    shares: float,
    *,
    pre_roll: float | None = None,
) -> bool:
    if shares <= 0 or unit_cost <= 0:
        return False
    tolerance = max(0.002, unit_cost * 0.001)
    candidates: list[float] = []
    for amount in (
        holding.settled_holding_amount,
        holding.holding_amount,
    ):
        if amount and amount > 0:
            candidates.append(round(amount / shares, 4))
    if pre_roll and pre_roll > 0:
        candidates.append(round(pre_roll / shares, 4))
        if abs(unit_cost * shares - pre_roll) <= max(1.0, pre_roll * 0.003):
            return True
    return any(abs(unit_cost - implied) <= tolerance for implied in candidates)


def _is_profit_inflation_artifact(
    holding: Holding,
    settled: float,
    pre_roll: float,
) -> bool:
    """持有收益被误写成「结算额 − 昨结算额」的滚入污染。"""
    if holding.holding_profit is None or settled <= pre_roll:
        return False
    inflation = round(settled - pre_roll, 2)
    if inflation <= 0:
        return False
    return abs(holding.holding_profit - inflation) <= max(1.0, abs(inflation) * 0.02)


def _is_return_polluted_against_pre_roll(holding: Holding, pre_roll: float) -> bool:
    ret = holding.holding_return_percent or holding.return_percent
    if ret is None or holding.holding_profit is None or pre_roll <= 0:
        return False
    implied = round(holding.holding_profit / pre_roll * 100, 2)
    return abs(ret - implied) <= 0.05


def _infer_purchase_unit_cost(
    holding: Holding,
    shares: float,
    *,
    market_amount: float | None = None,
) -> float | None:
    """支付宝口径：持仓成本价 = (持有金额 − 持有收益) / 份额。"""
    if shares <= 0:
        return None
    amount = market_amount or holding.holding_amount or holding.settled_holding_amount or 0
    if amount > 0 and holding.holding_profit is not None:
        total_cost = round(amount - holding.holding_profit, 2)
        if 0 < total_cost < amount:
            return round(total_cost / shares, 4)
    return_percent = holding.holding_return_percent or holding.return_percent
    if amount > 0 and return_percent is not None and shares > 0:
        total_cost = round(amount / (1 + return_percent / 100), 2)
        if total_cost > 0:
            return round(total_cost / shares, 4)
    return None


def _resolve_purchase_unit_cost(
    holding: Holding,
    profile: FundProfile | None,
    shares: float,
    *,
    market_amount: float | None = None,
    pre_roll: float | None = None,
) -> float | None:
    inferred = _infer_purchase_unit_cost(holding, shares, market_amount=market_amount)
    unit = profile.holding_cost if profile else None

    if inferred is not None and inferred > 0:
        if unit is None or unit <= 0:
            return inferred
        if _is_imputed_market_unit_cost(unit, holding, shares, pre_roll=pre_roll):
            return inferred
        if abs(unit - inferred) > max(0.002, inferred * 0.002):
            return inferred

    if unit and unit > 0 and not _is_imputed_market_unit_cost(
        unit,
        holding,
        shares,
        pre_roll=pre_roll,
    ):
        return round(unit, 4)
    if inferred is not None and inferred > 0:
        return inferred
    return round(unit, 4) if unit and unit > 0 else None


def _purchase_cost_total(
    holding: Holding,
    profile: FundProfile | None,
    shares: float,
    *,
    market_amount: float | None = None,
) -> float | None:
    unit = _resolve_purchase_unit_cost(
        holding,
        profile,
        shares,
        market_amount=market_amount,
    )
    if unit is None or shares <= 0:
        return None
    return round(unit * shares, 2)


def _pre_roll_settled(
    holding: Holding,
    profile: FundProfile | None,
    *,
    official_return: float | None,
    shares: float | None = None,
    official_unit_nav: float | None = None,
) -> float:
    """滚入前结算基线：回到上一交易日结算额（非支付宝持仓成本）。"""
    settled = _resolve_settled_amount(holding, profile)

    if shares and official_unit_nav and official_unit_nav > 0 and official_return is not None:
        nav_value = round(shares * official_unit_nav, 2)
        if abs(settled - nav_value) <= max(1.0, nav_value * 0.003):
            return round(settled / (1 + official_return / 100), 2)

    if holding.holding_profit is not None and holding.holding_profit > 0:
        candidate = round(settled - holding.holding_profit, 2)
        if candidate > 0 and _is_profit_inflation_artifact(holding, settled, candidate):
            return candidate

    cumulative_return = _resolve_cumulative_return_percent(holding, profile)
    if (
        cumulative_return is not None
        and profile
        and profile.holding_cost
        and shares
        and shares > 0
    ):
        principal = round(profile.holding_cost * shares, 2)
        inflated = round(principal * (1 + cumulative_return / 100), 2)
        if abs(settled - inflated) <= max(1.0, abs(inflated) * 0.003):
            return principal

    return settled


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
        shares = profile.holding_shares
        patch: dict = {
            "settled_holding_amount": holding.holding_amount,
            "holding_amount": holding.holding_amount,
        }
        if holding.holding_profit is not None:
            patch["holding_profit"] = holding.holding_profit
        return_percent = holding.holding_return_percent or holding.return_percent
        if return_percent is not None:
            patch["holding_return_percent"] = return_percent
        if shares and shares > 0:
            inferred_unit = _infer_purchase_unit_cost(holding, shares)
            if inferred_unit is not None and inferred_unit > 0:
                patch["holding_cost"] = inferred_unit
        if persist_profile:
            save_fund_profile(profile.model_copy(update=patch))
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
    inferred_unit = _infer_purchase_unit_cost(holding, shares)
    if inferred_unit is not None and inferred_unit > 0:
        patch["holding_cost"] = inferred_unit
    elif profile.holding_cost and profile.holding_cost > 0:
        patch["holding_cost"] = profile.holding_cost
    elif cost_basis := _cost_basis(holding, profile):
        if cost_basis > 0:
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
    allow_nav_fetch: bool = True,
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

    from app.services.fund_nav_service import prime_official_nav_cache

    trade_date = get_effective_trade_date()
    prime_official_nav_cache(sorted(codes), trade_date)

    quotes = estimate_quotes
    if quotes is None:
        quotes = fetch_fund_estimate_quotes(holdings, timeout_seconds=6.0)

    updated: list[Holding] = []
    for holding in holdings:
        updated.append(
            _sync_one_holding(
                holding,
                trade_date=trade_date,
                estimate_quote=quotes.get(holding.fund_code or ""),
                persist_profile=persist_profiles,
                shares_override=shares_override,
                allow_nav_fetch=allow_nav_fetch,
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


def _should_skip_official_nav_roll(
    holding: Holding,
    settled: float,
    *,
    official_return: float | None,
    shares: float | None,
    official_unit_nav: float | None,
) -> bool:
    """OCR/官方净值已更新后的持有金额已是当日结算额，勿再按日涨跌滚入。"""
    from app.services.holding_estimates import _ocr_holding_profit_is_cumulative

    if holding.amount_includes_today:
        return True
    if shares and official_unit_nav and official_unit_nav > 0 and settled > 0:
        nav_value = round(shares * official_unit_nav, 2)
        if abs(settled - nav_value) <= max(1.0, nav_value * 0.003):
            return True
    if holding.daily_return_percent_source == "official_nav" and _ocr_holding_profit_is_cumulative(
        holding
    ):
        return True
    return False


def _sync_one_holding(
    holding: Holding,
    *,
    trade_date: str,
    estimate_quote: dict | None,
    persist_profile: bool,
    shares_override: dict[str, float] | None = None,
    allow_nav_fetch: bool = True,
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
            unit_nav = _resolve_unit_nav(
                code,
                trade_date,
                estimate_quote,
                allow_fetch=allow_nav_fetch,
            )
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

    official_return = (
        get_official_nav_return(code, trade_date)
        if allow_nav_fetch
        else get_cached_official_nav_return(code, trade_date)
    )
    official_unit_nav = get_latest_unit_nav(code, allow_fetch=allow_nav_fetch)

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

    if official_return is not None:
        pre_roll = _pre_roll_settled(
            holding,
            profile,
            official_return=official_return,
            shares=shares,
            official_unit_nav=official_unit_nav,
        )
        skip_roll = _should_skip_official_nav_roll(
            holding,
            settled,
            official_return=official_return,
            shares=shares,
            official_unit_nav=official_unit_nav,
        )
        new_settled = (
            settled
            if skip_roll
            else _compute_rolled_settled_amount(
                baseline=pre_roll,
                official_return=official_return,
                shares=shares,
                official_unit_nav=official_unit_nav,
            )
        )
        if new_settled is not None:
            profit_patch = _profit_patch_from_rolled_settled(
                new_settled,
                shares or 0.0,
                profile,
                holding,
                market_amount=new_settled,
                pre_roll=pre_roll,
                settled_before=settled,
            )
            unit_cost = profit_patch.pop("holding_cost", None)
            profile_patch = {
                "settled_holding_amount": new_settled,
                "holding_amount": new_settled,
                **profit_patch,
            }
            if unit_cost is not None:
                profile_patch["holding_cost"] = unit_cost
            if persist_profile and profile is not None:
                save_fund_profile(profile.model_copy(update=profile_patch))
            return holding.model_copy(
                update={
                    "holding_amount": new_settled,
                    "settled_holding_amount": new_settled,
                    "amount_includes_today": holding.amount_includes_today,
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
    *,
    allow_fetch: bool = True,
) -> float | None:
    official_return = (
        get_official_nav_return(fund_code, trade_date)
        if allow_fetch
        else get_cached_official_nav_return(fund_code, trade_date)
    )
    if official_return is not None:
        return get_latest_unit_nav(fund_code, allow_fetch=allow_fetch)

    official = get_latest_unit_nav(fund_code, allow_fetch=allow_fetch)
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
    *,
    market_amount: float | None = None,
    pre_roll: float | None = None,
    settled_before: float | None = None,
) -> dict:
    """支付宝口径：持有收益 = 持有金额 − 持仓成本；收益率 = 收益 / 成本。"""
    from app.services.holding_estimates import _ocr_holding_profit_is_cumulative

    patch: dict = {}
    if shares <= 0:
        return patch

    if _ocr_holding_profit_is_cumulative(holding):
        unit_cost = _infer_purchase_unit_cost(
            holding,
            shares,
            market_amount=market_amount or new_settled,
        )
        if unit_cost is not None and unit_cost > 0:
            patch["holding_cost"] = unit_cost
        return_percent = holding.holding_return_percent or holding.return_percent
        patch.update(
            {
                "holding_profit": holding.holding_profit,
                "holding_return_percent": return_percent,
                "return_percent": return_percent,
            }
        )
        return patch

    settled_anchor = settled_before if settled_before is not None else (
        holding.settled_holding_amount or holding.holding_amount
    )
    profit_is_artifact = (
        pre_roll is not None
        and settled_anchor is not None
        and _is_profit_inflation_artifact(holding, settled_anchor, pre_roll)
    )
    return_is_polluted = pre_roll is not None and _is_return_polluted_against_pre_roll(
        holding,
        pre_roll,
    )

    inference_holding = holding
    if profit_is_artifact:
        inference_holding = holding.model_copy(update={"holding_profit": None})

    unit_cost = _resolve_purchase_unit_cost(
        inference_holding,
        profile,
        shares,
        market_amount=market_amount or new_settled,
        pre_roll=pre_roll,
    )
    if unit_cost is None or unit_cost <= 0:
        if holding.holding_profit is not None and not profit_is_artifact:
            return {
                "holding_profit": holding.holding_profit,
                "holding_return_percent": holding.holding_return_percent or holding.return_percent,
                "return_percent": holding.holding_return_percent or holding.return_percent,
            }
        return patch

    cost_total = round(unit_cost * shares, 2)
    profit = round(new_settled - cost_total, 2)
    return_percent = round(profit / cost_total * 100, 2) if cost_total > 0 else 0.0

    profile_unit = profile.holding_cost if profile else None
    if profile and (
        profile_unit is None
        or (
            pre_roll is not None
            and _is_imputed_market_unit_cost(
                profile_unit,
                holding,
                shares,
                pre_roll=pre_roll,
            )
        )
        or abs(profile_unit - unit_cost) > 0.0005
    ):
        patch["holding_cost"] = unit_cost

    if profit_is_artifact and return_is_polluted:
        # 仅滚入金额；收益/收益率需重新 OCR 或手工修正后再算
        return patch

    patch.update(
        {
            "holding_profit": profit,
            "holding_return_percent": return_percent,
            "return_percent": return_percent,
        }
    )
    return patch


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
