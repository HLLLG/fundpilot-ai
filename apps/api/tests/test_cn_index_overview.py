from app.services.cn_index_overview import (
    CN_INDEX_SPECS,
    assemble_cn_index_items,
    _change_from_price_and_percent,
    _overlay_live_session,
    _ttl_for,
)


def test_assemble_uses_fetched_quotes_in_fixed_order() -> None:
    fetched = {
        "1.000001": {
            "latest_price": 3927.18,
            "change_amount": 0.22,
            "change_percent": 0.01,
            "quote_timestamp": 1784621497,
        },
        "0.399001": {
            "latest_price": 12880.12,
            "change_percent": 0.10,
        },
    }

    items = assemble_cn_index_items(fetched)

    assert [item.display_name for item in items] == [name for _s, _id, name in CN_INDEX_SPECS]
    assert items[0].status == "ok"
    assert items[0].last_price == 3927.18
    assert items[0].change == 0.22
    assert items[1].status == "ok"
    assert items[1].change == _change_from_price_and_percent(12880.12, 0.10)
    assert all(item.status == "unavailable" for item in items[2:])


def test_assemble_reuses_previous_real_values_instead_of_inventing() -> None:
    prev = [
        {
            "symbol": "000001",
            "display_name": "上证指数",
            "last_price": 3900.0,
            "change": -8.5,
            "change_percent": -0.22,
            "quote_time": "2026-06-16T15:00:00+08:00",
        }
    ]

    items = assemble_cn_index_items({}, prev)

    assert items[0].status == "stale"
    assert items[0].last_price == 3900.0
    assert items[0].change == -8.5
    assert items[1].status == "unavailable"
    assert items[1].last_price is None
    assert items[1].change_percent is None


def test_assemble_empty_fetch_without_cache_stays_unavailable() -> None:
    items = assemble_cn_index_items(None)

    assert len(items) == len(CN_INDEX_SPECS)
    assert all(item.status == "unavailable" for item in items)
    assert all(item.last_price is None for item in items)
    assert all(item.change_percent is None for item in items)


def test_change_from_price_and_percent_matches_yangjibao_style_points() -> None:
    assert _change_from_price_and_percent(100.0, 1.0) == 0.9901
    assert _change_from_price_and_percent(None, 1.0) is None


def test_ttl_matches_shared_refresh_cadence() -> None:
    assert _ttl_for("trading_day_intraday") == 1200.0
    assert _ttl_for("trading_day_pre_close") == 1200.0
    assert _ttl_for("closed") == 10800.0


def test_overlay_live_session_keeps_quotes_but_uses_current_calendar() -> None:
    overview = _overlay_live_session(
        {
            "items": [],
            "available": True,
            "updated_at": "2026-08-14T15:00:00+08:00",
            "trade_date": "2026-08-14",
            "session_kind": "trading_day_after_close",
        },
        trade_date="2026-08-14",
        session_kind="non_trading_day",
        from_cache=True,
        stale=False,
    )
    assert overview.session_kind == "non_trading_day"
    assert overview.trade_date == "2026-08-14"
    assert overview.from_cache is True
