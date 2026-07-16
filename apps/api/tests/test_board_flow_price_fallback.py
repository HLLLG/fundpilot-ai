from app.services.board_fund_flow_history import parse_board_flow_kline
from app.services import discovery_sector_position as position_module


def test_parse_board_flow_kline_keeps_close_and_change_percent():
    point = parse_board_flow_kline(
        "2026-07-15,100000000,-20000000,30000000,40000000,50000000,"
        "1,2,3,4,5,1234.56,2.35,0,0"
    )

    assert point is not None
    assert point["main_force_net_yi"] == 1.0
    assert point["close_price"] == 1234.56
    assert point["change_percent"] == 2.35


def test_flow_history_price_rows_use_close_without_inventing_volume(monkeypatch):
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.resolve_board_flow_code_for_sector",
        lambda _label: ("BK1128", "CPO"),
    )
    monkeypatch.setattr(
        "app.services.board_fund_flow_history.get_board_flow_series_cache_only",
        lambda _code: [
            {
                "date": f"2026-06-{day:02d}",
                "close_price": 100 + day,
                "change_percent": 1.0,
            }
            for day in range(1, 21)
        ],
    )

    rows = position_module._flow_history_price_rows("CPO")

    assert len(rows) == 20
    assert rows[-1]["close"] == 120
    assert rows[-1]["volume"] is None
    assert rows[-1]["_source"] == "eastmoney_board_fund_flow_daily_close"


def test_hong_kong_mainline_uses_official_sina_index_history(monkeypatch):
    monkeypatch.setattr(
        "app.services.akshare_subprocess.fetch_hk_index_daily_history",
        lambda symbol, trading_days: {
            "source": "sina_hk_index_daily",
            "data": [{"date": "2026-07-15", "close": 4740.49}],
        },
    )

    rows = position_module._hk_index_price_rows("恒生科技")

    assert rows == [
        {
            "date": "2026-07-15",
            "close": 4740.49,
            "_source": "sina_hk_index_daily",
        }
    ]
