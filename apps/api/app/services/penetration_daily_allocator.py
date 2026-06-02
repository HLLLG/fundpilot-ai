from __future__ import annotations

from app.models import Holding


def allocate_penetration_daily_profit(
    holdings: list[Holding],
    account_daily_profit: float,
) -> list[Holding]:
    """按持仓金额 × 板块涨跌贡献，将账户场内穿透当日收益拆到各行（参考值）。"""
    if not holdings:
        return []

    weights = _allocation_weights(holdings)
    profits = _distribute_signed_total(weights, account_daily_profit)

    updated: list[Holding] = []
    for holding, daily_profit in zip(holdings, profits, strict=True):
        daily_return_percent = None
        if holding.holding_amount > 0:
            daily_return_percent = round(daily_profit / holding.holding_amount * 100, 4)

        note = holding.user_note or ""
        tag = "穿透拆分参考"
        user_note = note if tag in note else (f"{note} {tag}".strip() if note else tag)

        updated.append(
            holding.model_copy(
                update={
                    "daily_profit": daily_profit,
                    "daily_return_percent": daily_return_percent,
                    "user_note": user_note,
                }
            )
        )
    return updated


def _allocation_weights(holdings: list[Holding]) -> list[float]:
    sector_weights: list[float | None] = []
    for holding in holdings:
        if holding.sector_return_percent is not None:
            sector_weights.append(holding.holding_amount * holding.sector_return_percent)
        else:
            sector_weights.append(None)

    if any(item is not None for item in sector_weights):
        resolved = [item if item is not None else 0.0 for item in sector_weights]
        if sum(abs(value) for value in resolved) > 1e-6:
            return resolved

    total_amount = sum(holding.holding_amount for holding in holdings) or 1.0
    return [holding.holding_amount / total_amount * total_amount for holding in holdings]


def _distribute_signed_total(weights: list[float], total: float) -> list[float]:
    weight_sum = sum(weights)
    if abs(weight_sum) < 1e-9:
        equal = total / len(weights) if weights else 0.0
        return _fix_rounding([equal] * len(weights), total)

    raw = [total * weight / weight_sum for weight in weights]
    rounded = [round(value, 2) for value in raw]
    return _fix_rounding(rounded, total)


def _fix_rounding(values: list[float], target_total: float) -> list[float]:
    if not values:
        return []
    result = list(values)
    drift = round(target_total - sum(result), 2)
    if drift == 0:
        return result
    # 调整绝对值最大的分量，减少相对误差
    adjust_index = max(range(len(result)), key=lambda index: abs(result[index]))
    result[adjust_index] = round(result[adjust_index] + drift, 2)
    return result
