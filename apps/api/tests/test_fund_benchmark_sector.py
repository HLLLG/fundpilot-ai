"""业绩比较基准 → 关联板块自动解析。"""

from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.fund_benchmark_sector import (
    parse_benchmark_index,
    resolve_sector_from_benchmark,
)
from app.services.fund_primary_sector_service import (
    apply_primary_sector_to_holding,
    resolve_primary_sector,
)
from app.services.sector_canonical import get_canonical_sector


def test_parse_benchmark_index_semiconductor_material_equipment():
    text = "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%"
    match = parse_benchmark_index(text)
    assert match is not None
    assert match.index_code == "931743"


def test_resolve_sector_from_benchmark_maps_to_display_label():
    text = "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%"
    resolved = resolve_sector_from_benchmark(text)
    assert resolved is not None
    sector_name, intraday, match = resolved
    assert sector_name == "半导体材料"
    assert match.index_code == "931743"
    assert intraday is not None


def test_get_canonical_sector_prefers_semiconductor_material_over_semiconductor():
    canon = get_canonical_sector("半导体材料")
    assert canon is not None
    assert canon.source_code == "931743"
    assert canon.eastmoney_secid == "2.931743"


def test_resolve_primary_sector_021533_uses_benchmark(monkeypatch):
    benchmark = "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%"

    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: benchmark,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: FundProfile(
            fund_code="021533",
            fund_name="天弘半导体设备指数C",
            sector_name="半导体",
            source="alipay-overview",
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **kwargs: kwargs,
    )

    record = resolve_primary_sector("021533", fund_name="天弘半导体设备指数C")
    assert record is not None
    assert record.source == "benchmark_index"
    assert record.sector_name == "半导体材料"
    assert record.detail is not None
    assert record.detail["index_code"] == "931743"


def test_apply_primary_sector_overrides_wrong_semiconductor_on_holding(monkeypatch):
    benchmark = "中证半导体材料设备主题指数收益率×95%+银行活期存款利率（税后）×5%"

    monkeypatch.setattr(
        "app.services.fund_benchmark_sector.fetch_fund_benchmark_text",
        lambda _code: benchmark,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_primary_sector",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.save_fund_primary_sector",
        lambda **kwargs: kwargs,
    )

    holding = Holding(
        fund_code="021533",
        fund_name="天弘半导体设备指数C",
        holding_amount=3000.0,
        sector_name="半导体",
    )
    updated = apply_primary_sector_to_holding(holding)
    assert updated.sector_name == "半导体材料"
