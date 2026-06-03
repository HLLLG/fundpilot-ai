from app.services.trade_calendar_cache import get_trade_date_set


def test_get_trade_date_set_uses_disk_cache(tmp_path, monkeypatch):
    get_trade_date_set.cache_clear()
    cache_file = tmp_path / "trade_dates.json"
    cache_file.write_text(
        '{"fetched_at": "2026-06-02", "dates": ["2026-06-02", "2026-06-03"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "app.services.trade_calendar_cache.get_settings",
        lambda: type("S", (), {"db_path": tmp_path / "app.db"})(),
    )

    dates = get_trade_date_set()
    assert dates is not None
    assert "2026-06-02" in dates


def test_trading_session_does_not_import_akshare_in_process(monkeypatch):
    from datetime import datetime

    from zoneinfo import ZoneInfo

    from app.services import trading_session

    cn = ZoneInfo("Asia/Shanghai")
    monkeypatch.setattr(
        trading_session,
        "get_trade_date_set",
        lambda: frozenset({"2026-06-02"}),
    )
    session = trading_session.build_trading_session(datetime(2026, 6, 2, 10, 0, tzinfo=cn))
    assert session["is_trading_day"] is True
