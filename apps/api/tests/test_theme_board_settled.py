from types import SimpleNamespace

from app.services import market_shared_refresh
from app.services.theme_board_snapshot import theme_board_snapshot_is_settled


def _snapshot(*, trade_date: str, session_kind: str) -> dict:
    return {
        "trade_date": trade_date,
        "session_kind": session_kind,
        "items": [{"sector_label": "半导体", "change_1d_percent": 1.2}],
    }


def test_weekend_close_snapshot_is_settled() -> None:
    session = {
        "session_kind": "non_trading_day",
        "effective_trade_date": "2026-08-14",
    }
    assert (
        theme_board_snapshot_is_settled(
            _snapshot(trade_date="2026-08-14", session_kind="trading_day_after_close"),
            session,
        )
        is True
    )


def test_intraday_snapshot_is_not_settled_after_close() -> None:
    session = {
        "session_kind": "trading_day_after_close",
        "effective_trade_date": "2026-08-14",
    }
    assert (
        theme_board_snapshot_is_settled(
            _snapshot(trade_date="2026-08-14", session_kind="trading_day_intraday"),
            session,
        )
        is False
    )


def test_live_session_is_never_settled() -> None:
    session = {
        "session_kind": "trading_day_intraday",
        "effective_trade_date": "2026-08-17",
    }
    assert (
        theme_board_snapshot_is_settled(
            _snapshot(trade_date="2026-08-17", session_kind="trading_day_intraday"),
            session,
        )
        is False
    )


def test_weekend_settled_theme_board_skips_source_refresh(monkeypatch) -> None:
    called = {"theme": 0, "cn": 0}
    monkeypatch.setattr(
        market_shared_refresh,
        "build_trading_session",
        lambda: {"session_kind": "non_trading_day", "effective_trade_date": "2026-08-14"},
    )
    monkeypatch.setattr(market_shared_refresh, "_theme_board_is_settled", lambda: True)
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.refresh_theme_board_snapshot",
        lambda: called.__setitem__("theme", called["theme"] + 1),
    )
    monkeypatch.setattr(
        "app.services.cn_index_overview.get_cn_index_overview",
        lambda **_kwargs: called.__setitem__("cn", called["cn"] + 1),
    )

    market_shared_refresh.refresh_a_share_market_snapshots()
    assert called["theme"] == 0
    assert called["cn"] == 0


def test_weekend_settled_theme_board_skips_idle_loop_refresh(monkeypatch) -> None:
    called = {"refresh": 0}
    monkeypatch.setattr(
        market_shared_refresh,
        "get_settings",
        lambda: SimpleNamespace(
            theme_board_refresh_enabled=True,
            theme_board_refresh_interval_seconds=1200,
            theme_board_refresh_idle_interval_seconds=10800,
            market_shared_idle_interval_seconds=10800,
        ),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "build_trading_session",
        lambda: {"session_kind": "non_trading_day", "effective_trade_date": "2026-08-14"},
    )
    monkeypatch.setattr(market_shared_refresh, "_theme_board_is_settled", lambda: True)
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_a_share_market_snapshots",
        lambda: called.__setitem__("refresh", called["refresh"] + 1),
    )
    previous = market_shared_refresh._last_a_share_refresh_at
    market_shared_refresh._last_a_share_refresh_at = 9_000.0
    try:
        market_shared_refresh._maybe_refresh_a_share(10_000.0)
        assert called["refresh"] == 0
        market_shared_refresh._maybe_refresh_a_share(30_000.0)
        assert called["refresh"] == 0
    finally:
        market_shared_refresh._last_a_share_refresh_at = previous


def test_live_a_share_session_still_refreshes_cn_index(monkeypatch) -> None:
    called = {"theme": 0, "cn": 0}
    monkeypatch.setattr(
        market_shared_refresh,
        "build_trading_session",
        lambda: {"session_kind": "trading_day_intraday", "effective_trade_date": "2026-08-17"},
    )
    monkeypatch.setattr(market_shared_refresh, "_theme_board_is_settled", lambda: False)
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.refresh_theme_board_snapshot",
        lambda: called.__setitem__("theme", called["theme"] + 1),
    )
    monkeypatch.setattr(
        "app.services.cn_index_overview.get_cn_index_overview",
        lambda **_kwargs: called.__setitem__("cn", called["cn"] + 1),
    )

    market_shared_refresh.refresh_a_share_market_snapshots()
    assert called["theme"] == 1
    assert called["cn"] == 1


def test_startup_can_warm_cn_index_while_closed(monkeypatch) -> None:
    called = {"cn": 0}
    monkeypatch.setattr(
        market_shared_refresh,
        "build_trading_session",
        lambda: {"session_kind": "non_trading_day", "effective_trade_date": "2026-08-14"},
    )
    monkeypatch.setattr(market_shared_refresh, "_theme_board_is_settled", lambda: True)
    monkeypatch.setattr(
        "app.services.cn_index_overview.get_cn_index_overview",
        lambda **_kwargs: called.__setitem__("cn", called["cn"] + 1),
    )

    market_shared_refresh.refresh_a_share_market_snapshots(refresh_cn_index=True)
    assert called["cn"] == 1


def test_closed_us_session_skips_loop_refresh(monkeypatch) -> None:
    called = {"refresh": 0}
    monkeypatch.setattr(
        market_shared_refresh,
        "get_settings",
        lambda: SimpleNamespace(theme_board_refresh_enabled=True),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "detect_us_session",
        lambda: {"session_kind": "closed", "et_date": "2026-08-16"},
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_us_market_snapshot",
        lambda: called.__setitem__("refresh", called["refresh"] + 1),
    )
    previous = market_shared_refresh._last_us_refresh_at
    market_shared_refresh._last_us_refresh_at = 0.0
    try:
        market_shared_refresh._maybe_refresh_us(20_000.0)
        assert called["refresh"] == 0
    finally:
        market_shared_refresh._last_us_refresh_at = previous


def test_live_us_session_refreshes_on_live_interval(monkeypatch) -> None:
    called = {"refresh": 0}
    monkeypatch.setattr(
        market_shared_refresh,
        "get_settings",
        lambda: SimpleNamespace(
            theme_board_refresh_enabled=True,
            theme_board_refresh_interval_seconds=1200,
        ),
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "detect_us_session",
        lambda: {"session_kind": "regular", "et_date": "2026-08-17"},
    )
    monkeypatch.setattr(
        market_shared_refresh,
        "refresh_us_market_snapshot",
        lambda: called.__setitem__("refresh", called["refresh"] + 1),
    )
    previous = market_shared_refresh._last_us_refresh_at
    market_shared_refresh._last_us_refresh_at = 9_000.0
    try:
        market_shared_refresh._maybe_refresh_us(9_600.0)
        assert called["refresh"] == 0
        market_shared_refresh._maybe_refresh_us(10_300.0)
        assert called["refresh"] == 1
    finally:
        market_shared_refresh._last_us_refresh_at = previous
