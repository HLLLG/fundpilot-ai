from app.models import Holding
from app.services.sector_quote_resolver import SectorResolveResult
from app.services.sector_quote_service import refresh_holdings_sector_quotes


def test_refresh_uses_on_demand_for_missing_concept(tmp_path, monkeypatch):
    from app.config import refresh_settings

    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    refresh_settings()
    monkeypatch.setattr(
        "app.services.sector_quote_resolver.fetch_canonical_sector_quote",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_spot_boards",
        lambda **kwargs: {
            "index": {"人工智能": 5.5},
            "concept": {},
            "industry": {"半导体": 4.2},
        },
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.get_sector_mapping",
        lambda _key: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.save_sector_mapping",
        lambda _record: None,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_sector_on_demand",
        lambda sector_name, boards: (
            SectorResolveResult(
                confidence="high",
                change_percent=3.12,
                matched_name="商业航天",
                source_type="concept",
            )
            if sector_name == "商业航天"
            else None
        ),
    )

    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=1188.96,
            return_percent=-7.43,
            sector_name="商业航天",
            sector_return_percent=2.29,
        )
    ]
    result = refresh_holdings_sector_quotes(holdings, force_refresh=True)
    assert result["summary"]["matched"] == 1
    assert result["holdings"][0]["sector_return_percent"] == 3.12
    assert result["items"][0]["sector_quote_meta"]["source"] == "live"
