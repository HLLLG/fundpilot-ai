from app.services.sector_intraday_provider import _points_from_minute_frame, fetch_sector_intraday


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


def test_fetch_sector_intraday_endpoint(monkeypatch):
    monkeypatch.setattr(
        "app.main.fetch_sector_intraday",
        lambda source_type, source_name, force_refresh=False: (
            [{"time": "09:31", "percent": 1.2}],
            None,
            "2026-06-03",
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
