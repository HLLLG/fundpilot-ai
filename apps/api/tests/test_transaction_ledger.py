"""Part D 账本单测：单位净值取数、确认换算、有效份额、apply 编排。

依赖 conftest 的 autouse fixtures：强制 SQLite tmp db、默认用户 1、stub 交易日历与
净值子进程。net值统一用 monkeypatch 固定，避免真实网络。
"""

from __future__ import annotations

from uuid import uuid4

import pandas as pd

from app.database import (
    get_fund_profile_by_code,
    insert_fund_transaction,
    list_fund_transactions,
    save_fund_profile,
)
from app.models import FundProfile, FundTransaction, ParsedTransaction
from app.services import fund_nav_service, holding_amount_sync, transaction_ledger
from app.services.transaction_ledger import (
    apply_parsed_transactions,
    compute_effective_shares_map,
    confirm_pending_transactions,
)


def _nav_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "净值日期": ["2026-06-08", "2026-06-09", "2026-06-10"],
            "单位净值": [1.9, 2.0, 2.1],
            "日增长率": [0.5, 0.6, 0.7],
        }
    )


# --- get_unit_nav_on_date -----------------------------------------------------


def test_get_unit_nav_on_date_hits_exact_date(monkeypatch):
    fund_nav_service._UNIT_NAV_CACHE.clear()
    monkeypatch.setattr(fund_nav_service, "_fetch_nav_df", lambda _code: _nav_df())
    assert fund_nav_service.get_unit_nav_on_date("110011", "2026-06-09") == 2.0


def test_get_unit_nav_on_date_returns_none_when_not_published(monkeypatch):
    fund_nav_service._UNIT_NAV_CACHE.clear()
    monkeypatch.setattr(fund_nav_service, "_fetch_nav_df", lambda _code: _nav_df())
    assert fund_nav_service.get_unit_nav_on_date("110011", "2026-06-11") is None


# --- confirm_pending_transactions --------------------------------------------


def _pending_tx(**overrides) -> FundTransaction:
    base = dict(
        id=uuid4().hex,
        fund_code="110011",
        fund_name="测试基金",
        direction="buy",
        amount_yuan=1500.0,
        trade_time="2026-06-09 10:00:00",
        confirm_date="2026-06-09",
        status="pending",
        dedup_key=uuid4().hex,
        created_at="2026-06-09T10:00:00+00:00",
    )
    base.update(overrides)
    return FundTransaction(**base)


def test_confirm_pending_buy_sell_and_unpublished(monkeypatch):
    navs = {
        ("110011", "2026-06-09"): 2.0,
        ("220022", "2026-06-09"): 1.5,
    }
    monkeypatch.setattr(
        transaction_ledger, "get_unit_nav_on_date", lambda code, day: navs.get((code, day))
    )

    insert_fund_transaction(_pending_tx(fund_code="110011", direction="buy", amount_yuan=1500.0))
    insert_fund_transaction(_pending_tx(fund_code="220022", direction="sell", amount_yuan=2336.61))
    insert_fund_transaction(_pending_tx(fund_code="330033", direction="buy", amount_yuan=1000.0))

    confirmed = confirm_pending_transactions()
    assert confirmed == 2

    by_code = {tx.fund_code: tx for tx in list_fund_transactions()}
    assert by_code["110011"].status == "confirmed"
    assert by_code["110011"].shares_delta == 750.0
    assert by_code["110011"].nav_on_confirm == 2.0

    assert by_code["220022"].status == "confirmed"
    assert by_code["220022"].shares_delta == -1557.74

    # 净值不可得 → 保持 pending、不填 shares_delta
    assert by_code["330033"].status == "pending"
    assert by_code["330033"].shares_delta is None


# --- compute_effective_shares_map --------------------------------------------


def _confirmed_tx(code: str, confirm_date: str, delta: float) -> FundTransaction:
    return FundTransaction(
        id=uuid4().hex,
        fund_code=code,
        fund_name="测试基金",
        direction="buy" if delta >= 0 else "sell",
        amount_yuan=abs(delta),
        trade_time=f"{confirm_date} 10:00:00",
        confirm_date=confirm_date,
        status="confirmed",
        shares_delta=delta,
        nav_on_confirm=1.0,
        dedup_key=uuid4().hex,
        created_at="2026-06-10T10:00:00+00:00",
    )


def test_compute_effective_adds_only_after_baseline():
    save_fund_profile(
        FundProfile(
            fund_code="110011",
            fund_name="测试基金",
            holding_amount=2000.0,
            holding_shares=1000.0,
            shares_baseline_date="2026-06-01",
        )
    )
    insert_fund_transaction(_confirmed_tx("110011", "2026-06-10", 750.0))
    # 早于（含等于）基线日 → 视为已含在基线内，不叠加
    insert_fund_transaction(_confirmed_tx("110011", "2026-05-20", 500.0))

    result = compute_effective_shares_map(["110011"])
    assert result["110011"] == 1750.0


def test_compute_effective_clearance_non_positive():
    save_fund_profile(
        FundProfile(
            fund_code="220022",
            fund_name="清仓基金",
            holding_amount=2000.0,
            holding_shares=1000.0,
            shares_baseline_date="2026-06-01",
        )
    )
    insert_fund_transaction(_confirmed_tx("220022", "2026-06-10", -1000.0))
    result = compute_effective_shares_map(["220022"])
    assert result["220022"] <= 0


def test_compute_effective_skips_profiles_without_baseline_shares():
    save_fund_profile(
        FundProfile(fund_code="330033", fund_name="无份额", holding_amount=1000.0)
    )
    assert compute_effective_shares_map(["330033"]) == {}


# --- apply_parsed_transactions -----------------------------------------------


def _stub_apply_env(monkeypatch, nav: float = 2.0) -> None:
    monkeypatch.setattr(transaction_ledger, "get_unit_nav_on_date", lambda _c, _d: nav)
    monkeypatch.setattr(holding_amount_sync, "fetch_fund_estimate_quotes", lambda *_a, **_k: {})
    monkeypatch.setattr(holding_amount_sync, "get_latest_unit_nav", lambda _c: nav)
    monkeypatch.setattr(holding_amount_sync, "get_official_nav_return", lambda _c, _d: None)


def _parsed_buy(code: str | None = "110011", **over) -> ParsedTransaction:
    base = dict(
        direction="buy",
        fund_name="测试基金",
        fund_code=code,
        amount_yuan=1500.0,
        trade_time="2026-06-09 10:00:00",
        confirm_date="2026-06-09",
    )
    base.update(over)
    return ParsedTransaction(**base)


def test_apply_dedup_second_time_skipped(monkeypatch):
    _stub_apply_env(monkeypatch)
    parsed = _parsed_buy()
    first = apply_parsed_transactions([parsed])
    assert first["inserted"] == 1
    assert first["skipped"] == 0

    second = apply_parsed_transactions([parsed])
    assert second["inserted"] == 0
    assert second["skipped"] == 1


def test_apply_creates_provisional_profile_and_confirms(monkeypatch):
    _stub_apply_env(monkeypatch)
    result = apply_parsed_transactions([_parsed_buy(code="330033", fund_name="新基金")])

    assert result["inserted"] == 1
    assert result["pending"] == 0
    assert set(result) == {"holdings", "inserted", "skipped", "pending"}

    profile = get_fund_profile_by_code("330033")
    assert profile is not None
    assert profile.is_provisional is True
    # 建仓基线份额 0 + delta(1500 / 2.0 = 750) = 750
    assert compute_effective_shares_map(["330033"])["330033"] == 750.0


def test_apply_skips_when_fund_code_missing(monkeypatch):
    _stub_apply_env(monkeypatch)
    result = apply_parsed_transactions([_parsed_buy(code=None, fund_name="未匹配")])
    assert result["inserted"] == 0
    assert result["skipped"] == 1


def test_apply_new_position_visible_with_seeded_amount(monkeypatch):
    """买入全新基金建仓后，应进入持仓列表且金额 = 有效份额 × 最新净值（随后由 sync override 精确化）。"""
    _stub_apply_env(monkeypatch, nav=2.0)
    monkeypatch.setattr(transaction_ledger, "get_latest_unit_nav", lambda _c: 2.0)

    result = apply_parsed_transactions(
        [_parsed_buy(code="330033", fund_name="华夏成长混合")]
    )

    assert result["inserted"] == 1
    match = next(
        (h for h in result["holdings"] if h.get("fund_code") == "330033"), None
    )
    assert match is not None, "建仓的新基金应出现在持仓列表中"
    # 1500 / 2.0 = 750 份额 × 2.0 净值 = 1500.0
    assert match["holding_amount"] == 1500.0
