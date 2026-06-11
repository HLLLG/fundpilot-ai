from app.services.sector_signal_backtest import build_sector_signal_backtest


def _no_trade_calendar():
    return None


def _series():
    return [
        {"date": "2026-01-02", "change_percent": 1.2, "high_change_percent": 1.5},
        {"date": "2026-01-03", "change_percent": -1.0, "high_change_percent": 0.2},
        {"date": "2026-01-06", "change_percent": -0.5, "high_change_percent": 0.1},
        {"date": "2026-01-07", "change_percent": 2.5, "high_change_percent": 3.2},
        {"date": "2026-01-08", "change_percent": 1.0, "high_change_percent": 1.2},
        {"date": "2026-01-09", "change_percent": -2.5, "high_change_percent": -0.5},
        {"date": "2026-01-10", "change_percent": -0.2, "high_change_percent": 0.0},
    ]


def test_build_sector_signal_backtest_with_injected_series(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_signal_backtest.get_trade_date_set",
        _no_trade_calendar,
    )

    def fake_fetch(_secid: str, _source_code: str | None):
        return _series()

    result = build_sector_signal_backtest(
        ["半导体"],
        lookback_days=30,
        fetch_series=fake_fetch,
    )

    assert result["has_data"] is True
    assert result["sector_count"] == 1
    reversal = result["by_rule"]["reversal_down"]
    assert reversal["trigger_count"] >= 1
    assert reversal["hit_rate_percent"] is not None
    assert result["summary_lines"]


def test_build_sector_signal_backtest_unknown_sector():
    result = build_sector_signal_backtest(
        ["不存在的板块"],
        lookback_days=30,
        fetch_series=lambda *_args: [],
    )
    assert result["has_data"] is False
    assert result["sectors"] == []
