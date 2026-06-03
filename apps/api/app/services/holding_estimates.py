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


def compute_holding_profit(holding: Holding) -> float | None:
    if holding.holding_profit is not None:
        return holding.holding_profit
    return_percent = resolve_holding_return_percent(holding)
    if return_percent is None or holding.holding_amount <= 0:
        return None
    return _round2((holding.holding_amount * return_percent) / (100 + return_percent))


def apply_sector_daily_estimates(holding: Holding) -> Holding:
    """刷新板块后：当日收益 = 持有金额 × 板块涨跌%，忽略 OCR 截图中的当日收益。"""
    sector = holding.sector_return_percent
    if sector is None or holding.holding_amount <= 0:
        return holding.model_copy(update={"daily_profit": None, "daily_return_percent": None})
    daily = _round2(holding.holding_amount * sector / 100)
    return holding.model_copy(
        update={
            "daily_profit": daily,
            "daily_return_percent": sector,
        }
    )


def enrich_holding_estimates(holding: Holding) -> Holding:
    holding = apply_sector_daily_estimates(holding)
    holding_return = resolve_holding_return_percent(holding)
    holding_profit = compute_holding_profit(holding)
    return holding.model_copy(
        update={
            "holding_return_percent": holding_return,
            "holding_profit": holding_profit,
        }
    )


def enrich_holdings_estimates(holdings: list[Holding]) -> list[Holding]:
    return [enrich_holding_estimates(holding) for holding in holdings]


def sum_daily_profit(holdings: list[Holding]) -> float:
    return _round2(sum((holding.daily_profit or 0) for holding in holdings))


def compute_daily_profit(holding: Holding) -> float | None:
    if holding.daily_profit is not None:
        return holding.daily_profit
    if holding.sector_return_percent is not None and holding.holding_amount > 0:
        return _round2(holding.holding_amount * holding.sector_return_percent / 100)
    return None
