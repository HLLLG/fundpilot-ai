from app.models import Holding
from app.services.portfolio_profit_analysis import _holdings_intraday_fingerprint


def test_holdings_intraday_fingerprint_changes_with_amount():
    holdings = [
        Holding(fund_code="111111", fund_name="A", holding_amount=1000, sector_name="半导体"),
    ]
    first = _holdings_intraday_fingerprint(holdings, {})
    second = _holdings_intraday_fingerprint(
        [
            Holding(
                fund_code="111111",
                fund_name="A",
                holding_amount=2000,
                sector_name="半导体",
            )
        ],
        {},
    )
    assert first != second
