"""收盘后分时缓存完整性：盘中截断曲线不可在收盘后继续复用。"""

from __future__ import annotations

from app.services.sector_intraday_provider import (
    _is_complete_closed_intraday,
    fetch_sector_intraday,
)
from app.services.sector_quote_cache import save_spot_snapshot


def _partial_points() -> list[dict]:
    points: list[dict] = []
    for hour, start, end in ((9, 30, 60), (10, 0, 60), (11, 0, 30), (13, 0, 60), (14, 0, 48)):
        for minute in range(start, end):
            points.append({"time": f"{hour:02d}:{minute:02d}", "percent": 0.1})
    points.append({"time": "14:47", "percent": -1.4189})
    return points


def test_is_complete_closed_intraday_requires_close_point():
    partial = _partial_points()
    complete = partial + [{"time": "15:00", "percent": -1.28}]

    assert _is_complete_closed_intraday(partial) is False
    assert _is_complete_closed_intraday(complete) is True


def test_fetch_sector_intraday_refetches_incomplete_closed_cache(monkeypatch):
    trade_date = "2026-06-29"
    cache_key = f"intraday:v3:concept:商业航天:{trade_date}"
    save_spot_snapshot(
        cache_key,
        {
            "points": _partial_points(),
            "note": "盘中",
            "close_change_percent": -1.4189,
        },
    )

    full_points = _partial_points() + [
        {"time": "14:48", "percent": -1.4},
        {"time": "15:00", "percent": -1.28},
    ]

    monkeypatch.setattr(
        "app.services.sector_intraday_provider.build_trading_session",
        lambda: {"session_kind": "trading_day_after_close"},
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.get_effective_trade_date",
        lambda **kwargs: trade_date,
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider._load_intraday_from_network",
        lambda *args, **kwargs: (full_points, "展示收盘分时", trade_date, -1.28),
    )

    points, note, session_date, close = fetch_sector_intraday("concept", "商业航天")

    assert session_date == trade_date
    assert points[-1]["time"] == "15:00"
    assert close == -1.28
    assert note == "展示收盘分时"
