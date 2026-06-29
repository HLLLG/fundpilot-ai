"""盘中持有金额应保持上一交易日结算额，不因份额×净值漂移而抬升。"""

import pytest

from app.models import FundProfile, Holding
from app.services.holding_amount_sync import (
    _infer_purchase_unit_cost,
    _is_imputed_market_unit_cost,
    sync_holding_amounts_from_shares,
)


def _intraday_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_effective_trade_date",
        lambda: "2026-06-26",
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: None,
    )


def test_intraday_does_not_roll_settled_when_shares_times_nav_drifts(monkeypatch):
    """OCR 结算额与 shares×昨净值有偏差时，盘中不得改持有金额展示。"""
    _intraday_session(monkeypatch)
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9068.69,
        settled_holding_amount=9068.69,
        holding_shares=1000.0,
        holding_cost=8.5,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda _code: profile,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda _code, **_kwargs: 9.10038,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9100.38,
        settled_holding_amount=9068.69,
    )
    synced = sync_holding_amounts_from_shares([holding], persist_profiles=False)
    assert synced[0].settled_holding_amount == 9068.69
    assert synced[0].holding_amount == 9068.69


def test_intraday_repairs_polluted_profile_holding_amount(monkeypatch):
    """档案 holding_amount 被旧 sync 污染时，盘中 sync 应回写 OCR 结算锚点。"""
    _intraday_session(monkeypatch)
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9100.38,
        settled_holding_amount=9068.69,
        holding_shares=1000.0,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda _code: profile,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda _code, **_kwargs: 9.10038,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9100.38,
        settled_holding_amount=9068.69,
    )
    synced = sync_holding_amounts_from_shares([holding], persist_profiles=False)
    assert synced[0].holding_amount == 9068.69
    assert synced[0].settled_holding_amount == 9068.69


def test_resolve_settled_ignores_polluted_profile_holding_amount():
    from app.services.holding_amount_sync import _resolve_settled_amount

    holding = Holding(
        fund_code="008586",
        fund_name="测试",
        holding_amount=9068.69,
        settled_holding_amount=9068.69,
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="测试",
        holding_amount=9100.38,
        settled_holding_amount=9068.69,
    )
    assert _resolve_settled_amount(holding, profile) == 9068.69


def test_official_nav_published_rolls_settled(monkeypatch):
    """当日官方净值公布后滚入 shares×官方净值。"""
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: -5.02,
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9068.69,
        settled_holding_amount=9068.69,
        holding_shares=1000.0,
        holding_cost=8.5,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda _code: profile,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda _code, **_kwargs: 9.10038,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=9068.69,
        settled_holding_amount=9068.69,
    )
    synced = sync_holding_amounts_from_shares([holding], persist_profiles=False)
    assert synced[0].settled_holding_amount == 9100.38
    assert synced[0].holding_amount == 9100.38


def test_official_nav_rolls_settled_from_cached_return_without_unit_nav(monkeypatch):
    """fast 路径无单位净值缓存时，仍可用昨结算×(1+日涨跌%)滚入。"""
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=13671.67,
        settled_holding_amount=13671.67,
        holding_shares=7390.09,
        holding_cost=1.85,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda _code: profile,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=13671.67,
        settled_holding_amount=13671.67,
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert synced[0].settled_holding_amount == 13863.07
    assert synced[0].holding_amount == 13863.07


def test_official_nav_rolls_from_inflated_cost_plus_profit_base(monkeypatch):
    """OCR 累计收益污染 settled 时，应按昨结算×官方日涨跌滚入，而非 cost×(1+累计%)。"""
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=14253.95,
        settled_holding_amount=14253.95,
        holding_shares=7390.09,
        holding_cost=1.85,
        holding_profit=582.28,
        holding_return_percent=4.26,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda _code: profile,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=14253.95,
        settled_holding_amount=14253.95,
        holding_profit=582.28,
        holding_return_percent=4.26,
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert synced[0].settled_holding_amount == 13863.07
    assert synced[0].holding_amount == 13863.07
    assert synced[0].holding_profit == 582.28


def test_alipay_cost_profit_return_alignment(monkeypatch):
    """支付宝口径：13863.07 金额、552.10 收益、4.15% 收益率、1.8012 成本价。"""
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: 1.4,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: 1.8759,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=13671.67,
        settled_holding_amount=13671.67,
        holding_shares=7390.09,
        holding_cost=1.85,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda _code: profile,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )

    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=13671.67,
        settled_holding_amount=13671.67,
        holding_profit=552.10,
        holding_return_percent=4.15,
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert synced[0].holding_amount == 13863.07
    assert synced[0].holding_profit == pytest.approx(552.10, abs=0.1)
    assert synced[0].holding_return_percent == pytest.approx(4.15, abs=0.05)
    unit_cost = _infer_purchase_unit_cost(synced[0], 7390.09, market_amount=13863.07)
    assert unit_cost == 1.8012
    assert not _is_imputed_market_unit_cost(1.8012, synced[0], 7390.09, pre_roll=13671.67)


def test_ocr_official_nav_amount_not_rolled_again(monkeypatch):
    """支付宝 OCR 金额已是官方净值更新后总额，sync 不得再按日涨跌改写。"""
    _intraday_session(monkeypatch)
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_official_nav_return",
        lambda _code, _date: -1.06,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_cached_official_nav_return",
        lambda _code, _date: -1.06,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_latest_unit_nav",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.fund_nav_service.prime_official_nav_cache",
        lambda *_args, **_kwargs: {},
    )
    profile = FundProfile(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=11104.30,
        settled_holding_amount=11104.30,
        holding_shares=7843.13,
        holding_cost=1.4165,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.get_fund_profile_by_code",
        lambda _code: profile,
    )
    monkeypatch.setattr(
        "app.services.holding_amount_sync.fetch_fund_estimate_quotes",
        lambda *_args, **_kwargs: {},
    )
    holding = Holding(
        fund_code="025856",
        fund_name="华夏中证电网设备主题ETF联接A",
        holding_amount=11104.30,
        settled_holding_amount=11104.30,
        holding_profit=142.18,
        holding_return_percent=1.30,
        amount_includes_today=True,
        daily_return_percent_source="official_nav",
    )
    synced = sync_holding_amounts_from_shares(
        [holding],
        persist_profiles=False,
        allow_nav_fetch=False,
    )
    assert synced[0].holding_amount == 11104.30
    assert synced[0].settled_holding_amount == 11104.30
    assert synced[0].holding_profit == pytest.approx(142.18, abs=0.5)
    assert synced[0].holding_return_percent == pytest.approx(1.30, abs=0.05)
