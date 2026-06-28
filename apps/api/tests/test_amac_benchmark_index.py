"""中基协指数要素库 → THEME_BOARD_INDEX / 业绩基准解析。"""

from __future__ import annotations

from app.services.amac_benchmark_index_data import (
    amac_code_to_theme_label,
    load_amac_library,
)
from app.services.fund_benchmark_sector import parse_benchmark_index, resolve_sector_from_benchmark
from app.services.sector_registry_data import THEME_BOARD_INDEX, THEME_BOARD_WHITELIST


def test_amac_library_loaded():
    library = load_amac_library()
    assert library["total"] >= 150
    assert library["resolved"] == library["total"]
    assert library["unresolved"] == []


def test_amac_expanded_theme_board_index():
    assert len(THEME_BOARD_INDEX) >= 76


def test_amac_code_maps_chip_industry_to_semiconductor():
    assert amac_code_to_theme_label().get("H30007") == "半导体"


def test_parse_benchmark_via_amac_name_tmt():
    text = "中证TMT产业主题指数收益率×95%+银行活期存款利率（税后）×5%"
    match = parse_benchmark_index(text)
    assert match is not None
    assert match.index_code == "000998"


def test_resolve_sector_from_amac_tmt():
    text = "中证TMT产业主题指数收益率×95%+银行活期存款利率（税后）×5%"
    resolved = resolve_sector_from_benchmark(text)
    assert resolved is not None
    sector_name, _intraday, match = resolved
    assert match.index_code == "000998"
    assert sector_name == "电子"


def test_parse_benchmark_via_amac_name_800_quality():
    text = "中证800质量指数收益率×95%+银行活期存款利率（税后）×5%"
    match = parse_benchmark_index(text)
    assert match is not None
    assert match.index_code == "932433"


def test_amac_theme_labels_in_whitelist():
    for _code, label in amac_code_to_theme_label().items():
        assert label in THEME_BOARD_WHITELIST
