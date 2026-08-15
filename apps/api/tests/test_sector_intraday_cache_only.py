from __future__ import annotations

from datetime import datetime

from app.services.sector_intraday_provider import (
    _eastmoney_secid_for_index_symbol,
    _should_fetch_intraday,
    fetch_sector_intraday,
)
from app.services.trading_session import CN_TZ, build_trading_session


def test_intraday_cache_only_skips_network_when_closed(monkeypatch) -> None:
    session = build_trading_session(datetime(2026, 6, 13, 10, 0, tzinfo=CN_TZ))
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.build_trading_session",
        lambda: session,
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.get_spot_snapshot",
        lambda *args, **kwargs: None,
    )

    def _boom(*args, **kwargs):
        raise AssertionError("request path must not fetch Eastmoney")

    monkeypatch.setattr(
        "app.services.sector_intraday_provider._fetch_board_intraday",
        _boom,
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider._fetch_index_intraday",
        _boom,
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.resolve_intraday_source",
        lambda source_type, source_name: (source_type, source_name),
    )

    points, note, _session_date, close = fetch_sector_intraday(
        "concept",
        "人工智能",
        cache_only=True,
        force_refresh=True,
    )
    assert points == []
    assert close is None
    assert note is not None
    assert "休市" in note or "缓存" in note


def test_intraday_weekend_cache_miss_fetches_last_session(monkeypatch) -> None:
    session = build_trading_session(datetime(2026, 8, 15, 17, 0, tzinfo=CN_TZ))
    assert session["session_kind"] == "non_trading_day"
    assert _should_fetch_intraday(session) is True

    monkeypatch.setattr(
        "app.services.sector_intraday_provider.build_trading_session",
        lambda: session,
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.get_effective_trade_date",
        lambda **_kwargs: "2026-08-14",
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.get_spot_snapshot",
        lambda *args, **kwargs: None,
    )
    saved: dict = {}
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.save_spot_snapshot",
        lambda key, payload: saved.update({key: payload}),
    )

    sample = [{"time": "09:31", "percent": -0.2}]
    sample.extend({"time": f"10:{minute:02d}", "percent": -0.5} for minute in range(30))
    sample.append({"time": "15:00", "percent": -1.06})

    monkeypatch.setattr(
        "app.services.sector_intraday_provider._fetch_index_intraday",
        lambda *_args, **_kwargs: sample,
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.resolve_intraday_source",
        lambda source_type, source_name: (source_type, source_name),
    )

    points, note, trade_date, close = fetch_sector_intraday("index", "黄金")
    assert trade_date == "2026-08-14"
    assert len(points) >= 30
    assert points[-1]["time"] == "15:00"
    assert close == -1.06
    assert note is not None
    assert saved


def test_shanghai_etf_secid_prefix() -> None:
    assert _eastmoney_secid_for_index_symbol("518880") == "1.518880"
