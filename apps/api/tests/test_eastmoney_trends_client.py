from app.services.eastmoney_trends_client import (
    _parse_kline_payload,
    _parse_trends_payload,
    _secid_candidates,
    fetch_eastmoney_intraday_trends,
)


def test_secid_candidates_prefers_csi_prefix_two():
    candidates = _secid_candidates("0.931994", "931994")
    assert candidates == ["2.931994", "0.931994"]


def test_parse_kline_payload_rebases_to_session_open_close():
    payload = {
        "data": {
            "klines": [
                "2026-06-04 09:31,100,101,102,99,0,0,0,0.12,0,0,0",
                "2026-06-04 15:00,100,110,111,109,0,0,0,1.05,0,0,0",
                "2026-06-04 16:00,100,111,112,109,0,0,0,1.10,0,0,0",
            ]
        }
    }
    points = _parse_kline_payload(payload, trade_date="2026-06-04")
    assert len(points) == 2
    assert points[0]["time"] == "09:31"
    assert points[0]["percent"] == round((101 / 100 - 1) * 100, 4)
    assert points[-1]["percent"] == round((110 / 100 - 1) * 100, 4)


def test_parse_trends_payload_builds_session_percent_series():
    payload = {
        "data": {
            "prePrice": 1000.0,
            "trends": [
                "2026-06-04 09:31,1000,1001,1002,999,0,0,1000.5",
                "2026-06-04 15:00,1000,1010,1011,1009,0,0,1010",
            ],
        }
    }
    points = _parse_trends_payload(payload, trade_date="2026-06-04")
    assert len(points) == 2
    assert points[0]["percent"] == 0.0
    assert points[-1]["percent"] == round(1.1 - 0.2, 4)


def test_fetch_after_close_uses_eastmoney_kline(monkeypatch):
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.build_trading_session",
        lambda: {
            "session_kind": "trading_day_after_close",
            "is_trading_day": True,
        },
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.get_spot_snapshot",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.save_spot_snapshot",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.sector_intraday_provider.fetch_eastmoney_intraday_trends",
        lambda *args, **kwargs: [
            {"time": "09:31", "percent": 0.1},
            {"time": "15:00", "percent": 1.0},
        ],
    )

    from app.services.sector_intraday_provider import fetch_sector_intraday

    points, note, session_date = fetch_sector_intraday("index", "中证电网设备")
    assert len(points) == 2
    assert session_date is not None
    assert note and "收盘分时" in note


def test_fetch_eastmoney_intraday_trends_kline_path(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None, proxies=None):
            assert "kline/get" in url
            assert params["secid"] == "2.931994"
            return FakeResponse(
                {
                    "data": {
                        "klines": [
                            "2026-06-04 09:31,1,1,1,1,0,0,0,0.2,0,0,0",
                            "2026-06-04 15:00,1,1,1,1,0,0,0,0.8,0,0,0",
                        ]
                    }
                }
            )

    monkeypatch.setattr(
        "app.services.eastmoney_trends_client.requests.Session",
        lambda: FakeSession(),
    )
    points = fetch_eastmoney_intraday_trends(
        "2.931994",
        source_code="931994",
        trade_date="2026-06-04",
    )
    assert len(points) == 2
    assert points[0]["time"] == "09:31"
