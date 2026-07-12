from datetime import date, datetime, timedelta, timezone

from app.models import FundTransaction, Holding, PortfolioSummary
from app.services.ocr_pipeline import apply_confirmed_holdings
from app.services.portfolio_holdings_service import remove_holding_from_portfolio
from app.services.portfolio_snapshot import save_daily_snapshot


def test_delete_endpoint_maps_position_store_outage_to_503(monkeypatch):
    from fastapi import HTTPException

    from app import main
    from app.services.portfolio_ledger_service import PositionTruthStoreUnavailable

    def unavailable(*_args, **_kwargs):
        raise PositionTruthStoreUnavailable("primary unavailable")

    monkeypatch.setattr(main, "remove_holding_from_portfolio", unavailable)
    try:
        main.delete_portfolio_holding("001234")
    except HTTPException as exc:
        assert exc.status_code == 503
    else:  # pragma: no cover
        raise AssertionError("position truth outages must surface as 503")


def test_delete_endpoint_maps_unsettled_trade_conflict_to_409(monkeypatch):
    from fastapi import HTTPException

    from app import main
    from app.services.portfolio_ledger_service import PositionCloseConflict

    def conflict(*_args, **_kwargs):
        raise PositionCloseConflict(["future-tx"])

    monkeypatch.setattr(main, "remove_holding_from_portfolio", conflict)
    try:
        main.delete_portfolio_holding("001234")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert exc.detail["transaction_ids"] == ["future-tx"]
    else:  # pragma: no cover
        raise AssertionError("unsettled trades must block holding deletion")


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
        lambda holdings, **kwargs: holdings,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.bootstrap_holding_baselines",
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
    assert result["sector_refresh"] is not None
    assert result["sector_refresh"]["cache_only"] is True


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

    # Removing the compatibility snapshot/profile must also close immutable
    # ledger truth, otherwise the next decision preflight resurrects a ghost.
    from app.services.decision_repository import list_portfolio_ledger_events
    from app.services.portfolio_ledger import fold_ledger_events

    ledger = list_portfolio_ledger_events(user_id=1, fund_code="001234")
    assert ledger
    state = fold_ledger_events(
        ledger,
        position_as_of="2099-01-01",
        known_at="2099-01-01T23:59:59+08:00",
    )
    assert not any(
        row["fund_code"] == "001234" and float(row["settled_shares"]) > 0
        for row in state["positions"]
    )


def test_remove_holding_is_blocked_while_future_transaction_is_known(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings
    from app.database import (
        get_fund_profile_by_code,
        insert_fund_transaction,
    )
    from app.models import FundProfile
    from app.services.fund_profile import FundProfileService
    from app.services.portfolio_ledger_service import PositionCloseConflict

    refresh_settings()
    FundProfileService().save_profile(
        FundProfile(
            fund_code="001234",
            fund_name="测试基金混合A",
            holding_amount=1000,
        )
    )
    save_daily_snapshot(
        [
            Holding(
                fund_code="001234",
                fund_name="测试基金混合A",
                holding_amount=1000,
            )
        ],
        PortfolioSummary(total_assets=1000, holding_count=1),
    )
    future = (date.today() + timedelta(days=3)).isoformat()
    assert insert_fund_transaction(
        FundTransaction(
            id="future-tx",
            fund_code="001234",
            fund_name="测试基金混合A",
            direction="buy",
            amount_yuan=100,
            trade_time=f"{date.today().isoformat()} 14:00:00",
            confirm_date=future,
            status="pending",
            confirmed_shares=10,
            shares_source="user_confirmed",
            in_progress=True,
            dedup_key="future-delete-conflict",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    try:
        remove_holding_from_portfolio("001234")
    except PositionCloseConflict as exc:
        assert exc.transaction_ids == ["future-tx"]
    else:  # pragma: no cover
        raise AssertionError("known future trades must be handled before deletion")

    assert get_fund_profile_by_code("001234") is not None


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


def test_delete_last_holding_empty_snapshot_blocks_stale_profile_recovery(tmp_path, monkeypatch):
    """删除最后一只后，最新空快照代表用户已清空，旧档案不能把基金带回来。"""
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "app.db"))
    from app.config import refresh_settings

    refresh_settings()

    from app.models import FundProfile
    from app.services.fund_profile import FundProfileService
    from app.services.portfolio_holdings_service import load_persisted_holdings

    service = FundProfileService()
    service.save_profile(
        FundProfile(
            fund_code="016665",
            fund_name="天弘全球高端制造混合(QDII)C",
            holding_amount=100.0,
        )
    )
    service.save_profile(
        FundProfile(
            fund_code="022184",
            fund_name="富国全球科技互联网股票(QDII)C",
            holding_amount=1000.0,
        )
    )
    save_daily_snapshot(
        [
            Holding(
                fund_code="016665",
                fund_name="天弘全球高端制造混合(QDII)C",
                holding_amount=100.0,
                return_percent=0,
            )
        ],
        PortfolioSummary(total_assets=100.0, holding_count=1),
    )

    payload = remove_holding_from_portfolio(
        "016665",
        fund_name="天弘全球高端制造混合(QDII)C",
    )
    assert payload["holdings"] == []

    loaded, source, _, _ = load_persisted_holdings()

    assert loaded == []
    assert source == "snapshot"


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
