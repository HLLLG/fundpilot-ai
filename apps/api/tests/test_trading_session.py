from datetime import datetime

from zoneinfo import ZoneInfo

from app.services.trading_session import build_trading_session, get_effective_trade_date

CN = ZoneInfo("Asia/Shanghai")


def test_build_trading_session_weekend():
    session = build_trading_session(datetime(2026, 6, 6, 14, 45, tzinfo=CN))
    assert session["is_trading_day"] is False
    assert session["session_kind"] == "non_trading_day"
    assert session["effective_trade_date"] == "2026-06-05"


def test_get_effective_trade_date_on_trading_day():
    assert (
        get_effective_trade_date(
            session_kind="trading_day_intraday",
            today=datetime(2026, 6, 2, tzinfo=CN).date(),
        )
        == "2026-06-02"
    )


def test_get_effective_trade_date_on_weekend():
    assert (
        get_effective_trade_date(
            session_kind="non_trading_day",
            today=datetime(2026, 6, 7, tzinfo=CN).date(),
        )
        == "2026-06-05"
    )


def test_build_trading_session_pre_close_window():
    session = build_trading_session(datetime(2026, 6, 2, 14, 45, tzinfo=CN))
    assert session["session_kind"] == "trading_day_pre_close"
    assert session["minutes_to_close"] == 15


def test_build_trading_session_pre_open_uses_previous_trade_date():
    session = build_trading_session(datetime(2026, 6, 10, 8, 15, tzinfo=CN))
    assert session["is_trading_day"] is True
    assert session["session_kind"] == "trading_day_pre_open"
    assert session["effective_trade_date"] == "2026-06-09"
    assert session["minutes_to_close"] is None


def test_get_effective_trade_date_before_market_open():
    assert (
        get_effective_trade_date(
            session_kind="trading_day_pre_open",
            today=datetime(2026, 6, 10, tzinfo=CN).date(),
        )
        == "2026-06-09"
    )
