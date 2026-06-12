from __future__ import annotations

from unittest.mock import patch

from app.models import Holding
from app.services.fund_primary_sector_service import (
    GLOBAL_FUND_SECTOR_SEEDS,
    apply_primary_sector_to_holding,
    recommend_sector_from_holdings,
    resolve_primary_sector,
)
from app.services.overview_pipeline import enrich_holdings_from_profiles


def test_global_seed_maps_519674_to_semiconductor():
    record = resolve_primary_sector("519674", allow_name_infer=False)
    assert record is not None
    assert record.sector_name == "半导体"
    assert record.source == "seed"


def test_global_seed_maps_015945_to_commercial_aerospace():
    record = resolve_primary_sector("015945", allow_name_infer=False)
    assert record is not None
    assert record.sector_name == "商业航天"
    assert record.source == "seed"


def test_apply_primary_sector_skips_name_infer_for_defense_fund():
    holding = Holding(
        fund_code="999999",
        fund_name="易方达国防军工混合C",
        holding_amount=1000.0,
    )
    enriched = apply_primary_sector_to_holding(holding)
    assert enriched.sector_name != "国防军工"


def test_apply_primary_sector_uses_seed_by_code():
    holding = Holding(
        fund_code="519674",
        fund_name="银河创新成长混合A",
        holding_amount=1000.0,
    )
    enriched = apply_primary_sector_to_holding(holding)
    assert enriched.sector_name == "半导体"


def test_recommend_sector_from_holdings_scores_semiconductor():
    mock_holdings = [
        {"name": "北方华创", "weight": 9.5},
        {"name": "中微公司", "weight": 8.2},
        {"name": "海光信息", "weight": 7.1},
    ]
    with patch(
        "app.services.fund_primary_sector_service._fetch_holdings_subprocess",
        return_value=mock_holdings,
    ):
        record = recommend_sector_from_holdings("519674")
    assert record is not None
    assert record.sector_name == "半导体"
    assert record.source == "holdings_infer"


def test_recommend_sector_from_holdings_scores_commercial_aerospace():
    mock_holdings = [
        {"name": "中航光电", "weight": 8.0},
        {"name": "中航沈飞", "weight": 7.5},
        {"name": "航发动力", "weight": 6.2},
    ]
    with patch(
        "app.services.fund_primary_sector_service._fetch_holdings_subprocess",
        return_value=mock_holdings,
    ):
        record = recommend_sector_from_holdings("015945")
    assert record is not None
    assert record.sector_name == "商业航天"


def test_enrich_holdings_from_profiles_uses_seed_not_fund_name(monkeypatch):
    holding = Holding(
        fund_code="015945",
        fund_name="易方达国防军工混合C",
        holding_amount=1000.0,
    )
    enriched = enrich_holdings_from_profiles([holding])
    assert len(enriched) == 1
    assert enriched[0].sector_name == "商业航天"


def test_all_reference_seeds_exist():
    assert GLOBAL_FUND_SECTOR_SEEDS["025856"]["sector_name"] == "电网设备"
    assert GLOBAL_FUND_SECTOR_SEEDS["008586"]["sector_name"] == "人工智能"
