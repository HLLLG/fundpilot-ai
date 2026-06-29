from __future__ import annotations

from app.services.eastmoney_trends_client import _parse_daily_kline_series
from app.services.akshare_subprocess import _akshare_board_rows_to_daily_bars


def test_parse_daily_kline_series_keeps_volume_and_amount():
    payload = {
        "data": {
            "klines": [
                "2026-06-01,100,100,101,99,1000,200000,2.0,0.0,0,1.0",
                "2026-06-02,100,102,103,100,1500,330000,3.0,2.0,2,1.2",
            ]
        }
    }

    rows = _parse_daily_kline_series(payload, max_days=20)

    assert rows[-1]["date"] == "2026-06-02"
    assert rows[-1]["volume"] == 1500
    assert rows[-1]["amount"] == 330000


def test_akshare_board_rows_keep_volume_and_amount():
    rows = _akshare_board_rows_to_daily_bars(
        [
            {"date": "2026-06-01", "close": 100, "high": 101, "change_percent": 0, "volume": 1000, "amount": 200000},
            {"date": "2026-06-02", "close": 102, "high": 103, "change_percent": 2, "volume": 1500, "amount": 330000},
        ],
        max_days=20,
    )

    assert rows[-1]["volume"] == 1500
    assert rows[-1]["amount"] == 330000
