from app.services.eastmoney_trends_client import _parse_daily_kline_series


def test_parse_daily_kline_series_computes_changes():
    payload = {
        "data": {
            "klines": [
                "2026-06-08,100,100,101,99,0,0,0,0,0,0",
                "2026-06-09,100,102,104,100,0,0,0,2.0,0,0",
                "2026-06-10,102,100.5,103,99.5,0,0,0,-1.47,0,0",
            ]
        }
    }
    series = _parse_daily_kline_series(payload, max_days=10)
    assert len(series) == 2
    assert series[0]["date"] == "2026-06-09"
    assert float(series[0]["change_percent"]) == 2.0
    assert float(series[0]["high_change_percent"]) == 4.0
    assert series[1]["date"] == "2026-06-10"
