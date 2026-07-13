from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.fund_primary_sector_types import PrimarySectorRecord
from app.services.overview_pipeline import enrich_holdings_from_profiles


def _disable_primary_sector_lookup(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.primary_sector_fields_for_holding",
        lambda *args, **kwargs: {},
    )


def test_enrich_profiles_batches_repeated_codes_and_aliases(monkeypatch):
    calls = {"list": 0, "get": 0}
    profile = FundProfile(
        fund_code="123456",
        fund_name="Alpha Growth Fund",
        aliases=["Legacy Alpha Alias"],
        holding_return_percent=4.5,
        holding_profit=11.0,
    )

    def fake_list():
        calls["list"] += 1
        return [profile]

    def fail_point_query(_code):
        calls["get"] += 1
        raise AssertionError("overview batch enrichment must not issue point profile queries")

    monkeypatch.setattr("app.services.fund_profile.list_fund_profiles", fake_list)
    monkeypatch.setattr(
        "app.services.fund_profile.get_fund_profile_by_code",
        fail_point_query,
    )
    _disable_primary_sector_lookup(monkeypatch)
    holdings = [
        Holding(
            fund_code="123456",
            fund_name="Alpha Growth Fund",
            holding_amount=100,
            user_note="first",
        ),
        Holding(
            fund_code="000000",
            fund_name="Legacy Alpha Alias",
            holding_amount=200,
            user_note="alias",
        ),
        Holding(
            fund_code="123456",
            fund_name="Alpha Growth Fund",
            holding_amount=300,
            user_note="last",
        ),
    ]

    enriched = enrich_holdings_from_profiles(holdings, fetch_benchmark=False)

    assert calls == {"list": 1, "get": 0}
    assert [item.fund_code for item in enriched] == ["123456", "123456", "123456"]
    assert [item.fund_name for item in enriched] == [
        "Alpha Growth Fund",
        "Legacy Alpha Alias",
        "Alpha Growth Fund",
    ]
    assert [item.holding_amount for item in enriched] == [100, 200, 300]
    assert [item.user_note for item in enriched] == ["first", "alias", "last"]
    assert [item.return_percent for item in enriched] == [4.5, 4.5, 4.5]
    assert [item.holding_return_percent for item in enriched] == [4.5, 4.5, 4.5]
    assert [item.holding_profit for item in enriched] == [11.0, 11.0, 11.0]


def test_enrich_profiles_keeps_exact_code_precedence_and_input_order(monkeypatch):
    calls = {"list": 0, "get": 0}
    alias_profile = FundProfile(
        fund_code="111111",
        fund_name="Alpha Fund",
        aliases=["Shared Legacy Alias"],
        holding_return_percent=1.25,
        holding_profit=10.0,
    )
    exact_code_profile = FundProfile(
        fund_code="222222",
        fund_name="Beta Fund",
        aliases=[],
        holding_return_percent=2.5,
        holding_profit=20.0,
    )

    def fake_list():
        calls["list"] += 1
        return [alias_profile, exact_code_profile]

    def fail_point_query(_code):
        calls["get"] += 1
        raise AssertionError("overview batch enrichment must not issue point profile queries")

    monkeypatch.setattr("app.services.fund_profile.list_fund_profiles", fake_list)
    monkeypatch.setattr(
        "app.services.fund_profile.get_fund_profile_by_code",
        fail_point_query,
    )
    _disable_primary_sector_lookup(monkeypatch)
    holdings = [
        Holding(
            fund_code="222222",
            fund_name="Shared Legacy Alias",
            holding_amount=20,
        ),
        Holding(
            fund_code="000000",
            fund_name="Shared Legacy Alias",
            holding_amount=10,
        ),
        Holding(
            fund_code="222222",
            fund_name="Beta Fund",
            holding_amount=30,
        ),
    ]

    enriched = enrich_holdings_from_profiles(holdings, fetch_benchmark=False)

    assert calls == {"list": 1, "get": 0}
    assert [item.holding_amount for item in enriched] == [20, 10, 30]
    assert [item.fund_code for item in enriched] == ["222222", "111111", "222222"]
    assert [item.return_percent for item in enriched] == [2.5, 1.25, 2.5]
    assert [item.holding_profit for item in enriched] == [20.0, 10.0, 20.0]


def test_enrich_profiles_reuses_saved_profile_for_later_code_and_alias(monkeypatch):
    calls = {"list": 0, "get": 0, "save": 0}
    profile = FundProfile(
        fund_code="333333",
        fund_name="Chip Growth Fund",
        aliases=["Chip Legacy Alias"],
        holding_return_percent=6.0,
    )

    def fake_list():
        calls["list"] += 1
        return [profile]

    def fail_point_query(_code):
        calls["get"] += 1
        raise AssertionError("overview batch enrichment must not issue point profile queries")

    def save_profile(updated):
        calls["save"] += 1
        return updated

    monkeypatch.setattr("app.services.fund_profile.list_fund_profiles", fake_list)
    monkeypatch.setattr(
        "app.services.fund_profile.get_fund_profile_by_code",
        fail_point_query,
    )
    monkeypatch.setattr("app.services.fund_profile.save_fund_profile", save_profile)
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.resolve_primary_sector",
        lambda *args, **kwargs: PrimarySectorRecord(
            fund_code="333333",
            sector_name="Semiconductors",
            intraday_index_name="Chip ETF",
            source="benchmark_index",
        ),
    )
    monkeypatch.setattr(
        "app.services.fund_primary_sector_service.primary_sector_fields_for_holding",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.fund_profile._is_valid_sector_label",
        lambda value: value == "Semiconductors",
    )
    monkeypatch.setattr(
        "app.services.overview_pipeline._is_valid_sector_label",
        lambda value: value == "Semiconductors",
    )
    holdings = [
        Holding(
            fund_code="333333",
            fund_name="Chip Growth Fund",
            holding_amount=30,
        ),
        Holding(
            fund_code="000000",
            fund_name="Chip Legacy Alias",
            holding_amount=10,
        ),
        Holding(
            fund_code="333333",
            fund_name="Chip Growth Fund",
            holding_amount=20,
        ),
    ]

    enriched = enrich_holdings_from_profiles(holdings, fetch_benchmark=True)

    assert calls == {"list": 1, "get": 0, "save": 1}
    assert [item.holding_amount for item in enriched] == [30, 10, 20]
    assert [item.fund_code for item in enriched] == ["333333", "333333", "333333"]
    assert [item.sector_name for item in enriched] == [
        "Semiconductors",
        "Semiconductors",
        "Semiconductors",
    ]
    assert [item.intraday_index_name for item in enriched] == [
        "Chip ETF",
        "Chip ETF",
        "Chip ETF",
    ]
    assert [item.return_percent for item in enriched] == [6.0, 6.0, 6.0]
