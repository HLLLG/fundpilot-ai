from app.models import Holding, PortfolioSummary
from app.services.portfolio_persistence import enrich_loaded_holdings


def test_enrich_loaded_holdings_recomputes_daily_from_sector(monkeypatch):
    monkeypatch.setattr(
        "app.services.portfolio_persistence.sync_holding_amounts_from_shares",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_official_nav_return",
        lambda fund_code, trade_date: None,
    )
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
