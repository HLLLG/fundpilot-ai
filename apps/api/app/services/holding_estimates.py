from __future__ import annotations

from app.models import Holding


def _round2(value: float) -> float:
    return round(value, 2)


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


def compute_estimated_holding_return_percent(holding: Holding) -> float | None:
    """持有收益率：净值公布后 OCR 值为含当日总值；盘中为昨日结算 + 板块涨跌。"""
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


def resolve_settled_holding_profit(holding: Holding) -> float | None:
    if holding.holding_profit is not None:
        return holding.holding_profit
    return_percent = resolve_holding_return_percent(holding)
    if return_percent is None or holding.holding_amount <= 0:
        return None
    return _round2((holding.holding_amount * return_percent) / (100 + return_percent))


def compute_holding_profit(holding: Holding) -> float | None:
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
    if holding.daily_return_percent_source == "official_nav":
        return holding.holding_profit is None
    if resolve_intraday_return_percent(holding) is not None:
        return True
    return holding.holding_profit is None and compute_holding_profit(holding) is not None


def apply_sector_daily_estimates(holding: Holding) -> Holding:
    """刷新板块后：当日收益 = 持有金额 × 板块涨跌%，忽略 OCR 截图中的当日收益。

    若已写入官方净值当日收益率，则保留（关联板块列仍用 sector_return_percent）。"""
    if holding.daily_return_percent_source == "official_nav":
        return holding
    sector = holding.sector_return_percent
    if sector is None or holding.holding_amount <= 0:
        return holding.model_copy(
            update={
                "daily_profit": None,
                "daily_return_percent": None,
                "daily_return_percent_source": None,
            }
        )
    daily = _round2(holding.holding_amount * sector / 100)
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
    from app.services.trading_session import get_effective_trade_date

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
        updated.append(
            holding.model_copy(
                update={
                    "daily_return_percent": nav_return,
                    "daily_profit": compute_official_daily_profit(
                        holding.holding_amount,
                        nav_return,
                    ),
                    "daily_return_percent_source": "official_nav",
                }
            )
        )
    return updated


def enrich_holding_estimates(holding: Holding) -> Holding:
    """补全可持久化字段；含当日涨跌的持有收益仅在展示/分析层计算。"""
    holding = apply_sector_daily_estimates(holding)
    daily_profit = compute_daily_profit(holding)
    holding_return = resolve_holding_return_percent(holding)
    holding_profit = resolve_settled_holding_profit(holding)
    patch: dict = {
        "holding_return_percent": holding_return,
        "holding_profit": holding_profit,
    }
    if daily_profit is not None:
        patch["daily_profit"] = daily_profit
    return holding.model_copy(update=patch)


def enrich_holdings_estimates(holdings: list[Holding]) -> list[Holding]:
    estimated = [enrich_holding_estimates(holding) for holding in holdings]
    return enrich_holdings_yesterday_profits(estimated)


def sum_daily_profit(holdings: list[Holding]) -> float:
    return _round2(sum((compute_daily_profit(holding) or 0) for holding in holdings))


def compute_official_daily_profit(holding_amount: float, daily_return_percent: float) -> float:
    """官方净值当日收益：结算前金额 × 日涨幅 = 现金额 × r / (100 + r)。"""
    return _round2(holding_amount * daily_return_percent / (100 + daily_return_percent))


def compute_daily_profit(holding: Holding) -> float | None:
    if (
        holding.daily_return_percent_source == "official_nav"
        and holding.daily_return_percent is not None
        and holding.holding_amount > 0
    ):
        return compute_official_daily_profit(
            holding.holding_amount,
            holding.daily_return_percent,
        )
    if holding.daily_profit is not None:
        return holding.daily_profit
    if holding.sector_return_percent is not None and holding.holding_amount > 0:
        return _round2(holding.holding_amount * holding.sector_return_percent / 100)
    return None


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
