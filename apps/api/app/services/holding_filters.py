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
