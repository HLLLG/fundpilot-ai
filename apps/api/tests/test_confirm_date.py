"""确认日规则单测。

依赖 conftest stub 的交易日历（PYTEST_TRADE_DATES，含 2026-06-02..06-12 工作日，
不含周末 06-06/06-07）。所有 trade_time 均显式落在 stub 覆盖的工作日区间内。
"""

from app.services.trading_session import resolve_confirm_date


def test_before_cutoff_on_trading_day_confirms_same_day():
    # 06-03（周三，交易日）14:21 < 15:00 -> 当天
    assert resolve_confirm_date("2026-06-03 14:21:53") == "2026-06-03"


def test_at_or_after_cutoff_rolls_to_next_trading_day():
    # 06-03 15:00 起算 -> 顺延至下一交易日 06-04
    assert resolve_confirm_date("2026-06-03 15:00:00") == "2026-06-04"
    assert resolve_confirm_date("2026-06-03 16:30:00") == "2026-06-04"


def test_late_friday_rolls_over_weekend_to_monday():
    # 06-05（周五）≥15:00 -> 跳过周末，顺延至 06-08（周一）
    assert resolve_confirm_date("2026-06-05 15:10:00") == "2026-06-08"


def test_weekend_trade_time_rolls_to_next_trading_day():
    # 06-06（周六，非交易日）任意时间 -> 顺延至 06-08（周一）
    assert resolve_confirm_date("2026-06-06 10:00:00") == "2026-06-08"
    assert resolve_confirm_date("2026-06-07 23:59:00") == "2026-06-08"


def test_before_cutoff_friday_confirms_same_day():
    # 06-05（周五）14:00 < 15:00 -> 当天
    assert resolve_confirm_date("2026-06-05 14:00:00") == "2026-06-05"
