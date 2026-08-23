"""养基宝式展示：重仓投票有胜者就给关联板块，但不把不过线结果升成决策身份。"""

from __future__ import annotations

from app.database import get_fund_sector_current
from app.services.fund_holdings_sector_infer import HoldingStockRow
from app.services.fund_primary_sector_precompute import _evaluate_holdings_resolution
from app.services.fund_primary_sector_service import (
    _resolve_from_holdings_infer,
    recommend_sector_from_holdings,
)


def _mixed_pharma_stocks() -> list[HoldingStockRow]:
    coverage = {"portfolio_weight_coverage_percent": 50.0}
    return [
        HoldingStockRow(
            name="恒瑞医药",
            weight=18.0,
            industry="化学制药",
            stock_code="600276",
            coverage=coverage,
            industry_pit_qualified=True,
        ),
        HoldingStockRow(
            name="贵州茅台",
            weight=15.0,
            industry="白酒",
            stock_code="600519",
            coverage=coverage,
            industry_pit_qualified=True,
        ),
        HoldingStockRow(
            name="宁德时代",
            weight=12.0,
            industry="电池",
            stock_code="300750",
            coverage=coverage,
            industry_pit_qualified=True,
        ),
    ]


def test_holdings_infer_returns_display_sector_without_current_identity() -> None:
    record = _resolve_from_holdings_infer(
        "000960",
        persist=False,
        materialize_research=True,
        stocks=_mixed_pharma_stocks(),
        evidence_payload={
            "snapshot_hash": "display-fallback-1",
            "report_period": "2026Q2",
            "as_of": "2026-06-30",
            "available_at": "2026-07-21T09:00:00+08:00",
        },
    )

    assert record is not None
    assert record.sector_name == "医药"
    assert record.source == "holdings_infer"
    assert record.detail["qualification"]["research_only"] is True
    assert record.detail["qualification"]["sector_inference_eligible"] is False
    assert record.detail["display"]["method"] == "vote_winner"
    assert get_fund_sector_current("000960") == []


def test_unverified_pcb_display_falls_back_to_electronics() -> None:
    coverage = {"portfolio_weight_coverage_percent": 40.0}
    stocks = [
        HoldingStockRow(
            name="沪电股份",
            weight=12.0,
            industry="元件",
            stock_code="002463",
            coverage=coverage,
            industry_pit_qualified=True,
            theme="PCB",
            theme_pit_qualified=True,
            theme_available_at="2026-08-17T03:00:00+00:00",
        ),
        HoldingStockRow(
            name="三环集团",
            weight=8.0,
            industry="元件",
            stock_code="300408",
            coverage=coverage,
            industry_pit_qualified=True,
        ),
    ]

    record = _resolve_from_holdings_infer(
        "001701",
        persist=False,
        materialize_research=True,
        stocks=stocks,
        evidence_payload={"snapshot_hash": "pcb-display-1"},
    )

    assert record is not None
    assert record.sector_name == "电子"
    assert record.detail["display"]["method"] == "fine_theme_parent_fallback"
    assert record.detail["qualification"]["research_only"] is True
    assert get_fund_sector_current("001701") == []


def test_recommend_ignores_display_only_holdings(monkeypatch) -> None:
    display_record = _resolve_from_holdings_infer(
        "000961",
        persist=False,
        stocks=_mixed_pharma_stocks(),
        evidence_payload={"snapshot_hash": "recommend-1"},
    )
    assert display_record is not None
    assert display_record.detail["qualification"]["research_only"] is True

    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_holdings_infer",
        lambda *_args, **_kwargs: display_record,
    )

    assert recommend_sector_from_holdings("000961") is None


def test_precompute_keeps_display_fallback_as_research_only() -> None:
    from app.services.fund_holdings_sector_infer import assess_sector_from_portfolio_stocks

    stocks = _mixed_pharma_stocks()
    evaluation = _evaluate_holdings_resolution(
        "000962",
        {
            "status": "qualified",
            "stocks": stocks,
            "sector_clue": assess_sector_from_portfolio_stocks(stocks),
            "snapshot_hash": "precompute-display-1",
        },
    )

    assert evaluation.resolution_status == "research_only"
    assert evaluation.reason_code == "holdings_evidence_research_only"
