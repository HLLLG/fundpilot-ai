"""跟踪指数简称优先于主题板短名，避免房地产落到中证全指 931775。"""

from __future__ import annotations

from app.models import Holding
from app.services.sector_canonical import get_canonical_sector
from app.services.sector_quote_label import sector_display_label, sector_quote_lookup_label


def _holding(
    *,
    sector_name: str | None,
    intraday_index_name: str | None,
    fund_name: str = "测试基金",
    fund_code: str = "999999",
) -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name=fund_name,
        holding_amount=1000.0,
        return_percent=0.0,
        sector_name=sector_name,
        intraday_index_name=intraday_index_name,
    )


def test_guozheng_realty_quote_uses_399393_not_theme_board() -> None:
    holding = _holding(
        sector_name="房地产",
        intraday_index_name="房地产指数",
        fund_name="国泰国证房地产行业指数A",
        fund_code="160218",
    )
    assert sector_quote_lookup_label(holding, profile=None) == "房地产指数"
    assert sector_display_label(holding) == "房地产指数"
    canon = get_canonical_sector("房地产指数")
    assert canon is not None
    assert canon.source_code == "399393"
    theme = get_canonical_sector("房地产")
    assert theme is not None
    assert theme.source_code == "931775"


def test_gold_spot_and_hsh_gold_use_tracking_short_names() -> None:
    gold = _holding(
        sector_name="黄金",
        intraday_index_name="黄金9999",
        fund_name="博时黄金ETF联接A",
        fund_code="002610",
    )
    assert sector_quote_lookup_label(gold, profile=None) == "黄金9999"
    assert sector_display_label(gold) == "黄金9999"
    canon = get_canonical_sector("黄金9999")
    assert canon is not None
    assert canon.source_code == "518880"
    assert get_canonical_sector("黄金").source_code == "518880"

    gold_feeder = _holding(
        sector_name="黄金",
        intraday_index_name=None,
        fund_name="博时黄金ETF联接A",
        fund_code="002610",
    )
    assert sector_display_label(gold_feeder) == "黄金9999"

    equity = _holding(
        sector_name="黄金股",
        intraday_index_name="沪港深黄金",
        fund_name="南方黄金股C",
        fund_code="021959",
    )
    assert sector_quote_lookup_label(equity, profile=None) == "沪港深黄金"
    assert sector_display_label(equity) == "沪港深黄金"


def test_unregistered_benchmark_index_name_falls_back_to_board() -> None:
    holding = _holding(
        sector_name="机械设备",
        intraday_index_name="中证高端装备制造指数",
        fund_name="天弘全球高端制造混合(QDII)C",
    )
    assert sector_quote_lookup_label(holding, profile=None) == "机械设备"
    assert sector_display_label(holding) == "机械设备"


def test_stale_gold_spot_mapping_does_not_override_registry(monkeypatch) -> None:
    """库里还记着 AU9999 时，不能继续用 boards 里那条 +0.99% 夜盘。"""
    from app.services import sector_quote_resolver as resolver
    from app.services.sector_canonical import CanonicalQuoteResult

    monkeypatch.setattr(
        resolver,
        "fetch_canonical_sector_quote",
        lambda label, boards: CanonicalQuoteResult(
            change_percent=-1.06,
            matched_name="黄金",
            source_type="index",
            source_code="518880",
        ),
    )
    result = resolver.resolve_sector_quote(
        "黄金",
        {"index": {"黄金9999": 0.99, "黄金": 0.99}},
        persisted_mapping={
            "source_type": "index",
            "source_name": "黄金9999",
            "source_code": "AU9999",
        },
        quote_label="黄金",
    )
    assert result.change_percent == -1.06
    assert result.source_code == "518880"
