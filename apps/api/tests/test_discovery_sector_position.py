from __future__ import annotations

from app.services.discovery_sector_position import (
    build_sector_position_map_for_opportunities,
    summarize_sector_position,
    _default_fetch_series_for_label,
)


def _bar(day: int, close: float, volume: float = 100.0) -> dict:
    return {
        "date": f"2026-06-{day:02d}",
        "close": close,
        "volume": volume,
    }


def test_summarize_sector_position_detects_pullback_acceptance():
    closes = [
        100,
        102,
        104,
        106,
        108,
        110,
        112,
        114,
        116,
        118,
        120,
        119,
        118,
        117,
        116,
        115,
        114,
        113,
        114,
        115,
    ]
    rows = [_bar(index + 1, close, volume=100 + index * 2) for index, close in enumerate(closes)]

    result = summarize_sector_position("半导体", rows)

    assert result["available"] is True
    assert result["position_label"] == "pullback_acceptance"
    assert result["latest_close"] == 115
    assert result["twenty_day_high"] == 120
    assert result["twenty_day_low"] == 100
    assert result["drawdown_from_20d_high_percent"] == 4.17
    assert result["distance_from_20d_high_percent"] == -4.17
    assert result["distance_from_20d_low_percent"] == 15.0
    assert result["up_days_5d"] == 2
    assert result["down_days_5d"] == 3


def test_summarize_sector_position_detects_early_breakout_with_volume():
    rows = [
        _bar(index + 1, close, volume=100)
        for index, close in enumerate(
            [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 108, 107, 108, 109, 110]
        )
    ]
    rows.extend(
        [
            _bar(16, 109, volume=220),
            _bar(17, 110, volume=230),
            _bar(18, 111, volume=240),
            _bar(19, 112, volume=250),
            _bar(20, 114, volume=260),
        ]
    )

    result = summarize_sector_position("人工智能", rows)

    assert result["position_label"] == "early_breakout"
    assert result["breakout_over_prior_20d_high_percent"] == 1.79
    assert result["volume_ratio_5d_vs_20d"] > 1.5
    assert result["up_days_5d"] == 4
    assert result["down_days_5d"] == 1


def test_summarize_sector_position_returns_unavailable_for_short_series():
    result = summarize_sector_position("白酒", [_bar(1, 100), _bar(2, 101)])

    assert result["available"] is False
    assert result["reason"] == "insufficient_daily_kline"


def test_position_map_for_opportunities_respects_total_budget(monkeypatch):
    import time

    def slow_fetch(_label):
        time.sleep(0.08)
        return [_bar(index + 1, 100 + index) for index in range(20)]

    start = time.monotonic()
    result = build_sector_position_map_for_opportunities(
        ["半导体", "白酒", "创新药"],
        fetch_series=slow_fetch,
        total_timeout_seconds=0.02,
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.08
    assert result == {}


def test_default_fetch_series_allows_akshare_fallback(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        "app.services.sector_canonical.get_canonical_sector",
        lambda _label: object(),
    )

    def fake_fetch(canon, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_canonical_daily_kline_series",
        fake_fetch,
    )

    _default_fetch_series_for_label("半导体")

    assert captured["max_days"] == 40
    assert captured["allow_akshare"] is True
