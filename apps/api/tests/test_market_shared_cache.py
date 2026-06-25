"""共享市场快照：全用户读同一份服务端缓存。"""

from app.services.market_shared_refresh import (
    _A_SHARE_LIVE_SESSIONS,
    _US_LIVE_SESSIONS,
    _idle_interval_seconds,
    _live_interval_seconds,
)
from app.services.dip_radar_snapshot import get_dip_radar_snapshot
from app.services.sector_board_snapshot import get_sector_board_snapshot
from app.services.sector_quote_cache import save_spot_snapshot
from app.services.theme_board_snapshot import (
    _MARKET_REFRESH_SESSIONS,
    get_theme_board_snapshot,
)


def test_market_refresh_sessions_exclude_after_close():
    assert "trading_day_after_close" not in _MARKET_REFRESH_SESSIONS
    assert "non_trading_day" not in _MARKET_REFRESH_SESSIONS
    assert "trading_day_intraday" in _MARKET_REFRESH_SESSIONS


def test_a_share_and_us_live_sessions_distinct():
    assert "trading_day_intraday" in _A_SHARE_LIVE_SESSIONS
    assert "regular" in _US_LIVE_SESSIONS
    assert "after_hours" in _US_LIVE_SESSIONS
    assert "closed" not in _US_LIVE_SESSIONS


def test_market_shared_interval_defaults():
    live = _live_interval_seconds()
    idle = _idle_interval_seconds()
    assert live >= 60
    assert idle >= live
    assert idle >= 300


def test_sector_board_serves_stale_without_network(monkeypatch):
    cache_key = "market:sector_boards:v2:2026-06-25"
    payload = {
        "trade_date": "2026-06-25",
        "session_kind": "trading_day_intraday",
        "industry": [{"name": "半导体", "code": "BK1036", "change_percent": 1.2, "main_force_net_yi": 1.0}],
        "concept": [],
    }
    save_spot_snapshot(cache_key, payload)

    def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("network fetch should not run when stale cache exists")

    monkeypatch.setattr(
        "app.services.sector_board_snapshot.build_trading_session",
        lambda: {
            "effective_trade_date": "2026-06-25",
            "session_kind": "trading_day_intraday",
        },
    )
    monkeypatch.setattr(
        "app.services.sector_board_snapshot._fetch_all_board_records_parallel",
        _should_not_fetch,
    )

    monkeypatch.setattr(
        "app.services.sector_board_snapshot.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )

    result = get_sector_board_snapshot(force_refresh=False)
    assert result["from_cache"] is True
    assert result["stale"] is True
    assert result["industry"][0]["name"] == "半导体"


def test_theme_boards_read_from_cache_without_refresh(monkeypatch):
    trade_date = "2026-06-25"
    cache_key = f"theme:boards:v3:{trade_date}"
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
            "refreshed_at": "2026-06-25T08:00:00+00:00",
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
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.apply_flow_to_items",
        lambda items: items,
    )

    payload = get_theme_board_snapshot(force_refresh=False, holdings=[], sort="change")
    assert payload["from_cache"] is True
    assert payload["items"][0]["sector_label"] == "半导体"


def test_dip_radar_serves_stale_without_network(monkeypatch):
    trade_date = "2026-06-25"
    cache_key = f"dip:radar:v1:{trade_date}:5"
    save_spot_snapshot(
        cache_key,
        {
            "trade_date": trade_date,
            "lookback_days": 5,
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
