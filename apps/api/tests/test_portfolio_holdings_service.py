from app.models import Holding
from app.services.portfolio_holdings_service import (
    build_fast_snapshot_holdings_response,
    load_dashboard_holdings,
    load_persisted_holdings,
    profile_to_holding,
)


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


def test_fast_snapshot_response_uses_snapshot_without_slow_resolution(monkeypatch):
    snapshot_holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8671.67,
            settled_holding_amount=8671.67,
            return_percent=9.12,
            holding_return_percent=9.12,
            holding_profit=757.72,
            sector_name="人工智能",
            sector_return_percent=-4.62,
            sector_return_percent_source="closing_estimate",
        ).model_dump()
        | {"sector_return_percent_source": "closing_estimate"},
        Holding(
            fund_code="123456",
            fund_name="旧污染估值基金",
            holding_amount=1000,
            sector_name="人工智能",
            sector_return_percent=3.66,
            daily_return_percent=3.66,
            daily_return_percent_source="official_nav",
        ).model_dump(),
    ]
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_most_recent_portfolio_snapshot",
        lambda: {
            "snapshot_date": "2026-06-27",
            "captured_at": "2026-06-27T10:12:56+00:00",
            "total_assets": 29469.71,
            "daily_profit": 553.45,
            "daily_return_percent": 1.91,
            "holdings": snapshot_holdings,
        },
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.list_fund_profiles",
        lambda: (_ for _ in ()).throw(AssertionError("profiles should not be loaded")),
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_cached_official_nav_return",
        lambda code, trade_date: 3.66 if code == "008586" and trade_date == "2026-06-26" else None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_effective_trade_date",
        lambda: "2026-06-26",
    )

    payload = build_fast_snapshot_holdings_response()

    assert payload is not None
    assert payload["source"] == "snapshot"
    assert payload["holdings"][0]["sector_return_percent"] == -4.62
    assert payload["holdings"][0]["estimated_daily_return_percent"] == 3.66
    assert payload["holdings"][0]["daily_return_percent_source"] == "official_nav"
    assert payload["holdings"][1]["sector_return_percent"] is None
    assert payload["portfolio_summary"]["total_assets"] == 29469.71


def test_fast_snapshot_response_repairs_cross_market_semantic_sector(monkeypatch):
    snapshot_holdings = [
        Holding(
            fund_code="123456",
            fund_name="华夏全球科技先锋混合(QDII)C",
            holding_amount=2500,
            sector_name="电子",
        ).model_dump(),
    ]
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_most_recent_portfolio_snapshot",
        lambda: {
            "snapshot_date": "2026-07-01",
            "captured_at": "2026-07-01T03:30:00+00:00",
            "holdings": snapshot_holdings,
        },
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_effective_trade_date",
        lambda: "2026-07-01",
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_cached_official_nav_return",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.sync_holding_amounts_from_shares",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: {
            "fund_code": "123456",
            "sector_name": "电子",
            "intraday_index_name": None,
            "source": "ocr_detail",
            "confidence": 0.95,
            "detail": {"fund_name": "华夏全球科技先锋混合(QDII)C"},
        },
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.load_fresh_global_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **_kwargs: None,
    )

    payload = build_fast_snapshot_holdings_response()

    assert payload is not None
    assert payload["holdings"][0]["sector_name"] == "海外基金"


def test_load_dashboard_holdings_skips_profile_resolve(monkeypatch):
    snapshot_holdings = [
        Holding(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=8671.67,
            sector_name="人工智能",
            sector_return_percent=-4.62,
        ).model_dump(),
    ]
    resolve_calls = {"count": 0}

    def _resolve(*_args, **_kwargs):
        resolve_calls["count"] += 1
        return []

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_most_recent_portfolio_snapshot",
        lambda: {
            "snapshot_date": "2026-06-27",
            "captured_at": "2026-06-27T10:12:56+00:00",
            "holdings": snapshot_holdings,
        },
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.list_fund_profiles",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.holdings_from_profiles",
        _resolve,
    )
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_effective_trade_date",
        lambda: "2026-06-26",
    )

    holdings, source, snapshot_date, _ = load_dashboard_holdings()

    assert source == "snapshot"
    assert snapshot_date == "2026-06-27"
    assert len(holdings) == 1
    assert resolve_calls["count"] == 0


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
