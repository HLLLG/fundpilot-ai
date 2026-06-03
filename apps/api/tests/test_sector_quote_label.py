from app.models import Holding
from app.services.sector_quote_label import sector_display_label, sector_quote_lookup_label


def test_lookup_prefers_intraday_index_over_related_board():
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=12406.59,
        return_percent=2.74,
        sector_name="电网设备",
        intraday_index_name="中证电网设备",
    )
    assert sector_quote_lookup_label(holding) == "中证电网设备"
    assert sector_display_label(holding) == "电网设备"


def test_lookup_falls_back_to_related_board_when_no_index():
    holding = Holding(
        fund_code="015945",
        fund_name="易方达国防军工混合C",
        holding_amount=1188.96,
        return_percent=-6.94,
        sector_name="商业航天",
    )
    assert sector_quote_lookup_label(holding) == "商业航天"
