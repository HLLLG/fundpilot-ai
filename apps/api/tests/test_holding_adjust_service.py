from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import AdjustHoldingRequest, FundProfile, Holding
from app.services import holding_adjust_service, holding_amount_sync, portfolio_ledger_service
from app.services.portfolio_ledger import (
    create_legacy_estimated_baseline,
    create_transaction_event,
)


def _holding(*, amount: float = 1000.0) -> Holding:
    return Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=amount,
        settled_holding_amount=amount,
        holding_profit=100.0,
        holding_return_percent=11.1111,
        return_percent=11.1111,
    )


def _profile(*, amount: float = 1000.0, shares: float | None = 1000.0) -> FundProfile:
    return FundProfile(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=amount,
        settled_holding_amount=amount,
        holding_shares=shares,
        holding_profit=100.0,
        holding_return_percent=11.1111,
        holding_cost=0.9,
        shares_baseline_date="2026-07-01",
    )


def test_amount_edit_rebases_estimated_shares_and_survives_next_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_profile = _profile()
    saved_snapshot: dict[str, object] = {}

    def save_profile(profile: FundProfile) -> FundProfile:
        nonlocal current_profile
        current_profile = profile
        return profile

    monkeypatch.setattr(
        holding_adjust_service,
        "get_most_recent_portfolio_snapshot",
        lambda: {"snapshot_date": "2026-07-15", "holdings": [_holding().model_dump()]},
    )
    monkeypatch.setattr(
        holding_adjust_service,
        "get_fund_profile_by_code",
        lambda _code: current_profile,
    )
    monkeypatch.setattr(
        holding_adjust_service,
        "has_user_confirmed_position_shares",
        lambda _code: False,
    )
    monkeypatch.setattr(
        holding_adjust_service,
        "get_effective_trade_date",
        lambda: "2026-07-16",
    )
    monkeypatch.setattr(holding_adjust_service, "get_latest_unit_nav", lambda _code: 2.0)
    monkeypatch.setattr(holding_adjust_service, "save_fund_profile", save_profile)
    monkeypatch.setattr(holding_adjust_service, "enrich_holdings_estimates", lambda rows: rows)
    monkeypatch.setattr(holding_adjust_service, "get_portfolio_summary", lambda: None)
    monkeypatch.setattr(
        holding_adjust_service,
        "save_portfolio_summary",
        lambda _summary: None,
    )
    monkeypatch.setattr(
        holding_adjust_service,
        "save_daily_snapshot",
        lambda rows, summary: saved_snapshot.update(rows=rows, summary=summary),
    )
    monkeypatch.setattr(
        holding_adjust_service,
        "build_portfolio_holdings_response",
        lambda rows, **_kwargs: {"holdings": [row.model_dump() for row in rows]},
    )

    response = holding_adjust_service.adjust_holding_in_portfolio(
        "008586",
        AdjustHoldingRequest(settled_holding_amount=1250.0, holding_profit=50.0),
    )

    adjusted = Holding.model_validate(response["holdings"][0])
    assert adjusted.settled_holding_amount == 1250.0
    assert adjusted.holding_profit == 50.0
    assert adjusted.holding_return_percent == pytest.approx(4.1667)
    assert current_profile.holding_shares == 625.0
    assert current_profile.shares_baseline_date == "2026-07-16"
    assert current_profile.profit_settled_trade_date == "2026-07-16"
    assert current_profile.holding_cost == pytest.approx(1.92)
    assert saved_snapshot["rows"]

    # The next refresh must use the new 625-share baseline. The stale 1000-share
    # baseline would calculate 2000 and reproduce the original jump-back bug.
    monkeypatch.setattr(
        holding_amount_sync,
        "get_official_nav_return",
        lambda *_args, **_kwargs: 0.0,
    )
    monkeypatch.setattr(
        holding_amount_sync,
        "get_latest_unit_nav",
        lambda *_args, **_kwargs: 2.0,
    )
    refreshed, _ = holding_amount_sync._sync_one_holding(
        adjusted,
        profile=current_profile,
        trade_date="2026-07-16",
        estimate_quote=None,
        persist_profile=False,
        shares_override={"008586": current_profile.holding_shares or 0.0},
    )
    assert refreshed.settled_holding_amount == 1250.0


def test_amount_edit_rejects_confirmed_share_position(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        holding_adjust_service,
        "get_most_recent_portfolio_snapshot",
        lambda: {"snapshot_date": "2026-07-15", "holdings": [_holding().model_dump()]},
    )
    monkeypatch.setattr(
        holding_adjust_service,
        "get_fund_profile_by_code",
        lambda _code: _profile(),
    )
    monkeypatch.setattr(
        holding_adjust_service,
        "has_user_confirmed_position_shares",
        lambda _code: True,
    )

    with pytest.raises(holding_adjust_service.ConfirmedSharesAmountConflict, match="同步加仓"):
        holding_adjust_service.adjust_holding_in_portfolio(
            "008586",
            AdjustHoldingRequest(settled_holding_amount=1250.0, holding_profit=50.0),
        )


def test_inferred_shares_save_the_new_baseline_date(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[FundProfile] = []
    profile = _profile(shares=None)
    holding = _holding()
    monkeypatch.setattr(
        holding_amount_sync,
        "get_official_nav_return",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        holding_amount_sync,
        "get_latest_unit_nav",
        lambda *_args, **_kwargs: 2.0,
    )
    monkeypatch.setattr(
        holding_amount_sync,
        "save_fund_profile",
        lambda value: saved.append(value) or value,
    )

    holding_amount_sync._sync_one_holding(
        holding,
        profile=profile,
        trade_date="2026-07-16",
        estimate_quote=None,
        persist_profile=True,
    )

    assert saved[0].holding_shares == 500.0
    assert saved[0].shares_baseline_date == "2026-07-16"


def test_adjust_request_rejects_zero_amount() -> None:
    with pytest.raises(ValueError):
        AdjustHoldingRequest(settled_holding_amount=0)


def test_confirmed_share_detection_catches_mixed_quality_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)
    events = [
        create_legacy_estimated_baseline(
            fund_code="008586",
            shares=500,
            cost_basis_total=900,
            effective_at="2026-07-01",
            recorded_at="2026-07-01T08:00:00+00:00",
            event_id="legacy-baseline",
        ),
        create_transaction_event(
            event_id="confirmed-buy",
            fund_code="008586",
            direction="buy",
            status="confirmed",
            effective_at="2026-07-15",
            recorded_at="2026-07-15T08:00:00+00:00",
            confirmed_shares=10,
            shares_quality="user_confirmed",
        ),
    ]
    monkeypatch.setattr(portfolio_ledger_service, "get_request_user_id", lambda: 1)
    monkeypatch.setattr(portfolio_ledger_service, "_utc_now", lambda: now)
    monkeypatch.setattr(
        portfolio_ledger_service,
        "list_portfolio_ledger_events",
        lambda **_kwargs: events,
    )

    assert portfolio_ledger_service.has_user_confirmed_position_shares("008586") is True
