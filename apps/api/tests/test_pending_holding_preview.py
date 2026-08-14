from __future__ import annotations

from app.models import FundTransaction
from app.services.pending_holding_preview import overlay_pending_transaction_previews


def _tx(
    *,
    fund_code: str,
    fund_name: str,
    amount: float,
    direction: str = "buy",
    in_progress: bool = True,
) -> FundTransaction:
    return FundTransaction(
        id=f"tx-{fund_code}-{amount}",
        fund_code=fund_code,
        fund_name=fund_name,
        direction=direction,
        amount_yuan=amount,
        trade_time="2026-08-14 14:47:49",
        confirm_date="2026-08-14",
        status="pending",
        shares_delta=None,
        in_progress=in_progress,
        dedup_key=f"key-{fund_code}-{amount}",
        created_at="2026-08-14T11:07:59+00:00",
    )


def test_overlay_annotates_existing_holding_without_changing_settled_amount(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.pending_holding_preview.try_get_request_user_id",
        lambda: 1,
    )
    monkeypatch.setattr(
        "app.database.list_pending_fund_transactions",
        lambda: [
            _tx(fund_code="011036", fund_name="嘉实中证稀土产业ETF联接C", amount=300),
            _tx(fund_code="011036", fund_name="嘉实中证稀土产业ETF联接C", amount=200),
        ],
    )

    overlaid = overlay_pending_transaction_previews(
        [
            {
                "fund_code": "011036",
                "fund_name": "嘉实中证稀土产业ETF联接C",
                "holding_amount": 1300.0,
                "settled_holding_amount": 1300.0,
                "daily_profit": 32.58,
            }
        ]
    )

    assert len(overlaid) == 1
    row = overlaid[0]
    assert row["holding_amount"] == 1300.0
    assert row["settled_holding_amount"] == 1300.0
    assert row["daily_profit"] == 32.58
    assert row["pending_buy_amount"] == 500.0
    assert row["pending_transaction_count"] == 2
    assert row["has_in_progress_transactions"] is True


def test_overlay_prepends_pending_only_fund(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.pending_holding_preview.try_get_request_user_id",
        lambda: 1,
    )
    monkeypatch.setattr(
        "app.database.list_pending_fund_transactions",
        lambda: [_tx(fund_code="021959", fund_name="南方黄金股C", amount=500)],
    )

    overlaid = overlay_pending_transaction_previews(
        [
            {
                "fund_code": "011036",
                "fund_name": "嘉实中证稀土产业ETF联接C",
                "holding_amount": 1300.0,
            }
        ]
    )

    assert [row["fund_code"] for row in overlaid] == ["021959", "011036"]
    preview = overlaid[0]
    assert preview["holding_amount"] == 0.0
    assert preview["pending_buy_amount"] == 500.0
    assert preview["unsettled_preview"] is True
    assert preview["daily_profit"] is None
    assert preview["profit_accrual_deferred"] is True


def test_overlay_skips_without_request_user(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.pending_holding_preview.try_get_request_user_id",
        lambda: None,
    )
    called = {"value": False}

    def fail_if_called() -> list:
        called["value"] = True
        raise AssertionError("should not read transactions without a user")

    monkeypatch.setattr("app.database.list_pending_fund_transactions", fail_if_called)

    original = [{"fund_code": "011036", "holding_amount": 100}]
    assert overlay_pending_transaction_previews(original) == original
    assert called["value"] is False
