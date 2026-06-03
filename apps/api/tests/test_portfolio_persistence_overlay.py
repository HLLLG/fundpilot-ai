from app.models import Holding
from app.services.portfolio_persistence import _overlay_sector_fields


def test_overlay_sector_fields_updates_board_and_index():
    base = Holding(
        fund_code="519674",
        fund_name="银河创新成长混合A",
        holding_amount=4042.24,
        return_percent=1.94,
        sector_name="+",
    )
    patch = Holding(
        fund_code="519674",
        fund_name="银河创新成长混合A",
        holding_amount=4042.24,
        return_percent=1.94,
        sector_name="半导体",
        sector_return_percent=4.01,
        intraday_index_name=None,
    )
    merged = _overlay_sector_fields(base, patch)
    assert merged.sector_name == "半导体"
    assert merged.sector_return_percent == 4.01
