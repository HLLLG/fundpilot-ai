from app.models import Holding
from app.services.sector_quote_service import refresh_holdings_sector_quotes


def test_refresh_sector_quotes_updates_matched(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_spot_boards",
        lambda **kwargs: {
            "index": {},
            "concept": {"半导体": 4.57},
            "industry": {},
        },
    )
    monkeypatch.setattr("app.services.sector_quote_service.get_sector_mapping", lambda _key: None)
    monkeypatch.setattr("app.services.sector_quote_service.save_sector_mapping", lambda _record: None)

    holdings = [
        Holding(
            fund_code="015608",
            fund_name="测试",
            holding_amount=1000,
            return_percent=1,
            sector_name="半导体",
            sector_return_percent=1.0,
        )
    ]
    result = refresh_holdings_sector_quotes(holdings)
    assert result["ok"] is True
    assert result["summary"]["matched"] == 1
    assert result["holdings"][0]["sector_return_percent"] == 4.57
    assert result["items"][0]["sector_quote_meta"]["source"] == "live"


def test_refresh_sector_quotes_auto_maps_csi_grid_equipment(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_spot_boards",
        lambda **kwargs: {
            "index": {"电力设备主题": 1.5, "中证全指电网": 0.97},
            "concept": {"电网设备": 1.1, "电网设备ETF": 1.2},
            "industry": {"电网设备": 0.9},
        },
    )
    monkeypatch.setattr("app.services.sector_quote_service.get_sector_mapping", lambda _key: None)
    monkeypatch.setattr("app.services.sector_quote_service.save_sector_mapping", lambda _record: None)

    holdings = [
        Holding(
            fund_code="015608",
            fund_name="测试",
            holding_amount=1000,
            return_percent=1,
            sector_name="中证电网设备",
            sector_return_percent=0.5,
        )
    ]
    result = refresh_holdings_sector_quotes(holdings)
    assert result["summary"]["matched"] == 1
    assert result["holdings"][0]["sector_return_percent"] == 1.5
