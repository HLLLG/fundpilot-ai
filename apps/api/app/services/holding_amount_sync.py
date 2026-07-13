from __future__ import annotations

import logging

from app.database import get_fund_profile_by_code, list_fund_profiles, save_fund_profile
from app.models import FundProfile, Holding
from app.services.fund_estimate_provider import fetch_fund_estimate_quotes
from app.services.fund_nav_service import (
    get_cached_official_nav_return,
    get_latest_unit_nav,
    get_official_nav_return,
)
from app.services.trading_session import get_effective_trade_date

logger = logging.getLogger(__name__)


class _ProfileNotProvided:
    pass


_PROFILE_NOT_PROVIDED = _ProfileNotProvided()


def _load_profiles_by_code(codes: set[str]) -> dict[str, FundProfile]:
    if not codes:
        return {}
    return {
        profile.fund_code.strip(): profile
        for profile in list_fund_profiles()
        if profile.fund_code.strip() in codes
    }


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
    """滚入前结算基线：回到上一交易日结算额（非支付宝持仓成本）。

    只有 shares×官方单位净值 已经等于当前 settled 这一种情况需要"反推"
    （说明 settled 已经是今天滚过的净值口径，反推回滚入前的净值口径基线）；
    其余情况直接信任 settled 本身就是昨日真实结算额，不做任何猜测性改写。

    历史 bug：此前这里还有两段分支，试图用"settled 与 profit/成本×(1+累计
    收益率) 是否数值吻合"来判断 settled 是否被污染成了成本价——但这两个
    判断条件本身就是「成本」「收益率」这两个概念的定义式，对任何数据自洽、
    正常盈利的持仓都恒为真，会把真实的昨日市值错误替换成成本价，导致官方
    净值结算的基线每天都从成本价重新起算，逐日复利失效。真正的"档案污染"
    防护由 ``holding_estimates._repair_corrupted_settled_profit`` 在展示/
    分析层独立处理（用 ``profile.holding_return_percent`` 这个不同来源的
    信号做交叉校验，而不是用同一份数据的自洽性猜测），此处不需要重复防护。
    """
    settled = _resolve_settled_amount(holding, profile)

    if shares and official_unit_nav and official_unit_nav > 0 and official_return is not None:
        nav_value = round(shares * official_unit_nav, 2)
        if abs(settled - nav_value) <= max(1.0, nav_value * 0.003):
            return round(settled / (1 + official_return / 100), 2)

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

    codes = {
        (holding.fund_code or "").strip()
        for holding in holdings
        if (holding.fund_code or "").strip() not in {"", "000000"}
        and holding.holding_amount > 0
    }
    profiles_by_code = _load_profiles_by_code(codes)
    updated: list[Holding] = []
    for holding in holdings:
        code = (holding.fund_code or "").strip()
        profile = profiles_by_code.get(code)
        latest_profile = _bootstrap_profile_baseline(
            holding,
            profile=profile,
            estimate_quote=quotes.get(holding.fund_code or ""),
            persist_profile=persist_profiles,
            force_reset_shares=force_reset_shares,
            skip_network=skip_network,
        )
        if latest_profile is not None:
            profiles_by_code[latest_profile.fund_code.strip()] = latest_profile
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
    profile: FundProfile | None,
    estimate_quote: dict | None,
    persist_profile: bool,
    force_reset_shares: bool,
    skip_network: bool = False,
) -> FundProfile | None:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000" or holding.holding_amount <= 0:
        return profile

    if profile is None:
        return None

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
            return save_fund_profile(profile.model_copy(update=patch))
        return profile

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
            return save_fund_profile(profile.model_copy(update=patch))
        return profile

    unit_nav = _resolve_bootstrap_unit_nav(code, estimate_quote, skip_network=skip_network)
    if unit_nav is None or unit_nav <= 0:
        return profile

    shares = round(holding.holding_amount / unit_nav, 2)
    if shares <= 0:
        return profile

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
        return save_fund_profile(profile.model_copy(update=patch))
    return profile


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
        and holding.holding_amount > 0
    }
    if not codes:
        return holdings

    profiles_by_code = _load_profiles_by_code(codes)

    from app.services.fund_nav_service import prime_official_nav_cache

    trade_date = get_effective_trade_date()
    prime_official_nav_cache(sorted(codes), trade_date, cache_only=not allow_nav_fetch)

    quotes = estimate_quotes
    if quotes is None:
        quotes = fetch_fund_estimate_quotes(holdings, timeout_seconds=6.0)

    updated: list[Holding] = []
    for holding in holdings:
        code = (holding.fund_code or "").strip()
        synced_holding, latest_profile = _sync_one_holding(
            holding,
            profile=profiles_by_code.get(code),
            trade_date=trade_date,
            estimate_quote=quotes.get(holding.fund_code or ""),
            persist_profile=persist_profiles,
            shares_override=shares_override,
            allow_nav_fetch=allow_nav_fetch,
        )
        updated.append(synced_holding)
        if latest_profile is not None:
            profiles_by_code[latest_profile.fund_code.strip()] = latest_profile
    return updated


def _resolve_settled_amount(holding: Holding, profile: FundProfile | None) -> float:
    """养基宝口径：盘中展示上一交易日结算额，不用 profile.holding_amount（可能被旧 sync 污染）。"""
    if holding.settled_holding_amount is not None:
        return holding.settled_holding_amount
    if profile and profile.settled_holding_amount is not None:
        return profile.settled_holding_amount
    return holding.holding_amount


def resolve_display_settled_amount(
    holding: Holding,
    *,
    profile: FundProfile | None | _ProfileNotProvided = _PROFILE_NOT_PROVIDED,
) -> float:
    """API/展示层：上一交易日结算持有金额。"""
    if isinstance(profile, _ProfileNotProvided):
        profile = get_fund_profile_by_code(holding.fund_code) if holding.fund_code else None
    return _resolve_settled_amount(holding, profile)


def _pin_intraday_settled(
    holding: Holding,
    profile: FundProfile | None,
    settled: float,
    *,
    persist_profile: bool,
) -> tuple[Holding, FundProfile | None]:
    """盘中锁定 holding/settled；顺带修复档案里被污染的 holding_amount。"""
    patch = {
        "holding_amount": settled,
        "settled_holding_amount": settled,
        "amount_includes_today": False,
    }
    if persist_profile and profile is not None:
        profile = save_fund_profile(
            profile.model_copy(
                update={
                    "settled_holding_amount": settled,
                    "holding_amount": settled,
                }
            )
        )
    return holding.model_copy(update=patch), profile


def _should_skip_official_nav_roll(
    holding: Holding,
    settled: float,
    *,
    official_return: float | None,
    shares: float | None,
    official_unit_nav: float | None,
    profile: FundProfile | None,
    trade_date: str,
) -> bool:
    """本交易日是否已经用官方净值结算过：用 profile.profit_settled_trade_date
    显式状态标记判断，同一天多次调用幂等跳过。

    历史 bug：此前用 ``holding.amount_includes_today`` 永久信任（一旦为真、
    快照持久化后没有任何重置逻辑，第二天读回仍为真）以及
    ``_ocr_holding_profit_is_cumulative`` 数学恒等式（只要 amount/profit/
    return% 三者自洽即为真——系统自己结算写回的新三元组同样自洽）作为跳过
    信号，两者任一都会在第一次正确结算后对所有后续交易日永久返回 True，
    导致持有收益/结算金额从 OCR 上传起被冻结，官方净值公布后完全不会滚入。
    """
    if profile is not None and profile.profit_settled_trade_date == trade_date:
        return True
    if shares and official_unit_nav and official_unit_nav > 0 and settled > 0:
        nav_value = round(shares * official_unit_nav, 2)
        if abs(settled - nav_value) <= max(1.0, nav_value * 0.003):
            return True
    return False


def _sync_one_holding(
    holding: Holding,
    *,
    profile: FundProfile | None,
    trade_date: str,
    estimate_quote: dict | None,
    persist_profile: bool,
    shares_override: dict[str, float] | None = None,
    allow_nav_fetch: bool = True,
) -> tuple[Holding, FundProfile | None]:
    code = (holding.fund_code or "").strip()
    if not code or code == "000000" or holding.holding_amount <= 0:
        return holding, profile

    override_value = shares_override.get(code) if shares_override else None
    if override_value is not None:
        shares = override_value
    else:
        shares = profile.holding_shares if profile else None

    # 清仓：账本有效份额 ≤ 0 → 金额归零（不写回档案，由展示层过滤）。
    if override_value is not None and override_value <= 0:
        if holding.holding_amount == 0:
            return holding, profile
        return (
            holding.model_copy(
                update={
                    "holding_amount": 0.0,
                    "settled_holding_amount": 0.0,
                    "amount_includes_today": False,
                }
            ),
            profile,
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
                    profile = save_fund_profile(
                        profile.model_copy(update={"holding_shares": shares})
                    )

    settled = _resolve_settled_amount(holding, profile)
    from app.services.profit_accrual_defer import is_profit_accrual_deferred

    if is_profit_accrual_deferred(profile):
        locked = holding.holding_amount if holding.holding_amount > 0 else settled
        if abs(locked - holding.holding_amount) > 0.01 or holding.amount_includes_today is not False:
            return (
                holding.model_copy(
                    update={
                        "holding_amount": locked,
                        "settled_holding_amount": locked,
                        "amount_includes_today": False,
                    }
                ),
                profile,
            )
        return holding, profile

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
            profile = save_fund_profile(
                profile.model_copy(
                    update={
                        "settled_holding_amount": new_settled,
                        "holding_amount": new_settled,
                    }
                )
            )
        return (
            holding.model_copy(
                update={
                    "holding_amount": new_settled,
                    "settled_holding_amount": new_settled,
                    "amount_includes_today": False,
                }
            ),
            profile,
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
            profile=profile,
            trade_date=trade_date,
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
                skip_roll=skip_roll,
            )
            unit_cost = profit_patch.pop("holding_cost", None)
            profile_patch = {
                "settled_holding_amount": new_settled,
                "holding_amount": new_settled,
                "profit_settled_trade_date": trade_date,
                **profit_patch,
            }
            if unit_cost is not None:
                profile_patch["holding_cost"] = unit_cost
            if persist_profile and profile is not None:
                profile = save_fund_profile(profile.model_copy(update=profile_patch))
            return (
                holding.model_copy(
                    update={
                        "holding_amount": new_settled,
                        "settled_holding_amount": new_settled,
                        "amount_includes_today": holding.amount_includes_today,
                        **profit_patch,
                    }
                ),
                profile,
            )

    if abs(settled - holding.holding_amount) > 0.01 or holding.amount_includes_today is not False:
        return _pin_intraday_settled(holding, profile, settled, persist_profile=persist_profile)
    if official_return is None:
        return _pin_intraday_settled(holding, profile, settled, persist_profile=persist_profile)
    return holding, profile


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
    skip_roll: bool = False,
) -> dict:
    """支付宝口径：持有收益 = 持有金额 − 持仓成本；收益率 = 收益 / 成本。

    ``skip_roll``：本次结算被 ``_should_skip_official_nav_roll`` 判定为"本交易日
    已结算过、幂等跳过"（而不是"从未结算过、需要按新净值重算"）时为 True，此时
    原样保留 holding 上已有的 profit/收益率，不重新推导（避免同一天内多次调用
    因成本推断的微小误差而漂移）。

    历史 bug：此处曾用 ``_ocr_holding_profit_is_cumulative(holding)``
    （检测 amount/profit/return% 是否自洽的数学恒等式）代替显式的
    ``skip_roll`` 信号——但系统自己结算写回的新三元组同样自洽，导致该判定从
    第一次结算起永久为真，profit 被冻死，官方净值公布后无法再往前滚。
    """
    patch: dict = {}
    if shares <= 0:
        return patch

    if skip_roll:
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

    # 成本基数（cost_total）本质是不变量：只有加减仓才会改变它，官方净值结算
    # 只应该改变「总值」（new_settled），不应该反过来改变「成本」。因此这里优先
    # 直接信任档案里持久化的固定成本价 profile.holding_cost（先用
    # _is_imputed_market_unit_cost 排除它本身被污染成"市值/份额"这种非真实
    # 成本价的情况——这是 2026-06-30 那次档案污染 bug 的合法防护，继续保留），
    # 用它算 profit = new_settled − cost_total、return% = profit / cost_total。
    #
    # 历史 bug：此前的实现会把 holding 上「当前」的 holding_profit/收益率
    # （在这条调用路径里其实是上一次结算/OCR 留下的旧值）和 new_settled
    # （今天刚滚出来的新总值）混在一起反推成本——新旧数据时间点不一致，反推
    # 出来的成本是错的；而用来"识破这个错误"的 profit_is_artifact /
    # return_is_polluted 判定本身又是另一个数学恒等式（构造 candidate=settled
    # −profit 后再检测 profit 是否等于 settled−candidate，代数上必然成立），
    # 命中后直接放弃重算、原样保留旧 profit——等价于把 profit 冻死。
    #
    # 只有当档案完全没有可信的固定成本价时（真正意义上的"首次结算"），才退回
    # 用 holding 自身当前数据反推成本——此时 holding 上的数据是刚确认的新鲜值，
    # 不存在新旧时间点错配的问题。
    profile_unit_cost = profile.holding_cost if profile else None
    profile_cost_trustworthy = (
        profile_unit_cost is not None
        and profile_unit_cost > 0
        and not _is_imputed_market_unit_cost(
            profile_unit_cost,
            holding,
            shares,
            pre_roll=pre_roll,
        )
    )

    if profile_cost_trustworthy:
        unit_cost = profile_unit_cost
    else:
        # 兜底：档案完全没有可信固定成本价时，用 holding 自身当前数据反推。
        # 必须显式传 market_amount=None，让 _infer_purchase_unit_cost 内部退回
        # 用 holding.holding_amount/settled_holding_amount（holding 自己的旧
        # 金额）配 holding.holding_profit（同一时间点的旧收益）——两者时间点
        # 一致才能推出正确成本。绝不能传 new_settled（今天刚滚出来的新总值）
        # 去配旧 profit，那是新旧数据时间点错配的第五处同类恒等式：
        # cost = new_settled − old_profit，代回 profit = new_settled − cost
        # 精确还原 old_profit，等于又把 profit 冻死一次。
        unit_cost = _infer_purchase_unit_cost(holding, shares, market_amount=None)
        if unit_cost is None or unit_cost <= 0:
            unit_cost = profile_unit_cost if profile_unit_cost and profile_unit_cost > 0 else None

    if unit_cost is None or unit_cost <= 0:
        return patch

    cost_total = round(unit_cost * shares, 2)
    profit = round(new_settled - cost_total, 2)
    return_percent = round(profit / cost_total * 100, 2) if cost_total > 0 else 0.0

    if not profile_cost_trustworthy or abs((profile_unit_cost or 0) - unit_cost) > 0.0005:
        patch["holding_cost"] = unit_cost

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
