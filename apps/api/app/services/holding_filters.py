from __future__ import annotations

from app.models import Holding

_PLACEHOLDER_CODES = frozenset({"000001"})
_TEST_NAME_PREFIXES = ("测试", "新基金")


def is_test_holding(holding: Holding) -> bool:
    code = (holding.fund_code or "").strip()
    name = (holding.fund_name or "").strip()
    if code in _PLACEHOLDER_CODES:
        return True
    return any(name.startswith(prefix) for prefix in _TEST_NAME_PREFIXES)


def without_test_holdings(holdings: list[Holding]) -> list[Holding]:
    return [holding for holding in holdings if not is_test_holding(holding)]


def is_placeholder_holding(holding: Holding) -> bool:
    code = (holding.fund_code or "").strip()
    name = (holding.fund_name or "").strip()
    if code == "000000":
        return True
    return name == "待录入基金" or name.startswith("待录入")


def without_placeholder_holdings(holdings: list[Holding]) -> list[Holding]:
    return [holding for holding in holdings if not is_placeholder_holding(holding)]


def effective_holding_amount(holding: Holding) -> float:
    settled = holding.settled_holding_amount
    if settled is not None:
        return float(settled)
    return float(holding.holding_amount or 0)


def is_inactive_holding(holding: Holding) -> bool:
    """已删除/停用：持有金额为 0，不应出现在账户汇总列表。"""
    return effective_holding_amount(holding) <= 0


def without_inactive_holdings(holdings: list[Holding]) -> list[Holding]:
    return [holding for holding in holdings if not is_inactive_holding(holding)]
