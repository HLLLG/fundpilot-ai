"""灵活配置且无法确定关联板块：身份表留空，持仓页也不展示猜测标签。"""

from app.models import Holding
from app.services.fund_holdings_return_estimate import (
    HoldingsReturnEstimate,
    apply_holdings_daily_estimates,
    should_use_holdings_weighted_daily,
)
from app.services.fund_primary_sector_service import (
    PrimarySectorRecord,
    apply_page_associated_sector,
    associated_sector_is_page_visible,
    is_unthemed_allocation_fund,
    strip_unthemed_allocation_associated_sector,
)
from app.services.holding_client import serialize_holding_for_client
from app.services.holding_estimates import apply_sector_daily_estimates, enrich_holding_estimates
from app.services.portfolio_holdings_service import _fast_serialize_holding_for_client


def _holding(
    fund_code: str,
    fund_name: str,
    *,
    sector_name: str | None = "半导体材料",
) -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name=fund_name,
        holding_amount=10000,
        sector_name=sector_name,
        sector_return_percent=-0.42,
        intraday_index_name="中证半导体材料设备主题指数",
    )


def test_authoritative_labels_do_not_restamp_unthemed_allocation(monkeypatch) -> None:
    from app.services.portfolio_holdings_service import apply_authoritative_sector_labels

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.get_fund_primary_sectors_by_codes",
        lambda _codes: {
            "012200": {"sector_name": "半导体材料", "source": "holdings_infer"},
            "000960": {"sector_name": "CXO", "source": "holdings_infer"},
        },
    )
    holdings = apply_authoritative_sector_labels(
        [
            _holding("012200", "新华鑫科技3个月滚动持有灵活配置混合A"),
            Holding(
                fund_code="000960",
                fund_name="招商医疗保健股票A",
                holding_amount=1000,
                sector_name=None,
            ),
        ]
    )
    assert holdings[0].sector_name is None
    assert holdings[0].sector_return_percent is None
    assert holdings[1].sector_name == "医疗"


def test_serialize_strips_unthemed_allocation_research_board() -> None:
    holding = _holding("012200", "新华鑫科技3个月滚动持有灵活配置混合A")
    stripped = strip_unthemed_allocation_associated_sector(holding)
    assert stripped.sector_name is None
    assert stripped.sector_return_percent is None

    enriched = enrich_holding_estimates(holding, profile=None)
    assert enriched.sector_name is None
    assert enriched.sector_return_percent is None

    payload = serialize_holding_for_client(holding, profile=None)
    assert payload["sector_name"] is None
    assert payload["sector_return_percent"] is None
    assert payload["intraday_index_name"] is None

    fast = _fast_serialize_holding_for_client(holding, profile=None)
    assert fast["sector_name"] is None
    assert fast["sector_return_percent"] is None
    assert fast["intraday_index_name"] is None


def test_unthemed_allocation_classifier_does_not_swallow_named_or_bond_funds() -> None:
    assert is_unthemed_allocation_fund("新华鑫科技3个月滚动持有灵活配置混合A") is True
    assert is_unthemed_allocation_fund("万家宏观择时多策略混合C") is True
    assert is_unthemed_allocation_fund("万家宏观择时多策略灵活配置混合C") is True
    assert is_unthemed_allocation_fund("南方医药保健灵活配置混合A") is False
    assert is_unthemed_allocation_fund("富国国防军工混合A") is False
    assert is_unthemed_allocation_fund("富国安泰90天滚动持有短债债券A") is False


def test_flexible_mixed_fund_hides_holdings_inferred_sector() -> None:
    assert (
        associated_sector_is_page_visible(
            fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
            sector_name="半导体材料",
            source="holdings_infer",
        )
        is False
    )
    holding = apply_page_associated_sector(
        _holding("012200", "新华鑫科技3个月滚动持有灵活配置混合A"),
        PrimarySectorRecord(
            fund_code="012200",
            sector_name="半导体材料",
            intraday_index_name=None,
            source="holdings_infer",
            confidence=0.89,
        ),
    )
    assert holding.sector_name is None
    assert holding.intraday_index_name is None
    assert holding.sector_return_percent is None


def test_industry_equity_fund_keeps_holdings_infer_theme() -> None:
    assert (
        associated_sector_is_page_visible(
            fund_name="招商医疗保健股票A",
            sector_name="医疗",
            source="holdings_infer",
        )
        is True
    )


def test_repair_named_healthcare_cxo_row_rewrites_parent() -> None:
    from app.services.fund_primary_sector_service import _repair_named_healthcare_cxo_row

    record = _repair_named_healthcare_cxo_row(
        {
            "fund_code": "011373",
            "sector_name": "CXO",
            "intraday_index_name": "国证CXO",
            "source": "holdings_infer",
            "confidence": 0.92,
            "detail": {
                "scores": {"CXO": 41.85, "医疗": 5.0},
                "fund_name": "招商前沿医疗保健股票A",
            },
        },
        code="011373",
        fund_name="招商前沿医疗保健股票A",
        persist=False,
    )
    assert record is not None
    assert record.sector_name == "医疗"
    assert record.intraday_index_name != "国证CXO"


def test_named_healthcare_fund_does_not_display_cxo_override() -> None:
    holding = apply_page_associated_sector(
        Holding(
            fund_code="011373",
            fund_name="招商前沿医疗保健股票A",
            holding_amount=1000,
            sector_name="CXO",
            intraday_index_name="国证CXO",
        ),
        PrimarySectorRecord(
            fund_code="011373",
            sector_name="CXO",
            intraday_index_name="国证CXO",
            source="holdings_infer",
            confidence=0.92,
        ),
    )
    assert holding.sector_name == "医疗"
    assert holding.intraday_index_name != "国证CXO"


def test_named_theme_fund_keeps_matching_holdings_infer() -> None:
    assert (
        associated_sector_is_page_visible(
            fund_name="富国国防军工混合A",
            sector_name="国防军工",
            source="holdings_infer",
        )
        is True
    )
    holding = apply_page_associated_sector(
        _holding("001048", "富国国防军工混合A", sector_name="国防军工"),
        PrimarySectorRecord(
            fund_code="001048",
            sector_name="国防军工",
            intraday_index_name=None,
            source="holdings_infer",
            confidence=0.9,
        ),
    )
    assert holding.sector_name == "国防军工"


def test_passive_index_fund_keeps_tracking_sector() -> None:
    assert (
        associated_sector_is_page_visible(
            fund_name="华夏半导体材料设备ETF联接A",
            sector_name="半导体材料",
            source="benchmark_index",
        )
        is True
    )
    holding = apply_page_associated_sector(
        _holding("012719", "华夏半导体材料设备ETF联接A"),
        PrimarySectorRecord(
            fund_code="012719",
            sector_name="半导体材料",
            source="benchmark_index",
            confidence=0.9,
            intraday_index_name="中证半导体材料设备主题指数",
        ),
    )
    assert holding.sector_name == "半导体材料"


def test_detail_resolution_hides_flexible_mixed_research_sector(monkeypatch) -> None:
    from app.models import FundProfile
    from app.services.fund_profile import FundProfileService

    record = PrimarySectorRecord(
        fund_code="012200",
        sector_name="半导体材料",
        intraday_index_name="中证半导体材料设备主题指数",
        source="holdings_infer",
        confidence=0.89,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        lambda *_args, **_kwargs: record,
    )
    monkeypatch.setattr(
        "app.services.fund_profile.save_fund_profile",
        lambda profile: profile,
    )
    holding = Holding(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        holding_amount=1000,
        sector_name="半导体材料",
        sector_return_percent=-0.42,
    )
    profile = FundProfile(
        fund_code="012200",
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        aliases=["新华鑫科技3个月滚动持有灵活配置混合A"],
        holding_amount=1000,
        sector_name="半导体材料",
        source="alipay-overview",
    )
    resolved = FundProfileService()._resolve_holding_with_profile(
        holding,
        profile,
        fetch_benchmark=True,
        fetch_holdings_infer=True,
    )
    assert resolved.sector_name is None
    assert resolved.intraday_index_name is None
    assert resolved.sector_return_percent is None


def test_flexible_mixed_uses_holdings_calculator_until_official_nav() -> None:
    holding = apply_page_associated_sector(
        _holding("012200", "新华鑫科技3个月滚动持有灵活配置混合A"),
        PrimarySectorRecord(
            fund_code="012200",
            sector_name="半导体材料",
            intraday_index_name=None,
            source="holdings_infer",
            confidence=0.89,
        ),
    )
    assert should_use_holdings_weighted_daily(holding) is True
    estimated = apply_holdings_daily_estimates(
        [holding],
        {"012200": HoldingsReturnEstimate(0.17, 72.24, 72.24, 10)},
    )[0]
    assert estimated.daily_return_percent == 0.17
    assert estimated.daily_return_percent_source == "holdings_estimate"
    assert estimated.sector_name is None
    preserved = apply_sector_daily_estimates(estimated)
    assert preserved.daily_return_percent_source == "holdings_estimate"
    assert preserved.daily_return_percent == 0.17


def _verified_semiconductor_materials_record() -> PrimarySectorRecord:
    return PrimarySectorRecord(
        fund_code="012200",
        sector_name="半导体材料",
        intraday_index_name="中证半导体材料设备主题指数",
        source="holdings_infer",
        confidence=0.89,
        detail={
            "scores": {"半导体材料": 39.49, "机械设备": 20.0},
            "qualification": {
                "sector_inference_eligible": True,
                "research_only": False,
            },
            "fund_name": "新华鑫科技3个月滚动持有灵活配置混合A",
        },
    )


def test_unthemed_allocation_clears_inferred_identity_from_discovery() -> None:
    from app.database import (
        get_fund_primary_sector,
        get_fund_primary_sector_global,
        get_fund_sector_current,
        list_fund_primary_sectors_by_sector_names,
        save_fund_primary_sector,
        save_fund_primary_sector_global,
    )
    from app.services.fund_primary_sector_service import resolve_primary_sector
    from app.services.fund_sector_identity import materialize_primary_sector_record

    record = _verified_semiconductor_materials_record()
    save_fund_primary_sector(
        fund_code=record.fund_code,
        sector_name=record.sector_name,
        intraday_index_name=record.intraday_index_name,
        source=record.source,
        confidence=record.confidence,
        detail=record.detail,
    )
    save_fund_primary_sector_global(
        fund_code=record.fund_code,
        sector_name=record.sector_name,
        intraday_index_name=record.intraday_index_name,
        source=record.source,
        confidence=record.confidence,
        detail=record.detail,
    )
    materialize_primary_sector_record(record)

    assert get_fund_primary_sector("012200")["sector_name"] == "半导体材料"
    assert get_fund_sector_current("012200")
    assert any(
        row["fund_code"] == "012200"
        for row in list_fund_primary_sectors_by_sector_names(["半导体材料"])
    )

    assert (
        resolve_primary_sector(
            "012200",
            fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
            fetch_benchmark=False,
            fetch_holdings_infer=False,
        )
        is None
    )
    assert get_fund_primary_sector("012200") is None
    assert get_fund_primary_sector_global("012200") is None
    assert get_fund_sector_current("012200") == []
    assert all(
        row["fund_code"] != "012200"
        for row in list_fund_primary_sectors_by_sector_names(["半导体材料"])
    )


def test_holdings_infer_does_not_write_unthemed_allocation_identity() -> None:
    from app.database import get_fund_sector_current
    from app.services.fund_holdings_sector_infer import HoldingStockRow
    from app.services.fund_primary_sector_service import _resolve_from_holdings_infer

    coverage = {"portfolio_weight_coverage_percent": 72.0}
    stocks = [
        HoldingStockRow(
            name="鼎龙股份",
            weight=12.0,
            industry="电子化学品Ⅱ",
            stock_code="300054",
            coverage=coverage,
            industry_pit_qualified=True,
        ),
        HoldingStockRow(
            name="安集科技",
            weight=10.0,
            industry="电子化学品Ⅱ",
            stock_code="688019",
            coverage=coverage,
            industry_pit_qualified=True,
        ),
    ]
    record = _resolve_from_holdings_infer(
        "012200",
        persist=True,
        materialize_research=True,
        fund_name="新华鑫科技3个月滚动持有灵活配置混合A",
        stocks=stocks,
        evidence_payload={"snapshot_hash": "012200-should-stay-empty"},
    )
    assert record is None
    assert get_fund_sector_current("012200") == []


def test_named_theme_flexible_fund_can_still_keep_identity() -> None:
    from app.database import get_fund_primary_sector, save_fund_primary_sector
    from app.services.fund_primary_sector_service import resolve_primary_sector

    save_fund_primary_sector(
        fund_code="001048",
        sector_name="国防军工",
        source="holdings_infer",
        confidence=0.9,
        detail={"qualification": {"sector_inference_eligible": True, "research_only": False}},
    )
    record = resolve_primary_sector(
        "001048",
        fund_name="富国国防军工混合A",
        fetch_benchmark=False,
        fetch_holdings_infer=False,
    )
    assert record is not None
    assert record.sector_name == "国防军工"
    assert get_fund_primary_sector("001048")["sector_name"] == "国防军工"


def test_macro_timing_fund_does_not_keep_coal_identity() -> None:
    from app.database import (
        get_fund_primary_sector,
        get_fund_sector_current,
        save_fund_primary_sector,
    )
    from app.services.fund_primary_sector_service import resolve_primary_sector
    from app.services.fund_sector_identity import materialize_primary_sector_record

    record = PrimarySectorRecord(
        fund_code="017787",
        sector_name="煤炭",
        intraday_index_name=None,
        source="holdings_infer",
        confidence=0.89,
        detail={
            "scores": {"煤炭": 34.65, "食品饮料": 16.34},
            "qualification": {
                "sector_inference_eligible": True,
                "research_only": False,
            },
            "fund_name": "万家宏观择时多策略混合C",
        },
    )
    save_fund_primary_sector(
        fund_code=record.fund_code,
        sector_name=record.sector_name,
        source=record.source,
        confidence=record.confidence,
        detail=record.detail,
    )
    materialize_primary_sector_record(record)
    assert get_fund_primary_sector("017787")["sector_name"] == "煤炭"

    assert (
        resolve_primary_sector(
            "017787",
            fund_name="万家宏观择时多策略混合C",
            fetch_benchmark=False,
            fetch_holdings_infer=False,
        )
        is None
    )
    assert get_fund_primary_sector("017787") is None
    assert get_fund_sector_current("017787") == []
