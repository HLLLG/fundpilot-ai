"""当日买入递延计收益：官方净值公布后仍应保持 0 当日收益、不滚结算金额。"""

from __future__ import annotations

from app.models import FundProfile, Holding
from app.services.holding_amount_sync import sync_holding_amounts_from_shares
from app.services.holding_estimates import apply_sector_daily_estimates, overlay_official_nav_returns
from app.services.profit_accrual_defer import (
    is_profit_accrual_deferred,
    ocr_signals_pending_profit_accrual,
    resolve_profile_defer_patch,
)


def _deferred_holding() -> Holding:
    return Holding(
        fund_code="008281",
        fund_name="天弘半导体设备指数C",
        holding_amount=3000.0,
        settled_holding_amount=3000.0,
        holding_profit=0.0,
        holding_return_percent=0.0,
        return_percent=0.0,
        daily_profit=0.0,
        sector_return_percent=3.3,
        sector_return_percent_source="closing_estimate",
        amount_includes_today=False,
    )


def _deferred_profile() -> FundProfile:
    return FundProfile(
        fund_code="008281",
        fund_name="天弘半导体设备指数C",
        holding_amount=3000.0,
        settled_holding_amount=3000.0,
        holding_shares=2500.0,
        holding_profit=0.0,
        holding_return_percent=0.0,
        profit_accrual_deferred_until="2026-06-25",
        source="alipay-overview",
    )


def test_apply_sector_daily_estimates_deferred_overrides_official_nav(monkeypatch):
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda **_kw: "2026-06-25",
    )
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.get_profile_for_holding",
        lambda _h: _deferred_profile(),
    )

    holding = _deferred_holding().model_copy(
        update={
            "daily_return_percent": 2.65,
            "daily_profit": 79.23,
            "daily_return_percent_source": "official_nav",
        }
    )
    result = apply_sector_daily_estimates(holding)

    assert result.daily_profit == 0.0
    assert result.daily_return_percent == 0.0
    assert result.daily_return_percent_source == "pending_accrual"
    assert result.sector_return_percent == 3.3


def test_overlay_official_nav_skips_deferred_holding(monkeypatch):
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda **_kw: "2026-06-25",
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_official_nav_return",
        lambda _code, _date: 2.65,
    )
    monkeypatch.setattr(
        "app.database.list_fund_profiles",
        lambda: [_deferred_profile()],
    )

    holding = _deferred_holding()
    result = overlay_official_nav_returns([holding])[0]

    assert result.daily_return_percent_source != "official_nav"


def test_ocr_pending_accrual_without_daily_profit_field():
    holding = Holding(
        fund_code="021533",
        fund_name="天弘半导体材料设备指数C",
        holding_amount=3000.0,
        holding_profit=0.0,
        return_percent=0.0,
    )
    assert ocr_signals_pending_profit_accrual(holding) is True
    patch = resolve_profile_defer_patch(
        holding,
        FundProfile(fund_code="021533", fund_name="天弘半导体材料设备指数C"),
    )
    assert patch.get("profit_accrual_deferred_until") is not None


def test_ocr_pending_accrual_treats_return_percent_zero_as_zero():
    holding = Holding(
        fund_code="021533",
        fund_name="天弘半导体设备指数C",
        holding_amount=3000.0,
        holding_profit=0.0,
        holding_return_percent=None,
        return_percent=0.0,
        yesterday_profit=0.0,
    )
    assert ocr_signals_pending_profit_accrual(holding) is True


def test_sync_holding_amounts_keeps_ocr_settled_when_deferred(monkeypatch):
    monkeypatch.setattr(
        "app.services.trading_session.get_effective_trade_date",
        lambda **_kw: "2026-06-25",
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_official_nav_return",
        lambda _code, _date: 2.65,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_latest_unit_nav",
        lambda _code: 1.19588,
    )
    monkeypatch.setattr(
        "app.services.fund_estimate_provider.fetch_fund_estimate_quotes",
        lambda _holdings, timeout_seconds=6.0: {},
    )
    monkeypatch.setattr(
        "app.services.profit_accrual_defer.is_profit_accrual_deferred",
        lambda p: p is not None and p.profit_accrual_deferred_until == "2026-06-25",
    )

    profile = _deferred_profile()
    monkeypatch.setattr(
        "app.services.holding_amount_sync.list_fund_profiles",
        lambda: [profile],
    )

    holding = _deferred_holding()
    result = sync_holding_amounts_from_shares(
        [holding], persist_profiles=False, allow_nav_fetch=False
    )[0]

    assert result.settled_holding_amount == 3000.0
    assert result.holding_amount == 3000.0
    assert is_profit_accrual_deferred(profile)
