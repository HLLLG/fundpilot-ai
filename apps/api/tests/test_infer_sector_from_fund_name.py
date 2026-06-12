from app.services.sector_labels import infer_sector_label_from_fund_name
from app.services.sector_quote_label import sector_display_label, sector_quote_lookup_label
from app.models import Holding


def test_infer_sector_label_from_fund_name():
    assert infer_sector_label_from_fund_name("易方达国防军工混合C") == "国防军工"
    assert infer_sector_label_from_fund_name("华夏人工智能ETF联接C") == "人工智能"
    assert infer_sector_label_from_fund_name("银河创新成长混合A") is None


def test_sector_quote_lookup_uses_fund_name_inference():
    holding = Holding(
        fund_code="015945",
        fund_name="易方达国防军工混合C",
        holding_amount=1000,
        return_percent=0,
    )
    assert sector_quote_lookup_label(holding) == "国防军工"
    assert sector_display_label(holding) == "国防军工"
