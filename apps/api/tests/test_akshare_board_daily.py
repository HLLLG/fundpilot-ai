from app.services.akshare_subprocess import _akshare_board_rows_to_daily_bars


def test_akshare_board_rows_to_daily_bars_maps_change_and_high():
    rows = [
        {
            "date": "2026-06-08",
            "close": 100.0,
            "high": 101.0,
            "change_percent": 1.0,
        },
        {
            "date": "2026-06-09",
            "close": 98.0,
            "high": 102.0,
            "change_percent": -2.0,
        },
    ]
    bars = _akshare_board_rows_to_daily_bars(rows, max_days=10)
    assert len(bars) == 2
    assert bars[0]["date"] == "2026-06-08"
    assert float(bars[0]["change_percent"]) == 1.0
    assert float(bars[1]["high_change_percent"]) == 2.0
