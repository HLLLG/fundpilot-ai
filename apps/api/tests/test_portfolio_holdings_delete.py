from app.models import Holding, PortfolioSummary
from app.services.ocr_pipeline import apply_confirmed_holdings
from app.services.portfolio_holdings_service import remove_holding_from_portfolio
from app.services.portfolio_snapshot import save_daily_snapshot


def test_apply_confirmed_holdings_skips_heavy_sector_pipeline(monkeypatch):
    called = {"process": 0}

    def fake_process(*args, **kwargs):
        called["process"] += 1
        return [], {}, None

    monkeypatch.setattr("app.services.ocr_pipeline.process_overview_holdings", fake_process)
    monkeypatch.setattr(
        "app.services.ocr_pipeline._finalize_confirmed_holdings",
        lambda holdings, _service: holdings,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.apply_primary_sector_to_holdings",
        lambda holdings: holdings,
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

    holdings = [
        Holding(
            fund_code="001234",
            fund_name="测试基金混合A",
            holding_amount=1000.0,
            return_percent=1.0,
        )
    ]
    result = apply_confirmed_holdings(holdings)

    assert called["process"] == 0
    assert len(result["holdings"]) == 1
    assert result["sector_refresh"] is None


def test_remove_holding_from_portfolio_deletes_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings

    refresh_settings()

    from app.services.fund_profile import FundProfileService
    from app.models import FundProfile

    service = FundProfileService()
    service.save_profile(
        FundProfile(
            fund_code="001234",
            fund_name="测试基金混合A",
            holding_amount=1000.0,
        )
    )
    service.save_profile(
        FundProfile(
            fund_code="005678",
            fund_name="保留基金混合C",
            holding_amount=2000.0,
        )
    )

    holdings = [
        Holding(
            fund_code="001234",
            fund_name="测试基金混合A",
            holding_amount=1000.0,
            return_percent=1.0,
        ),
        Holding(
            fund_code="005678",
            fund_name="保留基金混合C",
            holding_amount=2000.0,
            return_percent=2.0,
        ),
    ]
    summary = PortfolioSummary(total_assets=3000.0, holding_count=2)
    save_daily_snapshot(holdings, summary)

    payload = remove_holding_from_portfolio("001234")

    assert len(payload["holdings"]) == 1
    assert payload["holdings"][0]["fund_code"] == "005678"
    assert payload["portfolio_summary"]["total_assets"] == 2000.0
    from app.database import get_fund_profile_by_code

    assert get_fund_profile_by_code("001234") is None
    assert get_fund_profile_by_code("005678") is not None


def test_delete_then_load_persisted_holdings_does_not_resurrect_fund(tmp_path, monkeypatch):
    """删除后刷新：快照与档案均移除，基金不应复活。"""
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings

    refresh_settings()

    from app.database import get_fund_profile_by_code
    from app.models import FundProfile
    from app.services.fund_profile import FundProfileService
    from app.services.portfolio_holdings_service import load_persisted_holdings

    service = FundProfileService()
    service.save_profile(
        FundProfile(
            fund_code="026790",
            fund_name="中欧上证科创板人工智能指数C",
            holding_amount=770.77,
        )
    )
    service.save_profile(
        FundProfile(
            fund_code="025857",
            fund_name="华夏中证电网设备主题ETF联接C",
            holding_amount=2000.0,
        )
    )
    holdings = [
        Holding(
            fund_code="026790",
            fund_name="中欧上证科创板人工智能指数C",
            holding_amount=770.77,
            return_percent=0,
        ),
        Holding(
            fund_code="025857",
            fund_name="华夏中证电网设备主题ETF联接C",
            holding_amount=2000.0,
            return_percent=0,
        ),
    ]
    save_daily_snapshot(holdings, PortfolioSummary(total_assets=2770.77, holding_count=2))

    remove_holding_from_portfolio("026790", fund_name="中欧上证科创板人工智能指数C")

    loaded, source, _, _ = load_persisted_holdings()
    codes = {h.fund_code for h in loaded}
    assert "026790" not in codes
    assert "025857" in codes
    assert source == "snapshot"
    assert get_fund_profile_by_code("026790") is None


def test_merge_holdings_with_profiles_does_not_readd_deleted_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings
    from app.models import FundProfile
    from app.services.fund_profile import FundProfileService
    from app.services.portfolio_holdings_service import merge_holdings_with_profiles
    from app.services.portfolio_snapshot import save_daily_snapshot

    refresh_settings()
    service = FundProfileService()
    service.save_profile(
        FundProfile(
            fund_code="021234",
            fund_name="中航机遇领航混合发起C",
            holding_amount=999.97,
            sector_name="CPO",
        )
    )
    service.save_profile(
        FundProfile(
            fund_code="005678",
            fund_name="保留基金混合C",
            holding_amount=2000.0,
        )
    )

    snapshot_holdings = [
        Holding(
            fund_code="005678",
            fund_name="保留基金混合C",
            holding_amount=2000.0,
            return_percent=2.0,
        )
    ]
    save_daily_snapshot(snapshot_holdings, PortfolioSummary(total_assets=2000.0, holding_count=1))

    merged = merge_holdings_with_profiles(snapshot_holdings)
    codes = {item.fund_code for item in merged}
    assert "021234" not in codes
    assert "005678" in codes


def test_remove_profile_only_holding(tmp_path, monkeypatch):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings
    from app.database import get_fund_profile_by_code
    from app.models import FundProfile
    from app.services.fund_profile import FundProfileService
    from app.services.portfolio_snapshot import save_daily_snapshot

    refresh_settings()
    service = FundProfileService()
    service.save_profile(
        FundProfile(
            fund_code="021234",
            fund_name="中航机遇领航混合发起C",
            holding_amount=999.97,
        )
    )
    save_daily_snapshot(
        [
            Holding(
                fund_code="005678",
                fund_name="保留基金混合C",
                holding_amount=2000.0,
                return_percent=2.0,
            )
        ],
        PortfolioSummary(total_assets=2000.0, holding_count=1),
    )

    payload = remove_holding_from_portfolio("021234", fund_name="中航机遇领航混合发起C")

    assert len(payload["holdings"]) == 1
    assert payload["holdings"][0]["fund_code"] == "005678"
    assert get_fund_profile_by_code("021234") is None
