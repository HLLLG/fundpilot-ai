"""Part B 交易表 CRUD 契约测试（SQLite，userId 隔离）。"""

from app.database import (
    delete_fund_transaction,
    insert_fund_transaction,
    list_fund_transactions,
    list_pending_fund_transactions,
    update_fund_transaction,
)
from app.models import FundTransaction


def _tx(**overrides) -> FundTransaction:
    base = dict(
        id="tx-1",
        fund_code="161725",
        fund_name="招商中证白酒指数A",
        direction="buy",
        amount_yuan=1500.0,
        trade_time="2026-06-03 14:21:53",
        confirm_date="2026-06-03",
        status="pending",
        dedup_key="161725|buy|2026-06-03 14:21:53|1500.0",
        created_at="2026-06-20T00:00:00+00:00",
    )
    base.update(overrides)
    return FundTransaction(**base)


def test_insert_returns_true_then_false_on_dedup():
    assert insert_fund_transaction(_tx()) is True
    # 同一 dedup_key（即便 id 不同）应被唯一索引忽略。
    assert insert_fund_transaction(_tx(id="tx-1-dup")) is False


def test_list_filters_and_orders():
    insert_fund_transaction(
        _tx(id="a", confirm_date="2026-06-10", dedup_key="k-a")
    )
    insert_fund_transaction(
        _tx(id="b", confirm_date="2026-06-03", dedup_key="k-b")
    )
    insert_fund_transaction(
        _tx(id="c", fund_code="000001", confirm_date="2026-06-05", dedup_key="k-c")
    )

    all_tx = list_fund_transactions()
    assert [tx.id for tx in all_tx] == ["b", "c", "a"]  # confirm_date 升序

    only_161725 = list_fund_transactions(fund_code="161725")
    assert {tx.id for tx in only_161725} == {"a", "b"}


def test_list_pending_and_update():
    insert_fund_transaction(_tx(id="p1", dedup_key="p1"))
    insert_fund_transaction(_tx(id="p2", dedup_key="p2"))

    assert {tx.id for tx in list_pending_fund_transactions()} == {"p1", "p2"}

    update_fund_transaction(
        "p1", status="confirmed", shares_delta=750.0, nav_on_confirm=2.0
    )
    pending = list_pending_fund_transactions()
    assert {tx.id for tx in pending} == {"p2"}

    confirmed = next(tx for tx in list_fund_transactions() if tx.id == "p1")
    assert confirmed.status == "confirmed"
    assert confirmed.shares_delta == 750.0
    assert confirmed.nav_on_confirm == 2.0


def test_delete_removes_row():
    insert_fund_transaction(_tx(id="d1", dedup_key="d1"))
    delete_fund_transaction("d1")
    assert all(tx.id != "d1" for tx in list_fund_transactions())
