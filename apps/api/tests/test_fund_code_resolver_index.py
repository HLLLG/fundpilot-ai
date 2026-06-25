"""东财基金名称表索引：O(1) 查码 + 搜索复用索引。"""

from app.services import fund_code_resolver as fcr
from app.services.fund_code_resolver import (
    clear_all_fund_name_table_caches,
    lookup_fund_code_by_name,
    lookup_fund_name_by_code,
    search_funds_by_keyword,
)


def _install_table(table: list[tuple[str, str]]) -> None:
    clear_all_fund_name_table_caches()
    fcr._fund_name_table_cache = table
    fcr._fund_name_index_cache = None


def test_lookup_fund_code_exact_match_via_index():
    _install_table(
        [
            ("025857", "华夏中证电网设备主题ETF联接C"),
            ("026790", "中欧上证科创板人工智能指数C"),
        ]
    )
    code, source = lookup_fund_code_by_name("华夏中证电网设备主题ETF联接C")
    assert code == "025857"
    assert source == "akshare"


def test_lookup_fund_name_by_code_uses_by_code_index():
    _install_table([("025857", "华夏中证电网设备主题ETF联接C")])
    assert lookup_fund_name_by_code("025857") == "华夏中证电网设备主题ETF联接C"


def test_lookup_fund_code_qdii_currency_suffix_resolves_rmb_c():
    """支付宝省略「人民币」时仍能精确匹配东财 021277。"""
    _install_table(
        [
            ("021277", "广发全球精选股票(QDII)人民币C"),
            ("000906", "广发全球精选股票(QDII)美元A"),
            ("023402", "广发全球精选股票(QDII)人民币A"),
            ("016665", "天弘全球高端制造混合(QDII)C"),
        ]
    )
    code, source = lookup_fund_code_by_name("广发全球精选股票(QDII)C")
    assert code == "021277"
    assert source == "akshare"


def test_search_funds_qdii_currency_suffix_finds_rmb_c():
    _install_table(
        [
            ("021277", "广发全球精选股票(QDII)人民币C"),
            ("000906", "广发全球精选股票(QDII)美元A"),
        ]
    )
    items = search_funds_by_keyword("广发全球精选股票(QDII)C")
    assert items
    assert items[0]["fund_code"] == "021277"


def test_lookup_fund_code_semiconductor_material_equipment_alias():
    """支付宝「半导体材料设备」展示名应对齐东财「半导体设备」。"""
    _install_table([("021533", "天弘半导体设备指数C")])
    code, source = lookup_fund_code_by_name("天弘半导体材料设备指数C")
    assert code == "021533"
    assert source == "akshare"


def test_lookup_fund_code_partial_match_still_works():
    _install_table(
        [
            ("016665", "天弘全球高端制造混合(QDII)C"),
            ("022184", "富国全球科技互联网股票(QDII)C"),
        ]
    )
    code, source = lookup_fund_code_by_name("天弘全球高端制造混合(QDII)C")
    assert code == "016665"
    assert source == "akshare"


def test_search_funds_by_exact_code_uses_by_code_index():
    _install_table(
        [
            ("025857", "华夏中证电网设备主题ETF联接C"),
            ("026790", "中欧上证科创板人工智能指数C"),
        ]
    )
    items = search_funds_by_keyword("025857")
    assert items == [{"fund_code": "025857", "fund_name": "华夏中证电网设备主题ETF联接C"}]


def test_search_funds_by_code_prefix_uses_index():
    _install_table(
        [
            ("025857", "华夏中证电网设备主题ETF联接C"),
            ("025858", "另一只025前缀基金C"),
            ("026790", "中欧上证科创板人工智能指数C"),
        ]
    )
    items = search_funds_by_keyword("0258")
    codes = {item["fund_code"] for item in items}
    assert codes == {"025857", "025858"}


def test_search_funds_by_name_substring_uses_bigram_index():
    _install_table(
        [
            ("025857", "华夏中证电网设备主题ETF联接C"),
            ("026790", "中欧上证科创板人工智能指数C"),
        ]
    )
    items = search_funds_by_keyword("电网设备")
    assert len(items) == 1
    assert items[0]["fund_code"] == "025857"


def test_search_funds_normalized_substring_uses_norm_bigram_index():
    _install_table([("016665", "天弘全球高端制造混合(QDII)C")])
    items = search_funds_by_keyword("高端制造")
    assert len(items) == 1
    assert items[0]["fund_code"] == "016665"
