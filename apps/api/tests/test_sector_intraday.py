from app.services.sector_intraday_provider import (
    _points_from_minute_frame,
    fetch_sector_intraday,
)
from app.services.sector_quote_cache import save_spot_snapshot


def test_points_from_minute_frame_parses_change_column():
    class Row:
        def __init__(self, data):
            self.index = list(data.keys())
            self._data = data

        def __getitem__(self, key):
            return self._data[key]

    class Frame:
        def __init__(self, rows):
            self._rows = rows

        @property
        def empty(self):
            return not self._rows

        def iterrows(self):
            for idx, row in enumerate(self._rows):
                yield idx, row

        def iloc(self, index):
            return self._rows[index]

    frame = Frame(
        [
            Row({"时间": "09:31", "涨跌幅": "0.12"}),
            Row({"时间": "09:32", "涨跌幅": "0.35"}),
        ]
    )
    points = _points_from_minute_frame(frame)
    assert len(points) == 2
    assert points[0]["time"] == "09:31"
    assert points[1]["percent"] == 0.35


def test_fetch_index_intraday_uses_browser_when_eastmoney_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.get_settings",
        lambda: type(
            "S",
            (),
            {
                "sector_quotes_browser_enabled": True,
                "sector_quotes_browser_timeout_seconds": 20.0,
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.fetch_eastmoney_intraday_trends",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.fetch_intraday_via_browser_command",
        lambda secid, **kwargs: (
            [{"time": "09:31", "percent": -0.5}, {"time": "15:00", "percent": 0.2}]
            if secid == "2.931865"
            else []
        ),
    )
    from app.services.sector_intraday_provider import _fetch_index_intraday

    points = _fetch_index_intraday("中证半导体", trade_date="2026-06-04")
    assert len(points) == 2
    assert points[-1]["percent"] == 0.2


def test_fetch_sector_intraday_uses_stale_cache_when_live_fetch_empty(monkeypatch):
    trade_date = "2026-06-10"

    monkeypatch.setattr(
        "app.services.sector_intraday_provider.build_trading_session",
        lambda: {
            "session_kind": "trading_day_after_close",
            "is_trading_day": True,
        },
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.get_effective_trade_date",
        lambda **kwargs: trade_date,
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider._fetch_index_intraday",
        lambda *args, **kwargs: [],
    )
    # stale 缓存必须有 ≥30 点才会被用作回退（骨架点不算有效缓存）
    stale_points = [{"time": f"09:{30 + i:02d}", "percent": round(i * 0.01, 4)} for i in range(30)]
    # stale key 需与 fetch_sector_intraday 实际使用的 trade_date 匹配（CN 时区，非 UTC date.today）
    save_spot_snapshot(
        f"intraday:v2:index:中证电网设备:{trade_date}",
        {
            "points": stale_points,
            "note": "展示缓存分时",
            "close_change_percent": 0.29,
        },
    )
    points, note, _, close = fetch_sector_intraday(
        "index", "中证电网设备", force_refresh=True
    )
    assert len(points) == 30
    assert close == 0.29
    assert note and "缓存" in note


def test_fetch_sector_intraday_endpoint(monkeypatch):
    monkeypatch.setattr(
        "app.main.fetch_sector_intraday",
        lambda source_type, source_name, force_refresh=False: (
            [{"time": "09:31", "percent": 1.2}],
            None,
            "2026-06-03",
            1.2,
        ),
    )
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get(
        "/api/sector-quotes/intraday",
        params={"source_type": "index", "source_name": "中证人工智能"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["points"][0]["percent"] == 1.2
