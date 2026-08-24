from __future__ import annotations

from datetime import date

from app.models import FundProfile, Holding, ParsedTransaction
from app.services.holding_exit import (
    fill_full_exit_amounts,
    is_exit_due,
    is_exit_pending,
    keep_amount_for_full_exit,
    next_calendar_day,
    purge_due_full_exits,
    stamp_full_exit_profile,
)
from app.services.holding_filters import is_inactive_holding


def _profile(**overrides) -> FundProfile:
    payload = {
        "fund_code": "011036",
        "fund_name": "嘉实中证稀土产业ETF联接C",
        "holding_amount": 5000.0,
    }
    payload.update(overrides)
    return FundProfile(**payload)


def _holding(**overrides) -> Holding:
    payload = {
        "fund_code": "011036",
        "fund_name": "嘉实中证稀土产业ETF联接C",
        "holding_amount": 5000.0,
        "settled_holding_amount": 5000.0,
    }
    payload.update(overrides)
    return Holding(**payload)


def test_fill_full_exit_amounts_splits_current_holding() -> None:
    parsed = [
        ParsedTransaction(
            direction="sell",
            fund_name="嘉实中证稀土产业ETF联接C",
            fund_code="011036",
            amount_yuan=0,
            trade_time="2026-08-24 14:34:25",
            confirmed_shares=400,
            full_exit=True,
        ),
        ParsedTransaction(
            direction="sell",
            fund_name="嘉实中证稀土产业ETF联接C",
            fund_code="011036",
            amount_yuan=0,
            trade_time="2026-08-24 14:26:16",
            confirmed_shares=100,
            full_exit=True,
        ),
    ]
    filled = fill_full_exit_amounts(parsed, {"011036": _profile()})
    assert [item.amount_yuan for item in filled] == [4000.0, 1000.0]


def test_stamp_full_exit_keeps_earlier_deadline(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.holding_exit.current_china_date",
        lambda: date(2026, 8, 24),
    )
    stamped = stamp_full_exit_profile(
        _profile(exit_pending_until="2026-08-25"),
        trade_date="2026-08-26",
        basis_amount=5000,
    )
    assert stamped.exit_pending_until == "2026-08-25"
    assert next_calendar_day("2026-08-24") == "2026-08-25"


def test_zero_shares_keep_amount_and_mark_next_day_exit(monkeypatch) -> None:
    saved: list[FundProfile] = []
    monkeypatch.setattr(
        "app.services.holding_exit.current_china_date",
        lambda: date(2026, 8, 24),
    )
    monkeypatch.setattr(
        "app.database.save_fund_profile",
        lambda profile: saved.append(profile) or profile,
    )
    holding, profile = keep_amount_for_full_exit(
        _holding(),
        _profile(),
        trade_date="2026-08-24",
    )
    assert holding.holding_amount == 5000.0
    assert holding.exit_pending_until == "2026-08-25"
    assert profile is not None
    assert profile.exit_pending_until == "2026-08-25"
    assert profile.exit_basis_amount == 5000.0
    assert saved == [profile]
    assert is_exit_pending(holding)
    assert not is_exit_due(profile, today="2026-08-24")
    assert is_exit_due(profile, today="2026-08-25")
    assert not is_inactive_holding(holding)


def test_exit_pending_zero_amount_stays_active() -> None:
    holding = _holding(
        holding_amount=0,
        settled_holding_amount=0,
        exit_pending_until="2026-08-25",
        exit_basis_amount=5000,
    )
    assert not is_inactive_holding(holding)


def test_purge_due_full_exits_removes_only_due_funds(monkeypatch) -> None:
    removed: list[str] = []
    monkeypatch.setattr(
        "app.database.list_fund_profiles",
        lambda: [
            _profile(fund_code="011036", exit_pending_until="2026-08-25"),
            _profile(
                fund_code="021959",
                fund_name="南方黄金股指数C",
                exit_pending_until="2026-08-26",
            ),
        ],
    )

    def _remove(code: str, fund_name: str | None = None) -> dict:
        removed.append(code)
        return {}

    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.remove_holding_from_portfolio",
        _remove,
    )
    assert purge_due_full_exits(today="2026-08-25") == ["011036"]
    assert removed == ["011036"]
