"""共享市场快照：全用户读同一份服务端缓存。"""

from datetime import datetime, timedelta, timezone

from app.services.market_shared_refresh import (    _A_SHARE_LIVE_SESSIONS,
    _US_LIVE_SESSIONS,
    _idle_interval_seconds,
    _live_interval_seconds,
    _poll_seconds,
)
from app.services.dip_radar_snapshot import get_dip_radar_snapshot
from app.services.sector_quote_cache import (
    mark_process_boot,
    save_spot_snapshot,
    snapshot_refreshed_before_process_boot,
)
from app.services.theme_board_snapshot import (
    get_theme_board_snapshot,
)


def test_a_share_live_sessions_for_refresh():
    assert "trading_day_intraday" in _A_SHARE_LIVE_SESSIONS
    assert "trading_day_pre_close" in _A_SHARE_LIVE_SESSIONS
    assert "trading_day_after_close" not in _A_SHARE_LIVE_SESSIONS


def test_a_share_and_us_live_sessions_distinct():
    assert "trading_day_intraday" in _A_SHARE_LIVE_SESSIONS
    assert "regular" in _US_LIVE_SESSIONS
    assert "after_hours" in _US_LIVE_SESSIONS
    assert "closed" not in _US_LIVE_SESSIONS


def test_market_shared_interval_defaults():
    live = _live_interval_seconds()
    idle = _idle_interval_seconds()
    poll = _poll_seconds()
    assert live >= 60
    assert idle >= live
    assert idle >= 300
    assert poll <= live
    assert poll <= 60.0


def test_snapshot_refreshed_before_process_boot():
    boot = mark_process_boot()
    assert snapshot_refreshed_before_process_boot(None) is True
    assert snapshot_refreshed_before_process_boot(
        (boot.replace(microsecond=0) - timedelta(seconds=1)).isoformat()
    ) is True
    assert snapshot_refreshed_before_process_boot(
        datetime.now(timezone.utc).isoformat()
    ) is False


def test_theme_board_refreshes_cache_from_prior_process(monkeypatch):
    mark_process_boot()
    trade_date = "2026-06-25"
    cache_key = f"theme:boards:v5:{trade_date}"
    save_spot_snapshot(
        cache_key,
        {
            "items": [{"sector_label": "旧数据", "change_1d_percent": 0.1}],
            "trade_date": trade_date,
            "session_kind": "trading_day_intraday",
            "refreshed_at": "2020-01-01T00:00:00+00:00",
        },
    )
    refreshed = {
        "items": [{"sector_label": "新数据", "change_1d_percent": 9.9}],
        "trade_date": trade_date,
        "session_kind": "trading_day_intraday",
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }

    monkeypatch.setattr(
        "app.services.theme_board_snapshot.build_trading_session",
        lambda: {
            "effective_trade_date": trade_date,
            "session_kind": "trading_day_intraday",
        },
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.refresh_theme_board_snapshot",
        lambda **_: refreshed,
    )

    payload = get_theme_board_snapshot(force_refresh=False, holdings=[], sort="change")
    assert payload["from_cache"] is False
    assert payload["items"][0]["sector_label"] == "新数据"


def test_theme_boards_read_from_cache_without_refresh(monkeypatch):
    from app.services.sector_quote_cache import mark_process_boot

    mark_process_boot()
    trade_date = "2026-06-25"
    cache_key = f"theme:boards:v5:{trade_date}"
    save_spot_snapshot(
        cache_key,
        {
            "items": [
                {
                    "sector_label": "半导体",
                    "board_kind": "index",
                    "secid": "2.H30184",
                    "source_code": "H30184",
                    "flow_source_code": "BK1036",
                    "change_1d_percent": 4.14,
                }
            ],
            "trade_date": trade_date,
            "session_kind": "trading_day_after_close",
            "refreshed_at": "2099-01-01T00:00:00+00:00",
        },
    )

    monkeypatch.setattr(
        "app.services.theme_board_snapshot.build_trading_session",
        lambda: {
            "effective_trade_date": trade_date,
            "session_kind": "trading_day_after_close",
        },
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.refresh_theme_board_snapshot",
        lambda **_: (_ for _ in ()).throw(AssertionError("should not sync refresh")),
    )

    payload = get_theme_board_snapshot(force_refresh=False, holdings=[], sort="change")
    assert payload["from_cache"] is True
    assert payload["items"][0]["sector_label"] == "半导体"


def test_dip_radar_serves_stale_without_network(monkeypatch):
    mark_process_boot()
    trade_date = "2026-06-25"
    cache_key = f"dip:radar:v2:{trade_date}:5"
    save_spot_snapshot(
        cache_key,
        {
            "trade_date": trade_date,
            "lookback_days": 5,
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "items": [
                {
                    "fund_code": "000001",
                    "fund_name": "测试基金",
                    "sector_label": "半导体",
                    "dip_drop_percent": -3.5,
                    "rank": 1,
                }
            ],
            "sector_dip_leaders": [],
            "available": True,
            "session_kind": "trading_day_intraday",
        },
    )

    monkeypatch.setattr(
        "app.services.dip_radar_snapshot.build_trading_session",
        lambda: {
            "effective_trade_date": trade_date,
            "session_kind": "trading_day_intraday",
        },
    )
    monkeypatch.setattr(
        "app.services.dip_radar_snapshot.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.dip_radar_snapshot.build_dip_radar_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("should not sync build")),
    )

    result = get_dip_radar_snapshot(lookback_days=5, force_refresh=False)
    assert result["from_cache"] is True
    assert result["stale"] is True
    assert result["items"][0]["fund_name"] == "测试基金"


def test_us_market_serves_stale_without_network(monkeypatch):
    from app.models import UsFuturesQuote, UsMarketSnapshot, UsdCnyQuote
    from app.services.us_market_service import get_us_market_snapshot

    cache_key = "market:us_overview:v8:live:2026-06-25"
    payload = UsMarketSnapshot(
        session_kind="regular",
        session_label="盘中",
        et_date="2026-06-25",
        updated_at="2026-06-25T10:00:00-04:00",
        futures=[
            UsFuturesQuote(
                symbol="NASDAQ_FUT",
                display_name="纳斯达克",
                last_price=19850.5,
                change_percent=0.62,
                quote_time="2026-06-25T10:00:00-04:00",
                status="ok",
            )
        ],
        usd_cny=UsdCnyQuote(
            last_price=6.8096,
            change_percent=-0.02,
            quote_time="2026-06-25",
            status="ok",
        ),
        qdii=[],
        qdii_status="unavailable",
        futures_status="ok",
        forex_status="ok",
        available=True,
        from_cache=False,
        stale=False,
        message=None,
    ).model_dump()
    save_spot_snapshot(cache_key, payload)

    monkeypatch.setattr(
        "app.services.us_market_service.detect_us_session",
        lambda: {"session_kind": "regular", "et_date": "2026-06-25"},
    )
    monkeypatch.setattr(
        "app.services.us_market_service.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.us_market_service.fetch_us_index_futures",
        lambda: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )

    snap = get_us_market_snapshot(force_refresh=False)
    assert snap.from_cache is True
    assert snap.stale is True
    assert snap.available is True
