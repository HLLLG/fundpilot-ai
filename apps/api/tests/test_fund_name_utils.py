"""基金名称归一化与查码比对（含 QDII 币种后缀）。"""

from app.services.fund_name_utils import (
    extract_share_class_letter,
    is_fund_name_match,
    lookup_match_score,
    normalize_fund_name_for_lookup,
)


def test_qdii_currency_suffix_normalized_for_lookup():
    ocr = "广发全球精选股票(QDII)C"
    db_rmb = "广发全球精选股票(QDII)人民币C"
    assert normalize_fund_name_for_lookup(ocr) == normalize_fund_name_for_lookup(db_rmb)


def test_qdii_currency_suffix_name_match():
    ocr = "广发全球精选股票(QDII)C"
    db_rmb = "广发全球精选股票(QDII)人民币C"
    db_usd = "广发全球精选股票(QDII)美元A"
    assert is_fund_name_match(ocr, db_rmb)
    assert lookup_match_score(ocr, db_rmb) > 0
    assert not is_fund_name_match(ocr, db_usd)


def test_qdii_share_class_extraction_with_optional_currency():
    assert extract_share_class_letter("广发全球精选股票(QDII)C") == "C"
    assert extract_share_class_letter("广发全球精选股票(QDII)人民币C") == "C"
    assert extract_share_class_letter("广发全球精选股票(QDII)美元A") == "A"
    assert extract_share_class_letter("天弘全球高端制造混合(QDII)C") == "C"


def test_qdii_fullwidth_parentheses_still_match():
    ocr = "广发全球精选股票（QDII)C"
    db = "广发全球精选股票(QDII)人民币C"
    assert is_fund_name_match(ocr, db)
