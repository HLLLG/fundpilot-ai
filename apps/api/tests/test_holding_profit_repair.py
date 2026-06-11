import pytest

from app.models import FundProfile, Holding
from app.services.holding_amount_sync import sync_holding_amounts_from_shares
from app.services.holding_estimates import (
    compute_estimated_holding_return_percent,
    compute_holding_profit,
)


def test_sync_does_not_overwrite_settled_holding_profit(monkeypatch):
    profile = FundProfile(
        fund_code="015945",
        fund_name="易方达国防军工混合C",
        holding_amount=805.69,
        holding_shares=621.71,
        holding_cost=2.0946,
        holding_return_percent=-9.36,
        holding_profit=-83.20,
        source="test",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda code: profile if code == "015945" else None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: 1.296,
    )

    holding = Holding(
        fund_code="015945",
        fund_name="易方达国防军工混合C",
        holding_amount=805.69,
        holding_return_percent=-9.36,
        holding_profit=-83.20,
        sector_return_percent=-1.99,
        sector_return_percent_source="realtime",
    )
    updated = sync_holding_amounts_from_shares(
        [holding],
        estimate_quotes={"015945": {"estimated_nav": 1.31}},
        persist_profiles=False,
    )
    synced = updated[0]
    assert synced.holding_amount == pytest.approx(round(621.71 * 1.31, 2), abs=0.02)
    assert synced.holding_profit == -83.20
    assert synced.holding_return_percent == -9.36
    assert synced.amount_includes_today is True


def test_repair_corrupted_holding_profit_after_bad_share_sync(monkeypatch):
    profile = FundProfile(
        fund_code="015945",
        fund_name="易方达国防军工混合C",
        holding_amount=805.56,
        holding_shares=621.71,
        holding_cost=2.0946,
        holding_return_percent=-9.36,
        holding_profit=-83.20,
        source="test",
    )
    monkeypatch.setattr(
        "app.database.get_fund_profile_by_code",
        lambda code: profile if code == "015945" else None,
    )
    holding = Holding(
        fund_code="015945",
        fund_name="易方达国防军工混合C",
        holding_amount=805.56,
        holding_return_percent=-39.27,
        holding_profit=-495.15,
        sector_return_percent=-1.99,
        sector_return_percent_source="realtime",
        amount_includes_today=True,
    )
    assert compute_estimated_holding_return_percent(holding) == pytest.approx(-11.35, abs=0.1)
    assert compute_holding_profit(holding) == pytest.approx(-99.56, abs=0.5)
