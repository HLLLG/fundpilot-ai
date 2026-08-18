"""支付宝全称与东财简称的自动选码：能对上才填，对不上宁可留空。"""
from __future__ import annotations

from app.services.fund_code_resolver import (
    lookup_fund_code_by_name,
    lookup_similar_fund_by_name,
    resolve_transaction_fund_code,
)
from app.services.fund_name_fuzzy import best_fuzzy_fund_match, best_similar_fund_match
from app.services.fund_name_utils import (
    is_fund_name_match,
    normalize_fund_name_for_lookup,
    sanitize_fund_name,
)


def _install_table(monkeypatch, table: list[tuple[str, str]]) -> None:
    from app.services import fund_code_resolver as resolver

    monkeypatch.setattr(resolver, "_fund_name_table_cache", table)
    monkeypatch.setattr(resolver, "_fund_name_index_cache", None)


def test_gold_share_class_aliases_match_despite_optional_index_token() -> None:
    assert is_fund_name_match("南方黄金股C", "南方黄金股指数C")
    assert is_fund_name_match("南方黄金股指数C", "南方黄金股C")
    assert not is_fund_name_match("南方黄金股C", "南方黄金股A")
    assert not is_fund_name_match("南方黄金股C", "博时黄金ETF联接A")


def test_sanitize_strips_alipay_metrics_glued_to_name() -> None:
    assert (
        sanitize_fund_name("鹏扬中证数字经济主题ETF联接C517.74+17.74+15.38+3.55%")
        == "鹏扬中证数字经济主题ETF联接C"
    )
    assert (
        sanitize_fund_name("博时黄金ETF联接A2,039.76+39.76+24.33+1.99%")
        == "博时黄金ETF联接A"
    )
    assert (
        sanitize_fund_name("国泰国证房地产行业指数(LOF)A97254-2746+268-275%")
        == "国泰国证房地产行业指数(LOF)A"
    )
    assert (
        sanitize_fund_name("新华鑫科技3个月滚动持有灵活配置混合A")
        == "新华鑫科技3个月滚动持有灵活配置混合A"
    )


def test_normalize_strips_flexible_allocation_and_fund_type_words() -> None:
    assert (
        normalize_fund_name_for_lookup("万家宏观择时多策略灵活配置混合C")
        == "万家宏观择时多策略混合C"
    )
    assert (
        normalize_fund_name_for_lookup("招商医疗保健股票型证券投资基金A")
        == "招商医疗保健股票A"
    )


def test_lookup_auto_selects_wanjia_c_class(monkeypatch) -> None:
    _install_table(
        monkeypatch,
        [
            ("519212", "万家宏观择时多策略混合A"),
            ("017787", "万家宏观择时多策略混合C"),
        ],
    )
    code, source = lookup_fund_code_by_name("万家宏观择时多策略灵活配置混合C")
    assert code == "017787"
    assert source == "akshare"


def test_lookup_prefers_exact_merchants_health_over_frontier(monkeypatch) -> None:
    _install_table(
        monkeypatch,
        [
            ("000960", "招商医疗保健股票A"),
            ("011373", "招商前沿医疗保健股票A"),
            ("011374", "招商前沿医疗保健股票C"),
        ],
    )
    code, _source = lookup_fund_code_by_name("招商医疗保健股票A")
    assert code == "000960"


def test_lookup_does_not_auto_pick_frontier_health_fund(monkeypatch) -> None:
    _install_table(
        monkeypatch,
        [
            ("011373", "招商前沿医疗保健股票A"),
            ("011374", "招商前沿医疗保健股票C"),
        ],
    )
    code, source = lookup_fund_code_by_name("招商医疗保健股票A")
    assert code is None
    assert source is None


def test_fuzzy_auto_match_rejects_extra_product_token() -> None:
    assert (
        best_fuzzy_fund_match(
            "招商医疗保健股票A",
            [
                ("011373", "招商前沿医疗保健股票A"),
                ("011374", "招商前沿医疗保健股票C"),
            ],
        )
        is None
    )


def test_similar_match_picks_closest_share_class_sibling() -> None:
    hit = best_similar_fund_match(
        "招商医疗保健股票A",
        [
            ("011373", "招商前沿医疗保健股票A"),
            ("011374", "招商前沿医疗保健股票C"),
        ],
    )
    assert hit is not None
    assert hit[0] == "011373"


def test_ocr_similar_lookup_fills_frontier_health_when_exact_missing(monkeypatch) -> None:
    _install_table(
        monkeypatch,
        [
            ("011373", "招商前沿医疗保健股票A"),
            ("011374", "招商前沿医疗保健股票C"),
        ],
    )
    hit = lookup_similar_fund_by_name("招商医疗保健股票A")
    assert hit == ("011373", "招商前沿医疗保健股票A")


def test_transaction_ocr_fills_similar_fund_when_exact_missing(monkeypatch) -> None:
    _install_table(
        monkeypatch,
        [
            ("011373", "招商前沿医疗保健股票A"),
            ("011374", "招商前沿医疗保健股票C"),
        ],
    )
    code, source = resolve_transaction_fund_code("招商医疗保健股票A")
    assert code == "011373"
    assert source == "similar"


def test_lookup_maps_alipay_gold_index_alias_to_eastmoney_short_name(monkeypatch) -> None:
    _install_table(
        monkeypatch,
        [
            ("021959", "南方黄金股C"),
            ("002610", "博时黄金ETF联接A"),
        ],
    )
    code, source = lookup_fund_code_by_name("南方黄金股指数C")
    assert code == "021959"
    assert source == "akshare"


def test_transaction_ocr_exact_match_is_not_marked_similar(monkeypatch) -> None:
    _install_table(
        monkeypatch,
        [
            ("000960", "招商医疗保健股票A"),
            ("011373", "招商前沿医疗保健股票A"),
        ],
    )
    code, source = resolve_transaction_fund_code("招商医疗保健股票A")
    assert code == "000960"
    assert source is None
