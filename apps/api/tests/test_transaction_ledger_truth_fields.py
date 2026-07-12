from __future__ import annotations

from app.models import FundProfile, FundTransaction, Holding, ParsedTransaction
from app.services import transaction_ledger
from app.services.decision_repository import list_portfolio_ledger_events


def _transaction(**overrides) -> FundTransaction:
    payload = {
        "id": "tx-1",
        "fund_code": "000001",
        "fund_name": "测试基金",
        "direction": "buy",
        "amount_yuan": 120.0,
        "trade_time": "2026-07-01 14:30:00",
        "confirm_date": "2026-07-01",
        "status": "pending",
        "dedup_key": "dedup",
        "created_at": "2026-07-01T07:00:00+00:00",
    }
    payload.update(overrides)
    return FundTransaction(**payload)


def test_old_parsed_transaction_remains_backward_compatible() -> None:
    parsed = ParsedTransaction(
        direction="buy",
        fund_name="测试基金",
        fund_code="000001",
        amount_yuan=100,
        trade_time="2026-07-01 14:30:00",
    )
    assert parsed.confirmed_shares is None
    assert parsed.fee_yuan is None


def test_transaction_identity_normalizes_code_and_trade_time() -> None:
    compact = ParsedTransaction(
        direction="buy",
        fund_name="测试基金",
        fund_code="1234",
        amount_yuan=100,
        trade_time="2026-07-01 14:30",
    )
    explicit = ParsedTransaction(
        direction="buy",
        fund_name="测试基金",
        fund_code="001234",
        amount_yuan=100,
        trade_time="2026-07-01 14:30:00",
    )

    assert compact.fund_code == explicit.fund_code == "001234"
    assert compact.trade_time == explicit.trade_time == "2026-07-01 14:30:00"
    assert transaction_ledger._dedup_key(compact) == transaction_ledger._dedup_key(explicit)


def test_invalid_confirm_date_is_rejected_before_any_transaction_write() -> None:
    from pydantic import ValidationError

    try:
        ParsedTransaction(
            direction="buy",
            fund_name="测试基金",
            fund_code="000001",
            amount_yuan=100,
            trade_time="2026-07-01 14:30:00",
            confirm_date="2026-02-30",
        )
    except ValidationError as exc:
        assert "confirm_date" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid confirmation dates must fail validation")


def test_user_confirmed_shares_do_not_depend_on_nav(monkeypatch) -> None:
    tx = _transaction(confirmed_shares=12.3456, fee_yuan=1.25)
    updates: list[dict] = []
    nav_calls: list[str] = []
    monkeypatch.setattr(transaction_ledger, "list_pending_fund_transactions", lambda: [tx])
    monkeypatch.setattr(
        transaction_ledger,
        "get_unit_nav_on_date",
        lambda *_args: nav_calls.append("called") or None,
    )
    monkeypatch.setattr(
        transaction_ledger,
        "update_fund_transaction",
        lambda tx_id, **kwargs: updates.append({"id": tx_id, **kwargs}),
    )

    assert transaction_ledger.confirm_pending_transactions() == 1
    assert updates[0]["shares_delta"] == 12.3456
    assert updates[0]["confirmed_shares"] == 12.3456
    assert updates[0]["shares_source"] == "user_confirmed"
    assert updates[0]["nav_on_confirm"] is None
    assert updates[0]["confirmed_at"] is not None
    assert nav_calls == []


def test_legacy_amount_nav_share_is_explicitly_derived(monkeypatch) -> None:
    tx = _transaction(amount_yuan=150)
    updates: list[dict] = []
    monkeypatch.setattr(transaction_ledger, "list_pending_fund_transactions", lambda: [tx])
    monkeypatch.setattr(transaction_ledger, "get_unit_nav_on_date", lambda *_args: 2.0)
    monkeypatch.setattr(
        transaction_ledger,
        "update_fund_transaction",
        lambda tx_id, **kwargs: updates.append({"id": tx_id, **kwargs}),
    )

    assert transaction_ledger.confirm_pending_transactions() == 1
    assert updates[0]["shares_delta"] == 75.0
    assert updates[0]["shares_source"] == "derived_amount_nav"


def test_in_progress_transaction_is_not_auto_confirmed(monkeypatch) -> None:
    tx = _transaction(in_progress=True, confirmed_shares=10)
    updates: list[dict] = []
    monkeypatch.setattr(transaction_ledger, "list_pending_fund_transactions", lambda: [tx])
    monkeypatch.setattr(transaction_ledger, "get_unit_nav_on_date", lambda *_args: 2.0)
    monkeypatch.setattr(
        transaction_ledger,
        "update_fund_transaction",
        lambda tx_id, **kwargs: updates.append({"id": tx_id, **kwargs}),
    )

    assert transaction_ledger.confirm_pending_transactions() == 0
    assert updates == []


def test_concurrent_confirmation_loser_is_an_idempotent_noop(monkeypatch) -> None:
    tx = _transaction(confirmed_shares=10)
    monkeypatch.setattr(transaction_ledger, "list_pending_fund_transactions", lambda: [tx])
    monkeypatch.setattr(
        transaction_ledger,
        "_get_pending_fund_transaction_on_connection",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        transaction_ledger,
        "append_portfolio_ledger_event",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("the losing confirmer must not append another event")
        ),
    )

    assert transaction_ledger.confirm_pending_transactions() == 0


def test_future_confirmation_date_stays_pending_even_with_reported_shares(monkeypatch) -> None:
    from datetime import date

    tx = _transaction(
        confirm_date="2026-07-02",
        confirmed_shares=10,
    )
    updates: list[dict] = []
    monkeypatch.setattr(transaction_ledger, "_current_china_date", lambda: date(2026, 7, 1))
    monkeypatch.setattr(transaction_ledger, "list_pending_fund_transactions", lambda: [tx])
    monkeypatch.setattr(
        transaction_ledger,
        "update_fund_transaction",
        lambda tx_id, **kwargs: updates.append({"id": tx_id, **kwargs}),
    )

    assert transaction_ledger.confirm_pending_transactions() == 0
    assert updates == []


def test_effective_shares_only_fold_confirmed_transactions(monkeypatch) -> None:
    profile = FundProfile(
        fund_code="000001",
        fund_name="测试基金",
        holding_shares=100,
        shares_baseline_date="2026-06-30",
    )
    transactions = [
        _transaction(id="confirmed", status="confirmed", shares_delta=10),
        _transaction(id="pending", status="pending", shares_delta=20),
        _transaction(id="skipped", status="skipped", shares_delta=30),
    ]
    monkeypatch.setattr(transaction_ledger, "get_fund_profile_by_code", lambda _code: profile)
    monkeypatch.setattr(
        transaction_ledger, "list_fund_transactions", lambda **_kwargs: transactions
    )

    assert transaction_ledger.compute_effective_shares_map(["000001"]) == {
        "000001": 110.0
    }


def test_effective_shares_respects_as_of_cutoff(monkeypatch) -> None:
    profile = FundProfile(
        fund_code="000001",
        fund_name="测试基金",
        holding_shares=100,
        shares_baseline_date="2026-06-30",
    )
    transactions = [
        _transaction(
            id="past",
            status="confirmed",
            confirm_date="2026-07-01",
            shares_delta=10,
        ),
        _transaction(
            id="future",
            status="confirmed",
            confirm_date="2026-07-10",
            shares_delta=20,
        ),
    ]
    monkeypatch.setattr(transaction_ledger, "get_fund_profile_by_code", lambda _code: profile)
    monkeypatch.setattr(
        transaction_ledger, "list_fund_transactions", lambda **_kwargs: transactions
    )

    assert transaction_ledger.compute_effective_shares_map(
        ["000001"], as_of_date="2026-07-05"
    ) == {"000001": 110.0}


def test_apply_preserves_confirmed_shares_fee_and_progress_state(monkeypatch) -> None:
    inserted: list[FundTransaction] = []
    monkeypatch.setattr(
        transaction_ledger,
        "insert_fund_transaction",
        lambda tx: inserted.append(tx) or True,
    )
    monkeypatch.setattr(
        transaction_ledger,
        "get_fund_profile_by_code",
        lambda _code: FundProfile(
            fund_code="000001", fund_name="测试基金", holding_amount=100
        ),
    )
    monkeypatch.setattr(transaction_ledger, "confirm_pending_transactions", lambda: 0)
    monkeypatch.setattr(transaction_ledger, "_seed_amounts_for_new_positions", lambda _codes: None)
    monkeypatch.setattr(transaction_ledger, "list_pending_fund_transactions", lambda: [])
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.sync_portfolio_from_profiles",
        lambda **_kwargs: [
            Holding(fund_code="000001", fund_name="测试基金", holding_amount=100)
        ],
    )

    result = transaction_ledger.apply_parsed_transactions(
        [
            ParsedTransaction(
                direction="sell",
                fund_name="测试基金",
                fund_code="000001",
                amount_yuan=25,
                confirmed_shares=12.5,
                fee_yuan=0.5,
                trade_time="2026-07-01 14:30:00",
                in_progress=True,
            )
        ]
    )

    assert result["inserted"] == 1
    assert inserted[0].confirmed_shares == 12.5
    assert inserted[0].fee_yuan == 0.5
    assert inserted[0].shares_source == "user_confirmed"
    assert inserted[0].in_progress is True


def test_database_round_trip_preserves_transaction_truth_fields(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "transactions.db"))
    from app.config import refresh_settings
    from app.database import (
        insert_fund_transaction,
        list_fund_transactions,
        update_fund_transaction,
    )

    refresh_settings()
    tx = _transaction(
        confirmed_shares=12.5,
        fee_yuan=0.8,
        shares_source="user_confirmed",
        in_progress=False,
    )
    assert insert_fund_transaction(tx) is True
    inserted = list_fund_transactions()[0]
    assert inserted.confirmed_shares == 12.5
    assert inserted.fee_yuan == 0.8
    assert inserted.shares_source == "user_confirmed"

    update_fund_transaction(
        tx.id,
        status="confirmed",
        shares_delta=12.5,
        nav_on_confirm=None,
        confirmed_shares=12.5,
        fee_yuan=0.8,
        shares_source="user_confirmed",
        confirmed_at="2026-07-01T08:00:00+00:00",
    )
    confirmed = list_fund_transactions()[0]
    assert confirmed.status == "confirmed"
    assert confirmed.shares_delta == 12.5
    assert confirmed.confirmed_shares == 12.5
    assert confirmed.fee_yuan == 0.8
    assert confirmed.confirmed_at == "2026-07-01T08:00:00+00:00"


def test_apply_atomically_double_writes_pending_and_confirmed_ledger(monkeypatch) -> None:
    from app.database import list_fund_transactions
    from app.services.portfolio_ledger import fold_ledger_events

    monkeypatch.setattr(transaction_ledger, "_seed_amounts_for_new_positions", lambda _codes: None)
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.sync_portfolio_from_profiles",
        lambda **_kwargs: [],
    )

    result = transaction_ledger.apply_parsed_transactions(
        [
            ParsedTransaction(
                direction="buy",
                fund_name="华夏人工智能ETF联接C",
                fund_code="008586",
                amount_yuan=25,
                confirmed_shares=12.3456789,
                fee_yuan=0.5,
                trade_time="2026-07-01 14:30:00",
            )
        ]
    )

    assert result["inserted"] == 1
    transactions = list_fund_transactions(fund_code="008586")
    assert len(transactions) == 1
    assert transactions[0].status == "confirmed"
    assert transactions[0].shares_delta == 12.345679
    events = list_portfolio_ledger_events(user_id=1, fund_code="008586")
    assert [row["status"] for row in events] == ["pending", "confirmed"]
    state = fold_ledger_events(
        events,
        position_as_of="2026-07-02",
        known_at="2099-01-01T00:00:00+08:00",
    )
    assert state["positions"][0]["settled_shares"] == "12.345679"
    assert state["pending_transaction_count"] == 0


def test_pending_transaction_insert_rolls_back_when_ledger_append_fails(monkeypatch) -> None:
    from app.database import list_fund_transactions

    def fail_append(**_kwargs):
        raise RuntimeError("injected ledger failure")

    monkeypatch.setattr(transaction_ledger, "append_portfolio_ledger_event", fail_append)

    try:
        transaction_ledger.apply_parsed_transactions(
            [
                ParsedTransaction(
                    direction="buy",
                    fund_name="华夏人工智能ETF联接C",
                    fund_code="008586",
                    amount_yuan=25,
                    confirmed_shares=12.5,
                    trade_time="2026-07-01 14:30:00",
                )
            ]
        )
    except RuntimeError as exc:
        assert "injected ledger failure" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("ledger append failure should propagate")

    assert list_fund_transactions() == []


def test_batch_pending_writes_roll_back_together_when_later_ledger_append_fails(
    monkeypatch,
) -> None:
    from app.database import list_fund_transactions

    original_append = transaction_ledger.append_portfolio_ledger_event
    calls = 0

    def fail_second_append(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected second ledger failure")
        return original_append(**kwargs)

    monkeypatch.setattr(
        transaction_ledger,
        "append_portfolio_ledger_event",
        fail_second_append,
    )
    items = [
        ParsedTransaction(
            direction="buy",
            fund_name="基金一",
            fund_code="000001",
            amount_yuan=25,
            confirmed_shares=10,
            trade_time="2026-07-01 14:30:00",
        ),
        ParsedTransaction(
            direction="buy",
            fund_name="基金二",
            fund_code="000002",
            amount_yuan=30,
            confirmed_shares=12,
            trade_time="2026-07-01 14:31:00",
        ),
    ]

    try:
        transaction_ledger.apply_parsed_transactions(items)
    except RuntimeError as exc:
        assert "second ledger failure" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("the whole pending batch must roll back")

    assert list_fund_transactions() == []
    assert list_portfolio_ledger_events(user_id=1) == []


def test_conflicting_duplicate_inside_one_request_is_rejected_before_writes() -> None:
    from app.database import list_fund_transactions

    first = ParsedTransaction(
        direction="buy",
        fund_name="测试基金",
        fund_code="000001",
        amount_yuan=100,
        confirmed_shares=10,
        trade_time="2026-07-01 14:30:00",
    )
    corrected = first.model_copy(update={"confirmed_shares": 11})

    try:
        transaction_ledger.apply_parsed_transactions([first, corrected])
    except transaction_ledger.TransactionTruthConflict as exc:
        assert exc.conflicts[0]["source"] == "duplicate_in_request"
    else:  # pragma: no cover
        raise AssertionError("ambiguous duplicate request must be rejected")

    assert list_fund_transactions() == []


def test_confirmation_rolls_back_when_confirmed_ledger_append_fails(monkeypatch) -> None:
    from app.database import insert_fund_transaction, list_fund_transactions

    tx = _transaction(confirmed_shares=12.5, shares_source="user_confirmed")
    assert insert_fund_transaction(tx) is True

    def fail_append(**_kwargs):
        raise RuntimeError("injected confirmation ledger failure")

    monkeypatch.setattr(transaction_ledger, "append_portfolio_ledger_event", fail_append)
    try:
        transaction_ledger.confirm_pending_transactions()
    except RuntimeError as exc:
        assert "injected confirmation ledger failure" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("confirmed ledger failure should propagate")

    stored = list_fund_transactions()[0]
    assert stored.status == "pending"
    assert stored.shares_delta is None


def test_duplicate_with_different_confirmed_truth_returns_conflict_before_writes(
    monkeypatch,
) -> None:
    from app.database import list_fund_transactions

    monkeypatch.setattr(transaction_ledger, "_seed_amounts_for_new_positions", lambda _codes: None)
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.sync_portfolio_from_profiles",
        lambda **_kwargs: [],
    )
    first = ParsedTransaction(
        direction="buy",
        fund_name="华夏人工智能ETF联接C",
        fund_code="008586",
        amount_yuan=25,
        confirmed_shares=10,
        fee_yuan=None,
        trade_time="2026-07-01 14:30:00",
        in_progress=True,
    )
    transaction_ledger.apply_parsed_transactions([first])
    corrected = first.model_copy(
        update={"confirmed_shares": 12, "fee_yuan": 1.5}
    )

    try:
        transaction_ledger.apply_parsed_transactions([corrected])
    except transaction_ledger.TransactionTruthConflict as exc:
        assert exc.conflicts[0]["diff"] == {
            "confirmed_shares": {"stored": 10.0, "requested": 12.0},
            "fee_yuan": {"stored": None, "requested": 1.5},
        }
    else:  # pragma: no cover
        raise AssertionError("truth correction must not be silently skipped")

    stored = list_fund_transactions()[0]
    assert stored.confirmed_shares == 10
    assert stored.fee_yuan is None


def test_exact_retry_heals_missing_new_buy_profile(monkeypatch) -> None:
    from app.database import delete_fund_profile, get_fund_profile_by_code

    monkeypatch.setattr(transaction_ledger, "_seed_amounts_for_new_positions", lambda _codes: None)
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.sync_portfolio_from_profiles",
        lambda **_kwargs: [],
    )
    item = ParsedTransaction(
        direction="buy",
        fund_name="华夏人工智能ETF联接C",
        fund_code="008586",
        amount_yuan=25,
        confirmed_shares=10,
        trade_time="2026-07-01 14:30:00",
    )

    first = transaction_ledger.apply_parsed_transactions([item])
    assert first["inserted"] == 1
    assert get_fund_profile_by_code("008586") is not None
    assert delete_fund_profile("008586") is True

    retry = transaction_ledger.apply_parsed_transactions([item])
    assert retry["inserted"] == 0
    assert retry["skipped"] == 1
    healed = get_fund_profile_by_code("008586")
    assert healed is not None
    assert healed.is_provisional is True


def test_canonical_request_reuses_legacy_formatted_dedup_record(monkeypatch) -> None:
    from app.database import insert_fund_transaction, list_fund_transactions

    legacy = _transaction(
        id="legacy-format",
        fund_code="1234",
        fund_name="测试基金",
        amount_yuan=100,
        trade_time="2026-07-01 14:30",
        confirm_date="2026-07-01",
        confirmed_shares=10,
        shares_source="user_confirmed",
        dedup_key="legacy-un-normalized-key",
    )
    assert insert_fund_transaction(legacy) is True
    monkeypatch.setattr(transaction_ledger, "_seed_amounts_for_new_positions", lambda _codes: None)
    monkeypatch.setattr(
        "app.services.portfolio_holdings_service.sync_portfolio_from_profiles",
        lambda **_kwargs: [],
    )

    result = transaction_ledger.apply_parsed_transactions(
        [
            ParsedTransaction(
                direction="buy",
                fund_name="测试基金",
                fund_code="001234",
                amount_yuan=100,
                confirmed_shares=10,
                trade_time="2026-07-01 14:30:00",
                confirm_date="2026-07-01",
            )
        ]
    )

    assert result["inserted"] == 0
    assert result["skipped"] == 1
    assert len(list_fund_transactions()) == 1
