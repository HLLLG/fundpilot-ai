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
