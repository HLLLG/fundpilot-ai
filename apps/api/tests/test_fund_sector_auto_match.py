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


def test_semantic_sector_from_fund_name_matches_competitor_examples():
    from app.services.sector_labels import infer_semantic_sector_from_fund_name

    cases = {
        "华夏中证电网设备主题ETF发起式联接C": "电网设备",
        "中欧上证科创板人工智能指数C": "人工智能",
        "天弘科创芯片设计主题ETF发起联接C": "科创芯片设计",
        "富国全球科技互联网股票(QDII)C": "海外基金",
        "天弘全球高端制造混合(QDII)C": "全球高端制造",
        "广发全球精选股票(QDII)人民币C": "全球精选股票",
    }

    for fund_name, expected in cases.items():
        candidate = infer_semantic_sector_from_fund_name(fund_name)
        assert candidate is not None, fund_name
        assert candidate.sector_name == expected
        assert candidate.source == "semantic_name"
        assert candidate.confidence >= 0.55


def test_semantic_sector_ignores_generic_product_words():
    from app.services.sector_labels import infer_semantic_sector_from_fund_name

    for fund_name in (
        "某某灵活配置混合C",
        "某某成长精选股票A",
        "某某稳健回报混合C",
    ):
        assert infer_semantic_sector_from_fund_name(fund_name) is None


def test_legacy_name_infer_keeps_existing_keyword_behavior():
    from app.services.sector_labels import infer_sector_label_from_fund_name

    assert infer_sector_label_from_fund_name("某某国防军工混合C") == "国防军工"
    assert infer_sector_label_from_fund_name("某某CPO主题股票A") == "CPO"


def test_legacy_name_infer_does_not_expand_to_registered_themes():
    from app.services.sector_labels import infer_sector_label_from_fund_name

    assert infer_sector_label_from_fund_name("某某银行指数A") is None
    assert infer_sector_label_from_fund_name("某某黄金ETF联接C") is None
