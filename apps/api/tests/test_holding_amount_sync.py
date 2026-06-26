"""盘中持有金额应保持上一交易日结算额，不因份额×净值漂移而抬升。"""

from app.models import FundProfile, Holding
from app.services.holding_amount_sync import sync_holding_amounts_from_shares


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
        lambda _code: 9.10038,
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
        lambda _code: 9.10038,
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
        lambda _code: 9.10038,
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
