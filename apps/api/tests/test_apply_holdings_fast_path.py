from app.models import Holding
from app.services.ocr_pipeline import apply_confirmed_holdings


def test_apply_confirmed_holdings_skips_network_bootstrap(monkeypatch):
    fetch_calls = {"estimate": 0, "nav": 0}

    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *args, **kwargs: fetch_calls.__setitem__("estimate", fetch_calls["estimate"] + 1) or {},
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_latest_unit_nav",
        lambda *args, **kwargs: fetch_calls.__setitem__("nav", fetch_calls["nav"] + 1) or None,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline._finalize_confirmed_holdings",
        lambda holdings, _service: holdings,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.apply_primary_sector_to_holdings",
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.FundProfileService",
        lambda: type(
            "StubProfileService",
            (),
            {
                "sync_profiles_from_holdings": lambda self, holdings: type(
                    "SyncResult", (), {"model_dump": lambda self: {"updated": 0, "created": 0}}
                )(),
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.ocr_pipeline.enrich_holdings_from_profiles",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.refresh_holdings_sector_quotes",
        lambda holdings, **kwargs: {
            "ok": True,
            "holdings": [
                {
                    **holding.model_dump(),
                    "sector_return_percent": 1.5,
                    "sector_return_percent_source": "realtime",
                }
                for holding in holdings
            ],
            "summary": {"matched": len(holdings)},
            "message": "cache hit",
        },
    )

    holdings = [
        Holding(
            fund_code="001234",
            fund_name="测试基金混合A",
            holding_amount=1000.0,
            return_percent=1.0,
        )
    ]
    result = apply_confirmed_holdings(holdings)

    assert fetch_calls["estimate"] == 0
    assert fetch_calls["nav"] == 0
    assert result["sector_refresh"]["cache_only"] is True
    assert result["sector_refresh"]["matched"] == 1
    assert result["holdings"][0]["sector_return_percent"] == 1.5
    assert result["holdings"][0]["daily_profit"] is not None
