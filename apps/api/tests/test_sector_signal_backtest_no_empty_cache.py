from app.services.sector_signal_backtest import build_sector_signal_backtest, _BACKTEST_CACHE


def test_sector_signal_backtest_does_not_cache_empty_results(monkeypatch):
    _BACKTEST_CACHE.clear()
    calls = {"count": 0}

    def fake_fetch(_canon):
        calls["count"] += 1
        return []

    monkeypatch.setattr(
        "app.services.sector_signal_backtest.get_trade_date_set",
        lambda: frozenset(),
    )
    monkeypatch.setattr(
        "app.services.sector_signal_backtest._default_fetch_series_for_canon",
        fake_fetch,
    )

    first = build_sector_signal_backtest(["半导体"], lookback_days=30)
    second = build_sector_signal_backtest(["半导体"], lookback_days=30)

    assert first["has_data"] is False
    assert second["has_data"] is False
    assert calls["count"] == 2
    assert not _BACKTEST_CACHE
