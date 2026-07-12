from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.portfolio_ledger import (
    LedgerIntegrityError,
    build_ledger_version,
    build_position_snapshot_payload,
    canonical_decimal,
    chain_ledger_events,
    create_cash_adjustment,
    create_cash_baseline,
    create_legacy_estimated_baseline,
    create_transaction_event,
    create_user_confirmed_baseline,
    fold_ledger_events,
    position_fingerprint,
)


T0 = "2026-07-01T00:00:00+08:00"
T1 = "2026-07-02T00:00:00+08:00"
T2 = "2026-07-03T00:00:00+08:00"
T3 = "2026-07-04T00:00:00+08:00"


def _position(state: dict, fund_code: str = "000001") -> dict:
    return next(row for row in state["positions"] if row["fund_code"] == fund_code)


def test_decimal_canonicalization_is_stable_and_rejects_non_finite_values() -> None:
    assert canonical_decimal(1.2300) == "1.23"
    assert canonical_decimal(Decimal("-0.000")) == "0"
    assert canonical_decimal("1000.0000") == "1000"
    assert canonical_decimal(None) is None
    with pytest.raises(ValueError):
        canonical_decimal(float("nan"))


def test_chain_hash_is_deterministic_and_conflicting_duplicate_ids_fail() -> None:
    baseline = create_user_confirmed_baseline(
        fund_code="000001",
        confirmed_shares="100",
        cost_basis_total="1000",
        effective_at=T0,
        recorded_at=T0,
        source_ref="baseline-1",
    )
    duplicate = dict(baseline)

    once = chain_ledger_events([baseline])
    deduplicated = chain_ledger_events([baseline, duplicate])
    assert once == deduplicated
    assert build_ledger_version([baseline]) == build_ledger_version([duplicate, baseline])

    conflict = {**baseline, "absolute_shares": "101"}
    with pytest.raises(LedgerIntegrityError):
        chain_ledger_events([baseline, conflict])


def test_legacy_baseline_is_estimated_but_user_baseline_is_truthful() -> None:
    legacy = create_legacy_estimated_baseline(
        fund_code="000001",
        shares="100",
        cost_basis_total="1000",
        effective_at=T0,
        recorded_at=T0,
        source_ref="legacy-profile-1",
    )
    confirmed = create_user_confirmed_baseline(
        fund_code="000002",
        confirmed_shares="50",
        cost_basis_total=None,
        effective_at=T0,
        recorded_at=T0,
        source_ref="user-baseline-1",
    )

    state = fold_ledger_events([legacy, confirmed], position_as_of=T1, known_at=T1)
    assert _position(state, "000001")["shares_quality"] == "estimated_legacy"
    assert _position(state, "000001")["cost_quality"] == "estimated_legacy"
    assert _position(state, "000002")["shares_quality"] == "user_confirmed"
    assert _position(state, "000002")["cost_basis_total_cny"] is None
    assert _position(state, "000002")["cost_quality"] == "unknown"


def test_pending_and_later_recorded_events_do_not_enter_old_settled_position() -> None:
    baseline = create_user_confirmed_baseline(
        fund_code="000001",
        confirmed_shares="100",
        cost_basis_total="1000",
        effective_at=T0,
        recorded_at=T0,
    )
    pending = create_transaction_event(
        event_id="pending-buy",
        fund_code="000001",
        direction="buy",
        status="pending",
        confirmed_shares="10",
        gross_amount="120",
        fee_yuan="1",
        effective_at=T1,
        recorded_at=T1,
    )
    learned_later = create_transaction_event(
        event_id="late-buy",
        fund_code="000001",
        direction="buy",
        status="confirmed",
        confirmed_shares="20",
        gross_amount="240",
        fee_yuan="2",
        effective_at=T1,
        recorded_at=T3,
    )

    old = fold_ledger_events(
        [baseline, pending, learned_later], position_as_of=T2, known_at=T2
    )
    assert _position(old)["settled_shares"] == "100"
    assert old["pending_transaction_count"] == 1

    current = fold_ledger_events(
        [baseline, pending, learned_later], position_as_of=T3, known_at=T3
    )
    assert _position(current)["settled_shares"] == "120"


def test_future_effective_pending_commitment_blocks_decision_completeness() -> None:
    baseline = create_user_confirmed_baseline(
        fund_code="000001",
        confirmed_shares="100",
        cost_basis_total="1000",
        effective_at=T0,
        recorded_at=T0,
    )
    future_sell = create_transaction_event(
        event_id="future-pending-sell",
        fund_code="000001",
        direction="sell",
        status="pending",
        confirmed_shares="100",
        gross_amount="1200",
        fee_yuan=None,
        effective_at=T3,
        recorded_at=T1,
    )

    snapshot = build_position_snapshot_payload(
        [baseline, future_sell],
        user_id=1,
        position_as_of=T1,
        captured_at=T2,
    )

    assert snapshot["positions"][0]["settled_shares"] == "100"
    assert snapshot["pending_transaction_count"] == 1
    assert snapshot["known_unsettled_transaction_count"] == 1
    assert snapshot["completeness"]["settled_position_complete"] is True
    assert snapshot["completeness"]["decision_position_complete"] is False
    assert snapshot["position_complete"] is False


def test_buy_uses_moving_average_and_sell_realizes_profit_with_known_fee() -> None:
    events = [
        create_user_confirmed_baseline(
            fund_code="000001",
            confirmed_shares="100",
            cost_basis_total="1000",
            effective_at=T0,
            recorded_at=T0,
        ),
        create_transaction_event(
            event_id="buy-1",
            fund_code="000001",
            direction="buy",
            status="confirmed",
            confirmed_shares="50",
            gross_amount="600",
            fee_yuan="6",
            effective_at=T1,
            recorded_at=T1,
        ),
        create_transaction_event(
            event_id="sell-1",
            fund_code="000001",
            direction="sell",
            status="confirmed",
            confirmed_shares="30",
            gross_amount="390",
            fee_yuan="3",
            effective_at=T2,
            recorded_at=T2,
        ),
    ]

    row = _position(fold_ledger_events(events, position_as_of=T3, known_at=T3))
    assert row["settled_shares"] == "120"
    assert row["cost_basis_total_cny"] == "1284.8"
    assert row["average_unit_cost_cny"] == "10.70666666666666666666666667"
    assert row["realized_profit_total_cny"] == "65.8"
    assert row["fee_complete"] is True


def test_unknown_fee_is_never_fabricated_as_zero() -> None:
    events = [
        create_user_confirmed_baseline(
            fund_code="000001",
            confirmed_shares="100",
            cost_basis_total="1000",
            effective_at=T0,
            recorded_at=T0,
        ),
        create_transaction_event(
            event_id="buy-unknown-fee",
            fund_code="000001",
            direction="buy",
            status="confirmed",
            confirmed_shares="50",
            gross_amount="600",
            fee_yuan=None,
            effective_at=T1,
            recorded_at=T1,
        ),
        create_transaction_event(
            event_id="sell-unknown-fee",
            fund_code="000001",
            direction="sell",
            status="confirmed",
            confirmed_shares="30",
            gross_amount="390",
            fee_yuan=None,
            effective_at=T2,
            recorded_at=T2,
        ),
    ]

    row = _position(fold_ledger_events(events, position_as_of=T3, known_at=T3))
    assert row["fee_complete"] is False
    assert row["realized_profit_total_cny"] is None
    assert row["realized_profit_before_fee_cny"] == "70"
    assert row["cost_quality"] == "derived"


def test_unknown_buy_fee_keeps_after_fee_realized_profit_unknown() -> None:
    events = [
        create_user_confirmed_baseline(
            fund_code="000001",
            confirmed_shares="100",
            cost_basis_total="100",
            effective_at=T0,
            recorded_at=T0,
        ),
        create_transaction_event(
            event_id="buy-unknown-fee-before-known-sell",
            fund_code="000001",
            direction="buy",
            status="confirmed",
            confirmed_shares="10",
            gross_amount="20",
            fee_yuan=None,
            effective_at=T1,
            recorded_at=T1,
        ),
        create_transaction_event(
            event_id="sell-known-fee-after-unknown-buy",
            fund_code="000001",
            direction="sell",
            status="confirmed",
            confirmed_shares="10",
            gross_amount="30",
            fee_yuan="1",
            effective_at=T2,
            recorded_at=T2,
        ),
    ]

    state = fold_ledger_events(events, position_as_of=T3, known_at=T3)
    row = _position(state)
    assert row["fee_complete"] is False
    assert row["realized_profit_total_cny"] is None
    assert row["realized_profit_before_fee_cny"] is not None
    snapshot = build_position_snapshot_payload(
        events,
        user_id=1,
        position_as_of=T3,
        captured_at=T3,
    )
    assert snapshot["completeness"]["cost_complete"] is False


def test_amount_nav_transaction_degrades_confirmed_baseline_share_quality() -> None:
    baseline = create_user_confirmed_baseline(
        fund_code="000001",
        confirmed_shares="100",
        cost_basis_total="1000",
        effective_at=T0,
        recorded_at=T0,
    )
    derived_buy = create_transaction_event(
        event_id="derived-buy",
        fund_code="000001",
        direction="buy",
        status="confirmed",
        confirmed_shares=None,
        gross_amount="220",
        fee_yuan=None,
        nav="11",
        effective_at=T1,
        recorded_at=T1,
    )
    row = _position(
        fold_ledger_events([baseline, derived_buy], position_as_of=T2, known_at=T2)
    )
    assert row["settled_shares"] == "120"
    assert row["shares_quality"] == "derived"


def test_full_redemption_closes_position_and_oversell_is_a_conflict() -> None:
    baseline = create_user_confirmed_baseline(
        fund_code="000001",
        confirmed_shares="100",
        cost_basis_total="1000",
        effective_at=T0,
        recorded_at=T0,
    )
    full_sell = create_transaction_event(
        event_id="sell-all",
        fund_code="000001",
        direction="sell",
        status="confirmed",
        confirmed_shares="100",
        gross_amount="1200",
        fee_yuan="5",
        effective_at=T1,
        recorded_at=T1,
    )
    closed = fold_ledger_events([baseline, full_sell], position_as_of=T2, known_at=T2)
    assert _position(closed)["settled_shares"] == "0"
    assert _position(closed)["position_status"] == "closed"
    assert closed["conflicts"] == []

    oversell = create_transaction_event(
        event_id="sell-too-many",
        fund_code="000001",
        direction="sell",
        status="confirmed",
        confirmed_shares="101",
        gross_amount="1212",
        fee_yuan="5",
        effective_at=T1,
        recorded_at=T1,
    )
    conflicted = fold_ledger_events([baseline, oversell], position_as_of=T2, known_at=T2)
    assert _position(conflicted)["settled_shares"] == "100"
    assert conflicted["conflicts"][0]["code"] == "oversell"


def test_newer_opening_baseline_resets_older_transactions() -> None:
    old_baseline = create_user_confirmed_baseline(
        fund_code="000001",
        confirmed_shares="100",
        cost_basis_total="1000",
        effective_at=T0,
        recorded_at=T0,
        source_ref="old-baseline",
    )
    old_buy = create_transaction_event(
        event_id="old-buy",
        fund_code="000001",
        direction="buy",
        status="confirmed",
        confirmed_shares="20",
        gross_amount="220",
        fee_yuan="1",
        effective_at=T1,
        recorded_at=T1,
    )
    new_baseline = create_user_confirmed_baseline(
        fund_code="000001",
        confirmed_shares="115",
        cost_basis_total="1180",
        effective_at=T2,
        recorded_at=T2,
        source_ref="new-baseline",
    )
    new_buy = create_transaction_event(
        event_id="new-buy",
        fund_code="000001",
        direction="buy",
        status="confirmed",
        confirmed_shares="5",
        gross_amount="55",
        fee_yuan="0.5",
        effective_at=T3,
        recorded_at=T3,
    )

    state = fold_ledger_events(
        [old_baseline, old_buy, new_baseline, new_buy],
        position_as_of=T3,
        known_at=T3,
    )
    assert _position(state)["settled_shares"] == "120"


def test_cash_stays_unknown_unless_baselined_and_unknown_fee_invalidates_it() -> None:
    assert create_cash_baseline(
        cash_balance=None, effective_at=T0, recorded_at=T0
    ) is None
    no_cash = fold_ledger_events([], position_as_of=T1, known_at=T1)
    assert no_cash["cash"] == {
        "balance_cny": None,
        "quality": "unknown",
        "known": False,
    }

    cash_baseline = create_cash_baseline(
        cash_balance="1000", effective_at=T0, recorded_at=T0
    )
    adjustment = create_cash_adjustment(
        cash_delta="50", effective_at=T1, recorded_at=T1
    )
    known = fold_ledger_events(
        [cash_baseline, adjustment], position_as_of=T2, known_at=T2
    )
    assert known["cash"]["balance_cny"] == "1050"
    assert known["cash"]["known"] is True

    unknown_fee_buy = create_transaction_event(
        event_id="cash-unknown-buy",
        fund_code="000001",
        direction="buy",
        status="confirmed",
        confirmed_shares="10",
        gross_amount="100",
        fee_yuan=None,
        effective_at=T2,
        recorded_at=T2,
    )
    invalidated = fold_ledger_events(
        [cash_baseline, adjustment, unknown_fee_buy],
        position_as_of=T3,
        known_at=T3,
    )
    assert invalidated["cash"]["balance_cny"] is None
    assert invalidated["cash"]["known"] is False


def test_snapshot_keeps_position_fingerprint_stable_across_nav_changes() -> None:
    baseline = create_user_confirmed_baseline(
        fund_code="000001",
        confirmed_shares="100",
        cost_basis_total="1000",
        effective_at=T0,
        recorded_at=T0,
    )
    kwargs = dict(
        events=[baseline],
        user_id=7,
        account_id="default",
        position_as_of=T1,
        captured_at=T1,
    )
    first = build_position_snapshot_payload(
        **kwargs,
        valuations={"000001": {"nav": "11", "nav_date": "2026-07-02"}},
    )
    second = build_position_snapshot_payload(
        **kwargs,
        valuations={"000001": {"nav": "12", "nav_date": "2026-07-02"}},
    )

    assert first["position_fingerprint"] == second["position_fingerprint"]
    assert first["valuation_version"] != second["valuation_version"]
    assert first["cash"]["balance_cny"] is None
    assert first["cash"]["known"] is False
    assert first["totals"]["total_assets_cny"] is None
    assert first["totals"]["invested_market_value_cny"] == "1100"
    assert first["snapshot_id"] != second["snapshot_id"]

    state = fold_ledger_events([baseline], position_as_of=T1, known_at=T1)
    assert position_fingerprint(state, ledger_version=first["ledger_version"]) == first[
        "position_fingerprint"
    ]


def test_decision_repository_round_trip_remains_foldable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "ledger.db"))
    from app.config import refresh_settings
    from app.database import _connect
    from app.services.decision_repository import (
        append_portfolio_ledger_event,
        list_portfolio_ledger_events,
    )

    refresh_settings()
    event = create_user_confirmed_baseline(
        fund_code="000001",
        confirmed_shares="88.5",
        cost_basis_total="900",
        effective_at=T0,
        recorded_at=T0,
        source_ref="round-trip-baseline",
    )
    with _connect() as connection:
        appended = append_portfolio_ledger_event(
            user_id=7,
            event=event,
            connection=connection,
        )
        rows = list_portfolio_ledger_events(
            user_id=7,
            connection=connection,
        )

    assert appended["logical_event_id"] == event["logical_event_id"]
    assert len(rows) == 1
    state = fold_ledger_events(rows, position_as_of=T1, known_at=T1)
    assert _position(state)["settled_shares"] == "88.5"
    assert build_ledger_version(rows, known_at=T1).startswith("pl1:1:")
