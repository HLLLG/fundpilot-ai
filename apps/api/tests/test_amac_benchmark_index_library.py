from __future__ import annotations

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
