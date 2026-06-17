from app.services.sector_canonical import get_canonical_sector
from app.services.sector_daily_kline_provider import fetch_canonical_daily_kline_series


def test_fetch_canonical_daily_kline_falls_back_to_akshare_board(monkeypatch):
    canon = get_canonical_sector("半导体")
    assert canon is not None

    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_eastmoney_daily_kline_series",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_daily_kline_via_relay",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_board_daily_kline_series",
        lambda *_args, **_kwargs: [
            {"date": "2026-06-16", "change_percent": 1.0},
            {"date": "2026-06-17", "change_percent": 2.5},
        ],
    )

    series = fetch_canonical_daily_kline_series(canon, max_days=20, timeout=1.0)
    assert len(series) == 2
    assert series[-1]["change_percent"] == 2.5


def test_fetch_canonical_daily_kline_falls_back_to_akshare_index(monkeypatch):
    canon = get_canonical_sector("电网设备")
    assert canon is not None
    assert canon.source_type == "index"

    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_eastmoney_daily_kline_series",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_daily_kline_via_relay",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_board_daily_kline_series",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_daily_kline_provider.fetch_index_daily_via_akshare",
        lambda *_args, **_kwargs: {
            "data": [
                {"date": "2026-06-16", "close": 100.0},
                {"date": "2026-06-17", "close": 101.5},
            ]
        },
    )

    series = fetch_canonical_daily_kline_series(canon, max_days=20, timeout=1.0)
    assert len(series) == 1
    assert series[0]["change_percent"] == 1.5
