from app.models import FundProfile, Holding
from app.services.holding_amount_sync import (
    bootstrap_holding_baselines,
    sync_holding_amounts_from_shares,
)


def test_sync_holding_amounts_uses_estimate_nav(monkeypatch):
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=9508.74,
        holding_shares=6734.71,
        holding_cost=1.3784,
        source="test",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda code: profile if code == "025856" else None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda fund_code, trade_date: None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda fund_code: 1.41,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.save_fund_profile",
        lambda item: item,
    )

    holdings = [
        Holding(
            fund_code="025856",
            fund_name="华夏中证电网设备主题ETF联接A",
            holding_amount=9508.74,
            return_percent=2.43,
            holding_return_percent=2.43,
        )
    ]
    estimate_quotes = {
        "025856": {
            "estimated_nav": 1.4282,
            "change_percent": 3.73,
        }
    }

    updated = sync_holding_amounts_from_shares(
        holdings,
        estimate_quotes=estimate_quotes,
        persist_profiles=True,
    )

    assert updated[0].holding_amount == 9618.51
    assert updated[0].holding_profit == 335.39


def test_bootstrap_locks_shares_from_overview_amount(monkeypatch):
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=9508.74,
        source="test",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda code: profile if code == "025856" else None,
    )
    saved: list[FundProfile] = []
    monkeypatch.setattr(
        "app.services.holding_amount_sync.save_fund_profile",
        lambda item: saved.append(item) or item,
    )

    holdings = [
        Holding(
            fund_code="025856",
            fund_name="华夏中证电网设备主题ETF联接A",
            holding_amount=9508.74,
            return_percent=2.43,
            holding_return_percent=2.43,
        )
    ]
    estimate_quotes = {"025856": {"estimated_nav": 1.4282}}

    bootstrap_holding_baselines(
        holdings,
        estimate_quotes=estimate_quotes,
        force_reset_shares=True,
    )

    assert saved
    assert saved[-1].holding_shares == 6657.71 or abs(saved[-1].holding_shares - 6657.71) < 1
    # 9508.74 / 1.4282 = 6657.71
    assert abs(saved[-1].holding_shares - round(9508.74 / 1.4282, 2)) < 0.02


def test_sync_holding_amounts_uses_official_unit_nav(monkeypatch):
    profile = FundProfile(
        fund_code="519674",
        fund_name="银河创新成长混合A",
        holding_amount=4001.68,
        holding_shares=329.24,
        source="test",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda code: profile if code == "519674" else None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda fund_code, trade_date: 4.15,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda fund_code: 12.0179,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.save_fund_profile",
        lambda item: item,
    )

    holdings = [
        Holding(
            fund_code="519674",
            fund_name="银河创新成长混合A",
            holding_amount=4001.68,
            return_percent=-3.79,
            holding_return_percent=-3.79,
            holding_profit=-157.77,
        )
    ]

    updated = sync_holding_amounts_from_shares(
        holdings,
        estimate_quotes={},
        persist_profiles=False,
    )

    assert updated[0].holding_amount == round(329.24 * 12.0179, 2)


def test_sync_skips_without_shares_or_nav(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda code: None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )

    holdings = [
        Holding(
            fund_code="015945",
            fund_name="易方达国防军工混合C",
            holding_amount=814.29,
            return_percent=-8.39,
        )
    ]

    updated = sync_holding_amounts_from_shares(holdings, persist_profiles=False)
    assert updated[0].holding_amount == 814.29
