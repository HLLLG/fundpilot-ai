from __future__ import annotations

import re

PERCENT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")


def extract_percent(line: str) -> float | None:
    match = PERCENT_RE.search(line)
    if not match:
        return None
    return float(match.group(1))


def align_profit_sign(
    profit: float | None,
    return_percent: float | None,
) -> float | None:
    if profit is None or return_percent is None or profit == 0 or return_percent == 0:
        return profit
    if (profit > 0) > (return_percent > 0):
        return -abs(profit)
    return profit


def is_near_zero(value: float | None) -> bool:
    if value is None:
        return False
    return abs(value) < 0.0001


def infer_holding_profit(
    *,
    holding_amount: float | None,
    holding_return_percent: float | None,
    holding_profit: float | None,
) -> float | None:
    aligned = align_profit_sign(holding_profit, holding_return_percent)
    if aligned is not None and not is_near_zero(aligned):
        return aligned
    if holding_amount is None or holding_return_percent is None:
        return aligned
    inferred = round(
        holding_amount * holding_return_percent / (100 + holding_return_percent),
        2,
    )
    return align_profit_sign(inferred, holding_return_percent)
