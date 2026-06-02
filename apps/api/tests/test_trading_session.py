from datetime import datetime

from zoneinfo import ZoneInfo

from app.services.trading_session import build_trading_session

CN = ZoneInfo("Asia/Shanghai")


def test_build_trading_session_weekend():
    session = build_trading_session(datetime(2026, 6, 6, 14, 45, tzinfo=CN))
    assert session["is_trading_day"] is False
    assert session["session_kind"] == "non_trading_day"


def test_build_trading_session_pre_close_window():
    session = build_trading_session(datetime(2026, 6, 2, 14, 45, tzinfo=CN))
    assert session["session_kind"] == "trading_day_pre_close"
    assert session["minutes_to_close"] == 15
