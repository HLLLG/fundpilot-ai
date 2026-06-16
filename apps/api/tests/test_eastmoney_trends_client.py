from app.services.eastmoney_trends_client import (
    _parse_kline_day_close_percent,
    _parse_kline_payload,
    _parse_trends_payload,
    _read_em_json,
    _secid_candidates,
    fetch_eastmoney_intraday_trends,
)


def test_secid_candidates_includes_shanghai_composite_prefix():
    candidates = _secid_candidates("", "000001")
    assert candidates[0] == "1.000001"
    assert "2.000001" in candidates


def test_parse_kline_day_close_percent_when_pre_k_price_zero_uses_row_change():
    payload = {
        "data": {
            "preKPrice": 0.0,
            "klines": [
                "2026-06-03,99,100,101,98,0,0,0,0,0,0,0",
                "2026-06-04,99.2,110,111,109,0,0,1.97,-0.69,-1,0",
            ],
        }
    }
    change = _parse_kline_day_close_percent(payload, trade_date="2026-06-04")
    assert change == -0.69


def test_read_em_json_strips_jsonp_wrapper():
    class FakeResponse:
        text = 'jQuery123({"rc":0,"data":{"preKPrice":100,"klines":[]}});'

    payload = _read_em_json(FakeResponse())
    assert payload["rc"] == 0
    assert payload["data"]["preKPrice"] == 100


def test_parse_kline_day_close_percent_ignores_concept_board_pre_k_price_placeholder():
    """东财概念板块日 K 常带 preKPrice=1000 占位，须回落行内涨跌列。"""
    payload = {
        "data": {
            "preKPrice": 1000.0,
            "klines": [
                "2026-06-08,2600,2627.98,2630,2590,0,0,0,-1.2,0,0,0",
                "2026-06-09,2665.92,2683.24,2684.69,2627.98,83698232,248177535206.00,2.15,1.74,45.91,3.06",
            ],
        }
    }
    change = _parse_kline_day_close_percent(payload, trade_date="2026-06-09")
    assert change == 1.74


def test_parse_kline_day_close_percent_matches_intraday_close():
    payload = {
        "data": {
            "preKPrice": 100.0,
            "klines": [
                "2026-06-03,99,100,101,98,0,0,0,0,0,0,0",
                "2026-06-04,99.2,110,111,109,0,0,0,0.15,0,0,0",
            ],
        }
    }
    change = _parse_kline_day_close_percent(payload, trade_date="2026-06-04")
    assert change == round((110 / 100 - 1) * 100, 4)


def test_parse_kline_payload_uses_pre_close_like_yangjibao():
    payload = {
        "data": {
            "preKPrice": 100.0,
            "klines": [
                "2026-06-04 09:31,99.2,101,102,99,0,0,0,-0.8,0,0,0",
                "2026-06-04 15:00,100,110,111,109,0,0,0,1.05,0,0,0",
                "2026-06-04 16:00,100,111,112,109,0,0,0,1.10,0,0,0",
            ],
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
    assert points[0]["percent"] == round((1001 / 1000 - 1) * 100, 4)
    assert points[-1]["percent"] == round((1010 / 1000 - 1) * 100, 4)


def test_fetch_eastmoney_intraday_trends_trends2_path(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None, proxies=None):
            if "kline/get" in url:
                return FakeResponse({"data": {"klines": []}})
            if "trends2/get" in url:
                assert params["secid"] == "2.930713"
                return FakeResponse(
                    {
                        "data": {
                            "prePrice": 7000.0,
                            "trends": [
                                "2026-06-04 09:31,7000,6990,6995,6988,0,0,6990",
                                "2026-06-04 15:00,7000,6951,6960,6948,0,0,6951",
                            ],
                        }
                    }
                )
            raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(
        "app.services.eastmoney_trends_client.requests.Session",
        lambda: FakeSession(),
    )
    points = fetch_eastmoney_intraday_trends(
        "2.930713",
        source_code="930713",
        trade_date="2026-06-04",
    )
    assert len(points) == 2
    assert points[-1]["percent"] == round((6951 / 7000 - 1) * 100, 4)


def test_fetch_eastmoney_intraday_trends_kline_path(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=None, proxies=None):
            if "trends2/get" in url:
                return FakeResponse({"data": {"trends": []}})
            assert "kline/get" in url
            assert params["secid"] == "2.931994"
            assert params["klt"] == "1"
            return FakeResponse(
                {
                    "data": {
                        "preKPrice": 100.0,
                        "klines": [
                            "2026-06-04 09:31,99,101,102,99,0,0,0,0.2,0,0,0",
                            "2026-06-04 15:00,99,110,111,109,0,0,0,0.8,0,0,0",
                        ],
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

