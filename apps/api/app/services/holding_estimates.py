from __future__ import annotations

from app.models import Holding


def clear_client_daily_estimate_fields(holding: Holding) -> Holding:
    """确认入库前剥离客户端/OCR 自带的「当日」字段。

    支付宝截图「日收益」语义为上一交易日官方净值结算收益，解析在 ``yesterday_profit``；
    当日收益仅由板块刷新或当日官方净值覆盖写入。
    「今日收益更新」时 OCR 已写入 ``daily_profit`` + ``official_nav``，须保留。
    """
    if (
        holding.daily_profit is not None
        and holding.daily_return_percent_source == "official_nav"
        and holding.amount_includes_today
    ):
        return holding
    patch: dict = {
        "daily_profit": None,
        "daily_return_percent": None,
        "daily_return_percent_source": None,
    }
    if holding.yesterday_profit is None and holding.daily_profit is not None:
        patch["yesterday_profit"] = holding.daily_profit
    return holding.model_copy(update=patch)


def clear_client_daily_estimate_fields_batch(holdings: list[Holding]) -> list[Holding]:
    return [clear_client_daily_estimate_fields(holding) for holding in holdings]


def _round2(value: float) -> float:
    return round(value, 2)


def _ocr_holding_profit_is_cumulative(holding: Holding) -> bool:
    """支付宝 OCR：持有收益与持有收益率均相对当前持有金额，已是含当日的累计值。"""
    if holding.holding_profit is None:
        return False
    return_percent = resolve_holding_return_percent(holding)
    amount = holding.settled_holding_amount or holding.holding_amount
    if return_percent is None or amount <= 0:
        return False
    expected = _round2((amount * return_percent) / (100 + return_percent))
    if expected == 0:
        return False
    return abs(holding.holding_profit - expected) <= max(1.0, abs(expected) * 0.02)


def resolve_holding_return_percent(holding: Holding) -> float | None:
    if holding.holding_return_percent is not None:
        return holding.holding_return_percent
    if holding.return_percent:
        return holding.return_percent
    return None


def resolve_intraday_return_percent(holding: Holding) -> float | None:
    """当日涨跌分量：官方净值已公布时用净值，否则用关联板块涨跌。"""
    if holding.daily_return_percent_source == "official_nav" and holding.daily_return_percent is not None:
        return holding.daily_return_percent
    if holding.sector_return_percent is not None:
        return holding.sector_return_percent
    if holding.daily_return_percent is not None:
        return holding.daily_return_percent
    return None


def resolve_effective_holding_return_percent(holding: Holding) -> float:
    """与前端 computeEstimatedHoldingReturnPercent 一致，供风控/分析/LLM 使用。"""
    estimated = compute_estimated_holding_return_percent(holding)
    if estimated is not None:
        return float(estimated)
    settled = resolve_holding_return_percent(holding)
    if settled is not None:
        return float(settled)
    return float(holding.return_percent)


def build_holding_display_metrics(holding: Holding) -> dict[str, float | bool | None]:
    """界面「持有」列与 analysis_facts 共用口径。"""
    settled = resolve_holding_return_percent(holding)
    return {
        "holding_return_percent_settled": settled if settled is not None else holding.return_percent,
        "estimated_holding_return_percent": resolve_effective_holding_return_percent(holding),
        "estimated_holding_profit": compute_holding_profit(holding),
        "holding_return_is_estimated": holding_profit_is_estimated(holding),
    }


def compute_estimated_holding_return_percent(holding: Holding) -> float | None:
    """持有收益率：净值公布后 OCR 值为含当日总值；盘中为昨日结算 + 板块涨跌。"""
    from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred

    profile = get_profile_for_holding(holding)
    if is_profit_accrual_deferred(profile):
        settled = resolve_holding_return_percent(holding)
        return round(settled, 4) if settled is not None else 0.0

    holding = _repair_corrupted_settled_profit(holding)
    if _ocr_holding_profit_is_cumulative(holding):
        settled = resolve_holding_return_percent(holding)
        if settled is not None:
            return round(settled, 4)
        return None
    settled = resolve_holding_return_percent(holding)
    if holding.daily_return_percent_source == "official_nav":
        if settled is not None:
            return round(settled, 4)
        if holding.daily_return_percent is not None:
            return round(holding.daily_return_percent, 4)
        return None
    intraday = resolve_intraday_return_percent(holding)
    if settled is None:
        return None
    if intraday is None:
        return settled
    return round(settled + intraday, 4)


def _expected_settled_profit(holding: Holding, return_percent: float) -> float | None:
    if holding.holding_amount <= 0:
        return None
    return _round2((holding.holding_amount * return_percent) / (100 + return_percent))


def _repair_corrupted_settled_profit(holding: Holding) -> Holding:
    """份额同步曾误写持有收益时，按昨日结算收益率修复。"""
    from app.database import get_fund_profile_by_code

    if _ocr_holding_profit_is_cumulative(holding):
        return holding

    profile = get_fund_profile_by_code(holding.fund_code) if holding.fund_code else None
    settled_return = resolve_holding_return_percent(holding)
    if profile and profile.holding_return_percent is not None:
        profile_return = profile.holding_return_percent
    else:
        profile_return = None

    if settled_return is None and profile_return is None:
        return holding
    if holding.holding_profit is None and profile_return is None:
        return holding

    reference_return = profile_return if profile_return is not None else settled_return
    if reference_return is None:
        return holding
    expected = _expected_settled_profit(holding, reference_return)
    if expected is None:
        return holding

    current_profit = holding.holding_profit
    if current_profit is None:
        return holding
    if (
        settled_return is not None
        and profile_return is not None
        and abs(settled_return - profile_return) > 0.05
    ):
        expected_from_holding = _expected_settled_profit(holding, settled_return)
        if expected_from_holding is not None and abs(current_profit - expected_from_holding) <= max(
            1.0, abs(expected_from_holding) * 0.02
        ):
            return holding
    delta = abs(current_profit - expected)
    if delta <= max(25.0, abs(expected) * 0.35):
        return holding

    restored_profit = (
        profile.holding_profit
        if profile and profile.holding_profit is not None
        else expected
    )
    patch: dict = {"holding_profit": restored_profit}
    if profile_return is not None:
        patch["holding_return_percent"] = profile_return
        patch["return_percent"] = profile_return
    elif settled_return is not None:
        patch["holding_return_percent"] = settled_return
        patch["return_percent"] = settled_return
    return holding.model_copy(update=patch)


def resolve_settled_holding_profit(holding: Holding) -> float | None:
    holding = _repair_corrupted_settled_profit(holding)
    if holding.holding_profit is not None:
        return holding.holding_profit
    return_percent = resolve_holding_return_percent(holding)
    if return_percent is None:
        return None
    return _expected_settled_profit(holding, return_percent)


def compute_holding_profit(holding: Holding) -> float | None:
    from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred

    profile = get_profile_for_holding(holding)
    if is_profit_accrual_deferred(profile):
        settled_profit = resolve_settled_holding_profit(holding)
        return settled_profit if settled_profit is not None else 0.0

    holding = _repair_corrupted_settled_profit(holding)
    if _ocr_holding_profit_is_cumulative(holding):
        if holding.holding_profit is not None:
            return holding.holding_profit
    if holding.daily_return_percent_source == "official_nav":
        if holding.holding_profit is not None:
            return holding.holding_profit
        estimated_return = compute_estimated_holding_return_percent(holding)
        if estimated_return is None or holding.holding_amount <= 0:
            return None
        return _round2((holding.holding_amount * estimated_return) / (100 + estimated_return))
    settled_profit = resolve_settled_holding_profit(holding)
    daily_profit = compute_daily_profit(holding)
    if settled_profit is not None and daily_profit is not None:
        return _round2(settled_profit + daily_profit)
    if settled_profit is not None:
        return settled_profit
    estimated_return = compute_estimated_holding_return_percent(holding)
    if estimated_return is None or holding.holding_amount <= 0:
        return None
    return _round2((holding.holding_amount * estimated_return) / (100 + estimated_return))


def holding_profit_is_estimated(holding: Holding) -> bool:
    from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred

    if is_profit_accrual_deferred(get_profile_for_holding(holding)):
        return False
    if _ocr_holding_profit_is_cumulative(holding):
        return False
    if holding.daily_return_percent_source == "official_nav":
        return holding.holding_profit is None
    if resolve_intraday_return_percent(holding) is not None:
        return True
    return holding.holding_profit is None and compute_holding_profit(holding) is not None


def _amount_includes_today_return(holding: Holding) -> bool:
    """盘中持有金额为上一交易日结算值，当日收益单独估算。"""
    if holding.amount_includes_today is not None:
        return holding.amount_includes_today
    return False


def compute_daily_profit_from_rate(
    holding_amount: float,
    daily_return_percent: float,
    *,
    amount_includes_today: bool,
) -> float:
    """当日收益：已更新金额用 r/(100+r)；OCR 结算金额用 r/100。"""
    if amount_includes_today:
        return compute_official_daily_profit(holding_amount, daily_return_percent)
    return _round2(holding_amount * daily_return_percent / 100)


def apply_sector_daily_estimates(holding: Holding) -> Holding:
    """刷新板块后重算当日收益，忽略 OCR 截图中的当日收益。

    若已写入官方净值当日收益率，则保留（关联板块列仍用 sector_return_percent）。
    若档案标记份额待确认（当日买入），则当日收益保持 0，不用板块覆盖。"""
    from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred

    if is_profit_accrual_deferred(get_profile_for_holding(holding)):
        return holding.model_copy(
            update={
                "daily_profit": 0.0,
                "daily_return_percent": 0.0,
                "daily_return_percent_source": "pending_accrual",
            }
        )
    if holding.daily_return_percent_source == "official_nav":
        return holding
    sector = holding.sector_return_percent
    amount = holding.settled_holding_amount or holding.holding_amount
    if sector is None or amount <= 0:
        return holding.model_copy(
            update={
                "daily_profit": None,
                "daily_return_percent": None,
                "daily_return_percent_source": None,
            }
        )
    daily = compute_daily_profit_from_rate(
        amount,
        sector,
        amount_includes_today=_amount_includes_today_return(holding),
    )
    return holding.model_copy(
        update={
            "daily_profit": daily,
            "daily_return_percent": sector,
            "daily_return_percent_source": "sector_estimate",
        }
    )


def overlay_official_nav_returns(holdings: list[Holding]) -> list[Holding]:
    """恢复持仓时若官方净值已公布，覆盖板块估算的当日收益。"""
    from app.services.fund_nav_service import get_official_nav_return
    from app.services.trading_session import build_trading_session, get_effective_trade_date

    session = build_trading_session()
    if session.get("session_kind") in {"trading_day_intraday", "trading_day_pre_close"}:
        return holdings

    trade_date = get_effective_trade_date()
    updated: list[Holding] = []
    for holding in holdings:
        if not holding.fund_code or holding.fund_code == "000000":
            updated.append(holding)
            continue
        nav_return = get_official_nav_return(holding.fund_code, trade_date)
        if nav_return is None:
            updated.append(holding)
            continue
        from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred

        if is_profit_accrual_deferred(get_profile_for_holding(holding)):
            updated.append(holding)
            continue
        amount = holding.settled_holding_amount or holding.holding_amount
        if (
            holding.daily_profit is not None
            and holding.daily_return_percent_source == "official_nav"
        ):
            updated.append(
                holding.model_copy(
                    update={
                        "daily_return_percent": nav_return,
                        "daily_return_percent_source": "official_nav",
                    }
                )
            )
            continue
        updated.append(
            holding.model_copy(
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
        )
    return updated


def enrich_holding_estimates(holding: Holding) -> Holding:
    """补全可持久化字段；含当日涨跌的持有收益仅在展示/分析层计算。"""
    holding = _repair_corrupted_settled_profit(holding)
    includes_today = _amount_includes_today_return(holding)
    holding = apply_sector_daily_estimates(holding)
    daily_profit = compute_daily_profit(holding)
    holding_return = resolve_holding_return_percent(holding)
    holding_profit = resolve_settled_holding_profit(holding)
    patch: dict = {
        "holding_return_percent": holding_return,
        "holding_profit": holding_profit,
        "amount_includes_today": includes_today,
    }
    if daily_profit is not None:
        patch["daily_profit"] = daily_profit
    return holding.model_copy(update=patch)


def enrich_holdings_estimates(holdings: list[Holding]) -> list[Holding]:
    estimated = [enrich_holding_estimates(holding) for holding in holdings]
    return enrich_holdings_yesterday_profits(estimated)


def sum_daily_profit(holdings: list[Holding]) -> float:
    return _round2(sum((compute_daily_profit(holding) or 0) for holding in holdings))


def compute_estimated_daily_return_percent(holding: Holding) -> float | None:
    """当日基金涨跌：优先 official/daily；否则用 sector_return_percent 估算。"""
    if holding.daily_return_percent is not None:
        return holding.daily_return_percent
    if holding.sector_return_percent is not None:
        return round(holding.sector_return_percent, 4)
    return None


def holding_daily_return_is_estimated(holding: Holding) -> bool:
    if holding.daily_return_percent_source in {"official_nav", "pending_accrual"}:
        return False
    from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred

    if is_profit_accrual_deferred(get_profile_for_holding(holding)):
        return False
    return holding.daily_return_percent is None and holding.sector_return_percent is not None


def portfolio_official_nav_settled(holdings: list[Holding]) -> bool:
    """组合当日收益是否已全部切到官方净值（盈亏日历「今日」展示条件）。"""
    from app.services.profit_accrual_defer import get_profile_for_holding, is_profit_accrual_deferred

    active = [
        holding
        for holding in holdings
        if holding.fund_code
        and holding.fund_code != "000000"
        and (holding.settled_holding_amount or holding.holding_amount) > 0
    ]
    if not active:
        return False

    counted = 0
    for holding in active:
        if is_profit_accrual_deferred(get_profile_for_holding(holding)):
            continue
        if holding.daily_return_percent_source == "pending_accrual":
            continue
        counted += 1
        if holding.daily_return_percent_source != "official_nav":
            return False
    return counted > 0


def compute_portfolio_daily_return_percent(holdings: list[Holding], daily_profit: float) -> float | None:
    total_assets = sum(
        (holding.settled_holding_amount or holding.holding_amount) + (holding.daily_profit or 0)
        for holding in holdings
        if (holding.settled_holding_amount or holding.holding_amount) > 0
    )
    if total_assets <= daily_profit or daily_profit == 0:
        return 0.0 if daily_profit == 0 else None
    previous = total_assets - daily_profit
    if previous <= 0:
        return None
    return _round2(daily_profit / previous * 100)


def compute_sector_fund_gap_percent(holding: Holding) -> float | None:
    """板块涨跌与基金当日/估算涨跌之差（百分点）。"""
    sector = holding.sector_return_percent
    if sector is None:
        return None
    fund_daily = holding.daily_return_percent
    if fund_daily is None:
        fund_daily = compute_estimated_daily_return_percent(holding)
    if fund_daily is None:
        return None
    return round(sector - fund_daily, 4)


def compute_official_daily_profit(holding_amount: float, daily_return_percent: float) -> float:
    """官方净值当日收益：结算前金额 × 日涨幅 = 现金额 × r / (100 + r)。"""
    return _round2(holding_amount * daily_return_percent / (100 + daily_return_percent))


def compute_daily_profit(holding: Holding) -> float | None:
    amount = holding.settled_holding_amount or holding.holding_amount
    if amount <= 0:
        return holding.daily_profit

    includes_today = _amount_includes_today_return(holding)
    if (
        holding.daily_profit is not None
        and holding.daily_return_percent_source == "official_nav"
        and holding.amount_includes_today
    ):
        return holding.daily_profit
    rate = holding.daily_return_percent
    if rate is not None:
        if holding.daily_return_percent_source == "official_nav" and not includes_today:
            return _round2(amount * rate / 100)
        return compute_daily_profit_from_rate(
            amount,
            rate,
            amount_includes_today=includes_today,
        )

    sector = holding.sector_return_percent
    if sector is not None:
        return compute_daily_profit_from_rate(
            amount,
            sector,
            amount_includes_today=includes_today,
        )

    return holding.daily_profit


def compute_yesterday_profit(
    holding: Holding,
    *,
    profile_yesterday_profit: float | None = None,
) -> float | None:
    """昨日收益：上一交易日官方净值涨跌；OCR 仅作兜底。"""
    if holding.fund_code and holding.fund_code != "000000" and holding.holding_amount > 0:
        from app.services.fund_nav_service import compute_yesterday_profit_from_official_nav
        from app.services.trading_session import get_effective_trade_date

        trade_date = get_effective_trade_date()
        nav_yesterday = compute_yesterday_profit_from_official_nav(
            holding.fund_code,
            holding.holding_amount,
            trade_date,
        )
        if nav_yesterday is not None:
            return nav_yesterday

    if holding.yesterday_profit is not None:
        return holding.yesterday_profit
    if profile_yesterday_profit is not None:
        return profile_yesterday_profit
    return None


def enrich_yesterday_profit(
    holding: Holding,
    *,
    profile_yesterday_profit: float | None = None,
) -> Holding:
    if holding.yesterday_profit is not None:
        return holding
    computed = compute_yesterday_profit(
        holding,
        profile_yesterday_profit=profile_yesterday_profit,
    )
    if computed is None:
        return holding
    return holding.model_copy(update={"yesterday_profit": computed})


def enrich_holdings_yesterday_profits(holdings: list[Holding]) -> list[Holding]:
    from app.database import get_fund_profile_by_code

    enriched: list[Holding] = []
    for holding in holdings:
        profile = get_fund_profile_by_code(holding.fund_code) if holding.fund_code else None
        profile_yesterday = profile.yesterday_profit if profile else None
        enriched.append(
            enrich_yesterday_profit(holding, profile_yesterday_profit=profile_yesterday)
        )
    return enriched
