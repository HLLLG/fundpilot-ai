from app.models import FundProfile, Holding
from app.services.portfolio_holdings_service import (
    _should_recover_from_profiles,
    load_persisted_holdings,
)
from app.services.portfolio_persistence import merge_holdings_with_snapshot


def test_should_recover_when_snapshot_has_fewer_funds():
    snap = [
        Holding(
            fund_code="000001",
            fund_name="测试",
            holding_amount=1000.0,
            return_percent=0,
        )
    ]
    profiles = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8270.43,
            return_percent=0,
        ),
        Holding(
            fund_code="519674",
            fund_name="银河创新成长混合A",
            holding_amount=4042.24,
            return_percent=0,
        ),
    ]
    assert _should_recover_from_profiles(snap, profiles, snapshot_total_assets=28289.55)


def test_merge_holdings_keeps_previous_when_incoming_is_subset(monkeypatch):
    previous = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8270.43,
            return_percent=-0.49,
            sector_name="中证人工智能",
            sector_return_percent=1.0,
        ),
        Holding(
            fund_code="519674",
            fund_name="银河创新成长混合A",
            holding_amount=4042.24,
            return_percent=-2.82,
            sector_name="半导体",
            sector_return_percent=2.0,
        ),
    ]
    monkeypatch.setattr(
        "app.services.portfolio_persistence.get_most_recent_portfolio_snapshot",
        lambda: {"holdings": [h.model_dump() for h in previous]},
    )
    incoming = [
        Holding(
            fund_code="000001",
            fund_name="测试",
            holding_amount=1000.0,
            return_percent=0,
            sector_name="半导体",
            sector_return_percent=4.33,
        )
    ]
    merged = merge_holdings_with_snapshot(incoming)
    assert len(merged) == 2
    assert merged[0].fund_code == "008586"
    assert merged[1].fund_code == "519674"


def test_load_recovers_from_profiles(monkeypatch):
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_most_recent_portfolio_snapshot",
        lambda: {
            "snapshot_date": "2026-06-03",
            "total_assets": 28289.55,
            "holdings": [
                Holding(
                    fund_code="000001",
                    fund_name="测试",
                    holding_amount=1000.0,
                    return_percent=0,
                ).model_dump()
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.list_fund_profiles",
        lambda: [
            FundProfile(
                fund_code="008586",
                fund_name="华夏人工智能ETF联接C",
                holding_amount=8270.43,
                holding_return_percent=-0.49,
                sector_name="中证人工智能",
            ),
            FundProfile(
                fund_code="519674",
                fund_name="银河创新成长混合A",
                holding_amount=4042.24,
                holding_return_percent=-2.82,
                sector_name="半导体",
            ),
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

    holdings, source, _ = load_persisted_holdings()
    assert source == "profiles_recovered"
    assert len(holdings) == 2
