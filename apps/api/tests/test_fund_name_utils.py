from app.services.fund_name_utils import (
    looks_like_fund_product_name,
    normalize_fund_name,
    normalize_fund_name_for_lookup,
    sanitize_fund_name,
)


def test_sanitize_strips_investment_tip_prefix():
    polluted = "投资锦囊北美云厂商持续加大资本支出华夏人工智能ETF联C"
    assert sanitize_fund_name(polluted) == "华夏人工智能ETF联接C"


def test_sanitize_strips_leading_ocr_junk():
    assert sanitize_fund_name("托易方达国防军工混合C") == "易方达国防军工混合C"
    assert sanitize_fund_name("托易方达国防军工混合") == "易方达国防军工混合"


def test_normalize_preserves_clean_fund_name():
    assert normalize_fund_name("银河创新成长混合A") == "银河创新成长混合A"
    assert normalize_fund_name("华夏中证电网设备主题ETF联接A") == "华夏中证电网设备主题ETF联接A"


def test_looks_like_fund_product_name_accepts_partial_ocr_names():
    assert looks_like_fund_product_name("华夏人工智能ETF联")
    assert looks_like_fund_product_name("华夏中证电网设备主")
    assert looks_like_fund_product_name("易方达国防军工混合")


def test_looks_like_fund_product_name_rejects_name_fragments():
    assert not looks_like_fund_product_name("题ETF联接A")
    assert not looks_like_fund_product_name("接C")
    assert not looks_like_fund_product_name("投资锦囊北美云厂商持续加大资本支出")


def test_normalize_fund_name_for_lookup_aligns_alipay_short_with_em_full_name():
    ocr = "天弘科创芯片设计ETF联接C"
    em = "天弘上证科创板芯片设计主题ETF发起联接C"
    assert normalize_fund_name_for_lookup(ocr) == normalize_fund_name_for_lookup(em)
