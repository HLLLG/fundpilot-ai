"""主动医药基金（000960）应对齐养基宝：持仓穿透出 CXO，分时走国证CXO。"""

from __future__ import annotations

from types import SimpleNamespace

from app.models import FundProfile, Holding
from app.services.fund_primary_sector_types import PrimarySectorRecord
from app.services.fund_profile import (
    FundProfileService,
    _looks_like_index_name,
    infer_intraday_index_from_sector,
)


def test_cxo_intraday_index_is_guozheng_cxo() -> None:
    assert infer_intraday_index_from_sector("CXO") == "国证CXO"
    assert _looks_like_index_name("国证CXO") is True


def test_detail_resolution_applies_holdings_infer_cxo(monkeypatch) -> None:
    """详情页必须把 holdings_infer 写回持仓；以前只接受 benchmark_index，主动基金一直空。"""

    record = PrimarySectorRecord(
        fund_code="000960",
        sector_name="CXO",
        intraday_index_name=None,
        source="holdings_infer",
        confidence=0.8,
    )

    def fake_resolve(fund_code, **kwargs):
        assert kwargs.get("fetch_holdings_infer") is True
        assert fund_code == "000960"
        return record

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        fake_resolve,
    )
    monkeypatch.setattr(
        "app.services.fund_profile.save_fund_profile",
        lambda profile: profile,
    )

    holding = Holding(
        fund_code="000960",
        fund_name="招商医疗保健股票A",
        holding_amount=1000,
    )
    profile = FundProfile(
        fund_code="000960",
        fund_name="招商医疗保健股票A",
        aliases=["招商医疗保健股票A"],
        holding_amount=1000,
        source="alipay-overview",
    )
    resolved = FundProfileService()._resolve_holding_with_profile(
        holding,
        profile,
        fetch_benchmark=True,
        fetch_holdings_infer=True,
    )
    assert resolved.sector_name == "CXO"
    assert resolved.intraday_index_name == "国证CXO"


def test_holding_detail_requests_holdings_infer(monkeypatch) -> None:
    from app.services.holding_detail_service import (
        HoldingDetailDataContext,
        build_holding_detail,
    )

    captured: dict[str, object] = {}

    def fake_resolve(self, holding, profile, **kwargs):
        captured.update(kwargs)
        return holding

    monkeypatch.setattr(FundProfileService, "_resolve_holding_with_profile", fake_resolve)
    monkeypatch.setattr(
        HoldingDetailDataContext,
        "find_profile",
        lambda self, holding, profile_service: None,
    )
    monkeypatch.setattr(
        "app.services.holding_detail_service.FundDataService",
        lambda: SimpleNamespace(
            get_nav_history=lambda *args, **kwargs: SimpleNamespace(
                source="none",
                points=[],
                latest_nav=None,
                latest_date=None,
                period_change_percent=None,
            )
        ),
    )
    monkeypatch.setattr(
        "app.services.holding_detail_service.compute_yesterday_profit",
        lambda holding: None,
    )
    monkeypatch.setattr(
        "app.services.holding_detail_service._remember_holding_detail_cache",
        lambda *args, **kwargs: None,
    )

    context = HoldingDetailDataContext()
    context._snapshots_loaded = True
    context._snapshots = []
    holding = Holding(
        fund_code="000960",
        fund_name="招商医疗保健股票A",
        holding_amount=1000,
    )
    build_holding_detail([holding], 0, data_context=context)
    assert captured.get("fetch_holdings_infer") is True


def test_display_fields_map_cxo_to_guozheng_index() -> None:
    from app.services.fund_primary_sector_service import _display_fields_from_primary_record

    fields = _display_fields_from_primary_record(
        PrimarySectorRecord(
            fund_code="000960",
            sector_name="CXO",
            intraday_index_name=None,
            source="holdings_infer",
            confidence=0.8,
        )
    )
    assert fields == {"sector_name": "CXO", "intraday_index_name": "国证CXO"}


def test_fast_refresh_infers_only_missing_sectors(monkeypatch) -> None:
    from app.services.fund_primary_sector_service import refresh_benchmark_sectors_for_holdings

    inferred: list[str] = []

    def fake_infer(fund_code, **kwargs):
        inferred.append(fund_code)
        return PrimarySectorRecord(
            fund_code=fund_code,
            sector_name="CXO",
            intraday_index_name="国证CXO",
            source="holdings_infer",
            confidence=0.8,
        )

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_holdings_infer",
        fake_infer,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.apply_primary_sector_to_holding",
        lambda holding, **kwargs: holding,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_holdings_sector_infer.fetch_portfolio_stocks_with_industry_evidence",
        lambda _code: {"stocks": []},
    )

    mapped = Holding(
        fund_code="000001",
        fund_name="已有板块基金",
        holding_amount=100,
        sector_name="半导体",
    )
    missing = Holding(
        fund_code="000960",
        fund_name="招商医疗保健股票A",
        holding_amount=1000,
    )
    refreshed = refresh_benchmark_sectors_for_holdings(
        [mapped, missing],
        fetch_missing_benchmark=False,
        fetch_holdings_infer=True,
        infer_missing_only=True,
    )
    assert inferred == ["000960"]
    assert refreshed[0].sector_name == "半导体"
    assert refreshed[1].sector_name == "CXO"
    assert refreshed[1].intraday_index_name == "国证CXO"
