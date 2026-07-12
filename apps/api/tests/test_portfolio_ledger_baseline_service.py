from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from app.database import get_fund_profile_by_code, save_fund_profile
from app.models import (
    ConfirmPortfolioLedgerBaselineRequest,
    FundProfile,
    Holding,
    LedgerBaselinePositionInput,
)
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.decision_repository import (
    append_portfolio_ledger_event,
    list_portfolio_ledger_events,
)
from app.services.portfolio_ledger import (
    create_transaction_event,
    create_user_confirmed_baseline,
)
from app.services.portfolio_ledger_service import (
    PositionTruthStoreUnavailable,
    capture_position_snapshot,
    confirm_portfolio_ledger_baseline,
    ensure_primary_position_store,
    get_portfolio_ledger_baseline_status,
)


def _seed_profile() -> None:
    save_fund_profile(
        FundProfile(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=1_000,
            settled_holding_amount=1_000,
            holding_shares=500,
            holding_cost=1.8,
            holding_profit=100,
            shares_baseline_date="2026-06-30",
        )
    )


def _request(*, cost: float | None = 900, cash: float | None = 88) -> ConfirmPortfolioLedgerBaselineRequest:
    return ConfirmPortfolioLedgerBaselineRequest(
        as_of_date=date.today(),
        cash_balance_yuan=cash,
        positions=[
            LedgerBaselinePositionInput(
                fund_code="008586",
                confirmed_shares=480.25,
                cost_basis_total_yuan=cost,
            )
        ],
    )


def test_confirmed_baseline_promotes_truth_and_is_idempotent() -> None:
    _seed_profile()

    before = get_portfolio_ledger_baseline_status()
    assert before["status"] == "estimated"
    assert before["positions"][0]["shares_quality"] == "estimated_legacy"

    first = confirm_portfolio_ledger_baseline(_request())
    rows_after_first = list_portfolio_ledger_events(user_id=1)
    second = confirm_portfolio_ledger_baseline(_request())
    rows_after_second = list_portfolio_ledger_events(user_id=1)

    assert first["status"] == "confirmed"
    assert first["position_complete"] is True
    assert first["positions"][0]["settled_shares"] == "480.25"
    assert first["positions"][0]["shares_quality"] == "user_confirmed"
    assert first["positions"][0]["cost_quality"] == "user_confirmed"
    assert first["cash"] == {
        "balance_cny": "88",
        "status": "known",
        "quality": "user_confirmed",
    }
    assert second["ledger_version"] == first["ledger_version"]
    assert len(rows_after_first) == 2
    assert len(rows_after_second) == 2

    profile = get_fund_profile_by_code("008586")
    assert profile is not None
    assert profile.holding_shares == 480.25
    assert profile.holding_cost == round(900 / 480.25, 8)


def test_omitted_cost_and_cash_remain_unknown_instead_of_zero_or_estimated() -> None:
    _seed_profile()

    status = confirm_portfolio_ledger_baseline(_request(cost=None, cash=None))

    assert status["status"] == "confirmed"
    assert status["positions"][0]["cost_basis_total_cny"] is None
    assert status["positions"][0]["cost_quality"] == "unknown"
    assert status["cash"]["balance_cny"] is None
    assert status["cash"]["status"] == "unknown"
    profile = get_fund_profile_by_code("008586")
    assert profile is not None
    assert profile.holding_cost is None


def test_future_baseline_is_rejected_without_writing_events() -> None:
    _seed_profile()
    tomorrow = date.fromordinal(date.today().toordinal() + 1)

    request = _request().model_copy(update={"as_of_date": tomorrow})
    try:
        confirm_portfolio_ledger_baseline(request)
    except ValueError as exc:
        assert "不能晚于今天" in str(exc)
    else:  # pragma: no cover - explicit assertion keeps this readable
        raise AssertionError("future baseline should be rejected")

    assert list_portfolio_ledger_events(user_id=1) == []


def test_position_truth_write_fails_closed_on_non_authoritative_store(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.portfolio_ledger_service._decision_store_authority",
        lambda _connection: "fallback_non_audited",
    )
    try:
        ensure_primary_position_store(object())
    except PositionTruthStoreUnavailable as exc:
        assert "未写入" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("fallback stores must never confirm position truth")


def test_ledger_baseline_http_contract(client: TestClient) -> None:
    user_id = int(client.get("/api/auth/me").json()["id"])
    token = set_request_user_id(user_id)
    try:
        _seed_profile()
    finally:
        reset_request_user_id(token)

    before = client.get("/api/portfolio/ledger-baseline")
    assert before.status_code == 200
    assert before.json()["status"] == "estimated"

    confirmed = client.put(
        "/api/portfolio/ledger-baseline",
        json={
            "as_of_date": date.today().isoformat(),
            "cash_balance_yuan": None,
            "positions": [
                {
                    "fund_code": "008586",
                    "confirmed_shares": 480.25,
                    "cost_basis_total_yuan": None,
                }
            ],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["status"] == "confirmed"
    assert payload["positions"][0]["shares_quality"] == "user_confirmed"
    assert payload["cash"]["status"] == "unknown"


def test_snapshot_does_not_double_count_profile_baseline_and_persisted_transaction() -> None:
    save_fund_profile(
        FundProfile(
            fund_code="008586",
            fund_name="华夏人工智能ETF联接C",
            holding_amount=110,
            holding_shares=100,
            holding_cost=1,
            shares_baseline_date="2026-07-01",
        )
    )
    append_portfolio_ledger_event(
        user_id=1,
        event=create_transaction_event(
            event_id="tx-buy-10",
            fund_code="008586",
            direction="buy",
            status="confirmed",
            effective_at="2026-07-10",
            recorded_at="2026-07-10T08:00:00+08:00",
            confirmed_shares=10,
            gross_amount=10,
            fee_yuan=0,
            source_ref="tx-buy-10",
        ),
    )
    holding = Holding(
        fund_code="008586",
        fund_name="华夏人工智能ETF联接C",
        holding_amount=110,
    )

    current = capture_position_snapshot(
        [holding],
        position_as_of="2026-07-10",
        captured_at="2026-07-11T08:00:00+08:00",
        authoritative=True,
        source="test",
        legacy_recorded_at="2026-07-01T08:00:00+08:00",
    )
    before_trade = capture_position_snapshot(
        [holding],
        position_as_of="2026-07-05",
        captured_at="2026-07-11T08:00:00+08:00",
        authoritative=True,
        source="test",
        legacy_recorded_at="2026-07-01T08:00:00+08:00",
    )

    assert current["positions"][0]["settled_shares"] == "110"
    assert before_trade["positions"][0]["settled_shares"] == "100"


def test_authoritative_empty_portfolio_is_complete_but_ghost_ledger_is_not() -> None:
    empty = capture_position_snapshot(
        [],
        position_as_of="2026-07-10",
        captured_at="2026-07-11T08:00:00+08:00",
        authoritative=True,
        source="test-empty",
    )

    assert empty["positions"] == []
    assert empty["position_complete"] is True
    assert empty["completeness"]["settled_position_complete"] is True
    assert empty["completeness"]["decision_position_complete"] is True

    append_portfolio_ledger_event(
        user_id=1,
        event=create_user_confirmed_baseline(
            fund_code="008586",
            confirmed_shares=100,
            cost_basis_total=100,
            effective_at="2026-07-01",
            recorded_at="2026-07-01T08:00:00+08:00",
            source_ref="ghost-baseline",
        ),
    )
    ghost = capture_position_snapshot(
        [],
        position_as_of="2026-07-10",
        captured_at="2026-07-11T08:00:00+08:00",
        authoritative=True,
        source="test-ghost",
    )

    assert ghost["position_complete"] is False
    assert ghost["completeness"]["decision_position_complete"] is False
    assert ghost["completeness"]["portfolio_code_mismatch"] == ["008586"]


def test_truncated_ledger_window_fails_closed(monkeypatch) -> None:
    from app.services import portfolio_ledger_service as service

    class TruncatedRows(list):
        def __len__(self) -> int:
            return 10_001

    observed: dict[str, int] = {}

    def fake_list_events(**kwargs):
        observed["limit"] = int(kwargs["limit"])
        return TruncatedRows()

    monkeypatch.setattr(service, "list_portfolio_ledger_events", fake_list_events)

    snapshot = service.capture_position_snapshot(
        [],
        position_as_of="2026-07-10",
        captured_at="2026-07-11T08:00:00+08:00",
        authoritative=True,
        source="test-truncated",
    )

    assert observed["limit"] == 10_001
    assert snapshot["ledger_truncated"] is True
    assert snapshot["ledger_event_count_lower_bound"] == 10_001
    assert snapshot["position_complete"] is False
    assert snapshot["completeness"]["ledger_truncated"] is True
    assert snapshot["completeness"]["settled_position_complete"] is False
