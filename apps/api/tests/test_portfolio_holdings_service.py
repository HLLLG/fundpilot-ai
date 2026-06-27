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
            "captured_at": "2026-06-03T08:15:00+00:00",
            "holdings": snapshot_holdings,
        },
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.list_fund_profiles",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.enrich_holdings_from_profiles",
        lambda holdings, **_kwargs: holdings,
    )

    holdings, source, snapshot_date, refreshed_at = load_persisted_holdings()
    assert source == "snapshot"
    assert snapshot_date == "2026-06-03"
    assert refreshed_at is not None
    assert refreshed_at.isoformat().startswith("2026-06-03T08:15:00")
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
        lambda holdings, **_kwargs: holdings,
    )

    class FakeService:
        def resolve_holdings(self, holdings, **_kwargs):
            return holdings

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.FundProfileService",
        FakeService,
    )

    holdings, source, snapshot_date, refreshed_at = load_persisted_holdings()
    assert source == "profiles"
    assert snapshot_date is None
    assert refreshed_at is None
    assert len(holdings) == 1
    assert holdings[0].fund_name == "银河创新成长混合A"


def test_enrich_loaded_holdings_skips_network_by_default(monkeypatch):
    from app.services.portfolio_persistence import enrich_loaded_holdings

    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长混合A",
        holding_amount=4042.24,
        return_percent=-2.82,
        sector_return_percent=1.5,
    )

    def _fail_sync(*_args, **_kwargs):
        raise AssertionError("sync_holding_amounts_from_shares should not run on fast load")

    def _fail_overlay(*_args, **_kwargs):
        raise AssertionError("overlay_official_nav_returns should not run on fast load")

    monkeypatch.setattr(
        "app.services.portfolio_persistence.sync_holding_amounts_from_shares",
        _fail_sync,
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.overlay_official_nav_returns",
        _fail_overlay,
    )

    enriched = enrich_loaded_holdings([holding])
    assert enriched[0].fund_code == "519674"
    assert enriched[0].daily_profit is not None
