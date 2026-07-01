from app.models import Holding
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_quote_label import sector_quote_lookup_label


def test_sector_quote_lookup_prefers_semiconductor_material_canonical():
    holding = Holding(
        fund_code="000000",
        fund_name="天弘半导体设备指数C",
        holding_amount=3000.0,
        return_percent=0.0,
        sector_name="半导体材料",
    )
    assert sector_quote_lookup_label(holding) == "半导体材料"


def test_sector_quote_lookup_uses_material_equipment_index_not_generic_semiconductor():
    holding = Holding(
        fund_code="000000",
        fund_name="天弘半导体设备指数C",
        holding_amount=3000.0,
        return_percent=0.0,
        sector_name="半导体",
        intraday_index_name="中证半导体材料设备主题指数",
    )
    label = sector_quote_lookup_label(holding)
    canon = get_canonical_sector(label)
    assert canon is not None
    assert canon.source_code == "931743"


def test_sector_quote_lookup_falls_back_to_board_when_benchmark_index_name_unmapped():
    """业绩基准原文抠出来的指数名（如"中证高端装备制造指数"）大多不在别名表里，
    不应该直接把这段查不到行情的原始文本当作 lookup key 返回——应该退回到已经
    注册过行情源的板块短名（如"机械设备"），否则详情页分时图会一直显示
    "暂无分时数据"。"""
    holding = Holding(
        fund_code="016665",
        fund_name="天弘全球高端制造混合(QDII)C",
        holding_amount=100.0,
        return_percent=0.0,
        sector_name="机械设备",
        intraday_index_name="中证高端装备制造指数",
    )
    label = sector_quote_lookup_label(holding)
    assert label == "机械设备"
    canon = get_canonical_sector(label)
    assert canon is not None
    assert canon.source_code == "932078"
