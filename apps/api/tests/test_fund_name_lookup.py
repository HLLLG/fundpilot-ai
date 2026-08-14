"""支付宝全称与东财简称的自动选码：能对上才填，对不上宁可留空。"""
from __future__ import annotations

from app.services.fund_code_resolver import lookup_fund_code_by_name
from app.services.fund_name_fuzzy import best_fuzzy_fund_match
from app.services.fund_name_utils import normalize_fund_name_for_lookup


def _install_table(monkeypatch, table: list[tuple[str, str]]) -> None:
    from app.services import fund_code_resolver as resolver

    monkeypatch.setattr(resolver, "_fund_name_table_cache", table)
    monkeypatch.setattr(resolver, "_fund_name_index_cache", None)


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
