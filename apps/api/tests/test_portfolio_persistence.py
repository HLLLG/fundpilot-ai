from app.models import Holding, PortfolioSummary
from app.services.portfolio_persistence import enrich_loaded_holdings


def test_enrich_loaded_holdings_recomputes_daily_from_sector():
    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=1000.0,
            return_percent=-5.0,
            daily_profit=594.0,
            sector_name="商业航天",
            sector_return_percent=3.5,
        )
    ]
    enriched = enrich_loaded_holdings(holdings)
    assert enriched[0].daily_profit == 35.0
    assert enriched[0].daily_return_percent == 3.5
