"""成交时间 → 确认净值日 / 首次计收益日。"""
from __future__ import annotations

from app.services.trading_session import resolve_confirm_date, resolve_first_return_date


def test_buy_before_close_confirms_same_trading_day() -> None:
    assert resolve_confirm_date("2026-06-10 14:59:59") == "2026-06-10"
    assert resolve_first_return_date("2026-06-10 14:59:59") == "2026-06-11"


def test_buy_at_or_after_close_confirms_next_trading_day() -> None:
    assert resolve_confirm_date("2026-06-10 15:00:00") == "2026-06-11"
    assert resolve_first_return_date("2026-06-10 15:00:00") == "2026-06-12"


def test_weekend_buy_confirms_next_trading_day() -> None:
    # 2026-06-06 周六不在交易日历里，确认日跳到下周一 06-08，首次计收益 06-09。
    assert resolve_confirm_date("2026-06-06 10:00:00") == "2026-06-08"
    assert resolve_first_return_date("2026-06-06 10:00:00") == "2026-06-09"


def test_sell_uses_the_same_cutoff_as_buy() -> None:
    assert resolve_confirm_date("2026-06-10 14:30:00") == "2026-06-10"
    assert resolve_confirm_date("2026-06-10 15:01:00") == "2026-06-11"
    assert resolve_first_return_date("2026-06-10 15:01:00") == "2026-06-12"
