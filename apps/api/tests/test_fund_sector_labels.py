from app.models import Holding
from app.services.fund_primary_sector_service import (
    GLOBAL_FUND_SECTOR_SEEDS,
    primary_sector_fields_for_holding,
    resolve_primary_sector,
)
from app.services.fund_profile import _is_valid_sector_label
from app.services.sector_quote_service import refresh_holdings_sector_quotes


def test_fund_product_names_are_not_valid_sector_labels():
    assert not _is_valid_sector_label("\u4e2d\u822a\u673a\u9047\u9886\u822a\u6df7\u5408\u53d1\u8d77C")
    assert not _is_valid_sector_label(
        "\u5e7f\u53d1\u7535\u5b50\u4fe1\u606f\u4f20\u5a92\u4ea7\u4e1a\u7cbe\u9009\u80a1\u7968C"
    )
    assert _is_valid_sector_label("CPO")
    assert _is_valid_sector_label("传媒")


def test_primary_sector_seeds_for_reported_funds():
    for code, expected_sector in (("018957", "CPO"), ("010236", "传媒")):
        record = resolve_primary_sector(code, fund_name="placeholder")
        assert record is not None
        assert record.sector_name == expected_sector
        assert record.source == "seed"


def test_primary_sector_fields_replace_polluted_sector_name():
    polluted_name = "\u4e2d\u822a\u673a\u9047\u9886\u822a\u6df7\u5408\u53d1\u8d77C"
    holding = Holding(
        fund_code="018957",
        fund_name= polluted_name,
        holding_amount=1000.0,
        return_percent=0.69,
        sector_name=polluted_name,
    )
    assert not _is_valid_sector_label(polluted_name)
    fields = primary_sector_fields_for_holding(holding, allow_name_infer=True)
    assert fields["sector_name"] == "CPO"


def test_estimate_fallback_does_not_persist_fund_name_as_sector(monkeypatch):
    fund_name = "\u4e2d\u822a\u673a\u9047\u9886\u822a\u6df7\u5408\u53d1\u8d77C"
    holding = Holding(
        fund_code="018957",
        fund_name=fund_name,
        holding_amount=1000.0,
        return_percent=0.69,
        sector_name=None,
    )

    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_spot_boards_result",
        lambda **kwargs: type(
            "R",
            (),
            {
                "boards": {"concept": {}, "industry": {}, "index": {}},
                "provider_path": "relay_live",
                "from_stale_cache": False,
                "kline_prefetched": 0,
                "elapsed_seconds": 0.1,
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.prefetch_canonical_kline_quotes",
        lambda *args, **kwargs: 0,
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.fetch_fund_estimate_quotes",
            lambda holdings, timeout_seconds=None: {
            "018957": {
                "change_percent": 0.69,
                "fund_name": fund_name,
            }
        },
    )
    monkeypatch.setattr(
        "app.services.sector_quote_service.get_official_nav_return",
        lambda *args, **kwargs: None,
    )

    result = refresh_holdings_sector_quotes([holding], force_refresh=True, timeout_seconds=5.0)
    updated = result["holdings"][0]
    assert updated["sector_name"] == "CPO"
    assert updated["sector_name"] != holding.fund_name
