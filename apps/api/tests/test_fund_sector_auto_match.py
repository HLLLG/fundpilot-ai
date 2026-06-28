"""板块自动匹配：行业映射与持仓穿透。"""

from __future__ import annotations

from app.services.fund_holdings_sector_infer import (
    HoldingStockRow,
    infer_sector_from_portfolio_stocks,
)
from app.services.fund_industry_theme_map import map_industry_to_theme_label
from app.services.fund_primary_sector_service import resolve_primary_sector


def test_map_industry_to_theme_label_semiconductor():
    assert map_industry_to_theme_label("半导体") == "半导体"


def test_map_industry_to_theme_label_em_industry_name():
    assert map_industry_to_theme_label("半导体设备") == "半导体"


def test_infer_sector_from_portfolio_stocks_weighted_vote():
    stocks = [
        HoldingStockRow(name="北方华创", weight=9.5, industry="半导体"),
        HoldingStockRow(name="中微公司", weight=8.0, industry="半导体"),
        HoldingStockRow(name="招商银行", weight=2.0, industry="银行"),
    ]
    result = infer_sector_from_portfolio_stocks("519674", stocks)
    assert result is not None
    sector_name, scores, evidence = result
    assert sector_name == "半导体"
    assert scores["半导体"] == 17.5
    assert len(evidence) == 3


def test_resolve_primary_sector_skips_name_infer_by_default(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )

    record = resolve_primary_sector("999999", fund_name="某某国防军工混合")
    assert record is None


def test_resolve_primary_sector_name_infer_only_when_allowed(monkeypatch):
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service._resolve_from_benchmark_index",
        lambda *_args, **_kwargs: None,
    )

    record = resolve_primary_sector(
        "999999",
        fund_name="某某国防军工混合",
        allow_name_infer=True,
    )
    assert record is not None
    assert record.source == "name_infer"
    assert record.sector_name == "国防军工"
