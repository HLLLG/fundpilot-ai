from app.services.sector_signal_backtest import build_sector_signal_backtest, _BACKTEST_CACHE


def test_sector_signal_backtest_response_cache(monkeypatch):
    _BACKTEST_CACHE.clear()
    calls = {"count": 0}

    def fake_fetch(_secid: str, _code: str | None):
        calls["count"] += 1
        return [
            {"date": "2026-06-02", "close": 100.0, "change_percent": 1.0},
            {"date": "2026-06-03", "close": 101.0, "change_percent": 1.0},
            {"date": "2026-06-04", "close": 99.0, "change_percent": -2.0},
        ]

    monkeypatch.setattr(
        "app.services.sector_signal_backtest.get_trade_date_set",
        lambda: frozenset({"2026-06-02", "2026-06-03", "2026-06-04"}),
    )
    monkeypatch.setattr(
        "app.services.sector_signal_backtest._default_fetch_series_for_canon",
        lambda _canon: fake_fetch("", None),
    )

    first = build_sector_signal_backtest(["半导体"], lookback_days=30)
    second = build_sector_signal_backtest(["半导体"], lookback_days=30)

    assert first["has_data"] is True
    assert second["has_data"] is True
    assert calls["count"] == 1
