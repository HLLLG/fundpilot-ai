from app.models import Holding
from app.models import PortfolioSummary
from app.services.holding_validation import (
    build_holding_review,
    can_allocate_penetration_daily,
    diff_holdings,
    enrich_portfolio_summary_source,
    infer_daily_profit_source,
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


def test_validate_invalid_sector_label():
    warnings = validate_holdings([_holding(sector_name="+")])
    assert any(item.code == "invalid_sector_label" for item in warnings)


def test_validate_daily_profit_sign_mismatch():
    holding = _holding(daily_profit=176.88, daily_return_percent=-2.38)
    warnings = validate_holdings([holding])
    codes = {item.code for item in warnings}
    assert "daily_profit_sign_mismatch" in codes


def test_validate_account_daily_sum_mismatch():
    holdings = [_holding(daily_profit=100.0), _holding(fund_code="015945", fund_name="易方达国防军工", daily_profit=50.0)]
    warnings = validate_holdings(holdings, account_daily_profit=-482.0)
    assert any(item.code == "account_daily_sum_mismatch" for item in warnings)


def test_infer_penetration_when_account_has_daily_but_rows_empty():
    holdings = [
        _holding(daily_profit=None, daily_return_percent=None, sector_return_percent=2.0),
    ]
    summary = PortfolioSummary(total_assets=10000, daily_profit=369.84)

    assert infer_daily_profit_source(summary, holdings) == "penetration_estimate"
    assert can_allocate_penetration_daily(summary, holdings) is True

    enriched = enrich_portfolio_summary_source(summary, holdings)
    assert enriched is not None
    assert enriched.daily_profit_source == "penetration_estimate"


def test_pre_close_penetration_estimate_no_sum_mismatch_warn():
    holdings = [
        _holding(daily_profit=None, daily_return_percent=None, sector_return_percent=2.87),
        _holding(
            fund_code="015945",
            fund_name="易方达国防军工",
            daily_profit=None,
            daily_return_percent=None,
            sector_return_percent=0.51,
        ),
    ]
    warnings = validate_holdings(
        holdings,
        account_daily_profit=369.84,
        account_daily_profit_source="penetration_estimate",
    )
    assert not any(item.code == "account_daily_sum_mismatch" for item in warnings)
    assert any(item.code == "account_daily_penetration_estimate" for item in warnings)


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
