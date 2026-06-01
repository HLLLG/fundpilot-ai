from app.models import Holding
from app.services.holding_validation import (
    build_holding_review,
    diff_holdings,
    merge_holdings_with_previous,
    validate_holdings,
)


def _holding(**kwargs) -> Holding:
    base = {
        "fund_code": "008586",
        "fund_name": "华夏人工智能ETF联接C",
        "holding_amount": 7250.12,
        "return_percent": -3.47,
        "daily_profit": -176.88,
        "daily_return_percent": -2.38,
        "sector_name": "中证人工智能",
        "sector_return_percent": -2.52,
    }
    base.update(kwargs)
    return Holding(**base)


def test_validate_daily_profit_sign_mismatch():
    holding = _holding(daily_profit=176.88, daily_return_percent=-2.38)
    warnings = validate_holdings([holding])
    codes = {item.code for item in warnings}
    assert "daily_profit_sign_mismatch" in codes


def test_validate_account_daily_sum_mismatch():
    holdings = [_holding(daily_profit=100.0), _holding(fund_code="015945", fund_name="易方达国防军工", daily_profit=50.0)]
    warnings = validate_holdings(holdings, account_daily_profit=-482.0)
    assert any(item.code == "account_daily_sum_mismatch" for item in warnings)


def test_diff_detects_added_and_removed():
    previous = [_holding()]
    current = [
        _holding(),
        _holding(fund_code="015945", fund_name="易方达国防军工混合C", holding_amount=1793.45),
    ]
    diffs = diff_holdings(previous, current)
    assert any(item.change_type == "added" for item in diffs)


def test_merge_keeps_previous_codes_and_updates_amounts():
    previous = [_holding(fund_code="008586")]
    current = [
        _holding(
            fund_code="000000",
            fund_name="华夏人工智能ETF.",
            holding_amount=8000,
            daily_profit=-200,
        )
    ]
    merged = merge_holdings_with_previous(previous, current)
    assert len(merged) == 1
    assert merged[0].fund_code == "008586"
    assert merged[0].holding_amount == 8000
    assert merged[0].daily_profit == -200


def test_build_holding_review_includes_warning_count():
    review = build_holding_review(
        [_holding(daily_profit=10, daily_return_percent=-1)],
        previous_holdings=[],
        portfolio_summary=None,
    )
    assert review["warning_count"] >= 1
