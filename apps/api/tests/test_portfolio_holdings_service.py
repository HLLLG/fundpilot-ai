from app.models import Holding
from app.services.portfolio_holdings_service import load_persisted_holdings, profile_to_holding


def test_profile_to_holding_maps_core_fields():
    from app.models import FundProfile

    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=8270.43,
        holding_return_percent=-0.49,
        holding_profit=-40.54,
        sector_name="中证人工智能",
        sector_return_percent=4.25,
    )
    holding = profile_to_holding(profile)
    assert holding.fund_code == "008586"
    assert holding.holding_amount == 8270.43
    assert holding.sector_name == "中证人工智能"


def test_load_persisted_holdings_prefers_snapshot(tmp_path, monkeypatch):
    snapshot_holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8270.43,
            return_percent=-0.49,
            sector_name="中证人工智能",
        ).model_dump()
    ]
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_most_recent_portfolio_snapshot",
        lambda: {
            "snapshot_date": "2026-06-03",
            "holdings": snapshot_holdings,
        },
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.list_fund_profiles",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.enrich_holdings_from_profiles",
        lambda holdings: holdings,
    )

    holdings, source, snapshot_date = load_persisted_holdings()
    assert source == "snapshot"
    assert snapshot_date == "2026-06-03"
    assert len(holdings) == 1
    assert holdings[0].fund_code == "008586"


def test_load_persisted_holdings_falls_back_to_profiles(tmp_path, monkeypatch):
    from app.models import FundProfile

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_most_recent_portfolio_snapshot",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.list_fund_profiles",
        lambda: [
            FundProfile(
                fund_code="519674",
                fund_name="银河创新成长混合A",
                holding_amount=4042.24,
                holding_return_percent=-2.82,
                sector_name="半导体",
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.enrich_holdings_from_profiles",
        lambda holdings: holdings,
    )

    class FakeService:
        def resolve_holdings(self, holdings):
            return holdings

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.FundProfileService",
        FakeService,
    )

    holdings, source, snapshot_date = load_persisted_holdings()
    assert source == "profiles"
    assert snapshot_date is None
    assert len(holdings) == 1
    assert holdings[0].fund_name == "银河创新成长混合A"
