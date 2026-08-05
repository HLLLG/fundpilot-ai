from __future__ import annotations

import pytest

from app.services.amac_benchmark_index_data import amac_theme_label_for_code
from app.services.fund_benchmark_sector import resolve_sector_from_benchmark
from scripts.sync_amac_benchmark_index_library import _infer_theme_label


def test_broad_financial_indices_are_not_fintech() -> None:
    for code in ("000992", "399914", "000974", "H50007"):
        assert amac_theme_label_for_code(code) is None

    assert _infer_theme_label("中证全指金融指数", "行业主题指数") is None
    assert _infer_theme_label("沪深300金融地产指数", "行业主题指数") is None
    assert _infer_theme_label("中证金融科技主题指数", "行业主题指数") == "金融科技"


def test_broad_financial_benchmark_text_no_longer_resolves_to_fintech() -> None:
    assert (
        resolve_sector_from_benchmark(
            "中证全指金融地产指数收益率×95%+银行活期存款利率×5%"
        )
        is None
    )


def test_letter_prefixed_sector_index_code_is_not_dropped() -> None:
    resolved = resolve_sector_from_benchmark(
        "中证银行指数收益率×95%+银行人民币活期存款利率（税后）×5%"
    )

    assert resolved is not None
    sector_name, _intraday_name, match = resolved
    assert sector_name == "银行"
    assert match.index_code == "H30022"


def test_unclassified_broad_prefix_does_not_shadow_specific_sector() -> None:
    resolved = resolve_sector_from_benchmark(
        "中证全指房地产指数收益率×95%+银行人民币活期存款利率（税后）×5%"
    )

    assert resolved is not None
    sector_name, _intraday_name, match = resolved
    assert sector_name == "房地产"
    assert match.index_code == "931775"


@pytest.mark.parametrize(
    ("benchmark_text", "expected_sector", "expected_code"),
    [
        ("中证细分化工产业主题指数收益率×95%+存款×5%", "化工", "000813"),
        ("中证煤炭指数收益率×95%+存款×5%", "煤炭", "399998"),
        ("中证煤炭等权指数收益率×95%+存款×5%", "煤炭", "399990"),
        ("中证全指家用电器指数收益率×95%+存款×5%", "家电", "930697"),
        ("中证沪深港黄金产业股票指数收益率×95%+存款×5%", "黄金股", "931238"),
        ("中证沪港深高股息指数收益率×95%+存款×5%", "红利", "930917"),
        ("中证800制药与生物科技指数收益率×95%+存款×5%", "医药", "000841"),
        ("国证信息技术创新主题指数收益率×95%+存款×5%", "信创", "CN5075"),
        ("上海黄金交易所Au99.99现货实盘合约收益率×95%", "黄金", "AU9999"),
    ],
)
def test_exact_passive_benchmark_catalog_keeps_source_identity(
    benchmark_text: str,
    expected_sector: str,
    expected_code: str,
) -> None:
    resolved = resolve_sector_from_benchmark(benchmark_text)

    assert resolved is not None
    sector_name, _intraday_name, match = resolved
    assert sector_name == expected_sector
    assert match.index_code == expected_code
