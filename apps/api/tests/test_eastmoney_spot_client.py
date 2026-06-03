from app.services.eastmoney_spot_client import _absorb_board_rows


def test_absorb_board_rows_parses_name_and_change():
    target: dict[str, float] = {}
    _absorb_board_rows(
        [
            {"f14": "半导体", "f3": 4.59},
            {"f14": "  中证人工智能  ", "f3": "5.54"},
            {"f14": "无效", "f3": "-"},
        ],
        target,
    )
    assert target["半导体"] == 4.59
    assert target["中证人工智能"] == 5.54
    assert "无效" not in target


def test_fetch_eastmoney_boards_merges_index_sources(monkeypatch):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            params = params or {}
            if params.get("fs") == "m:90 t:3 f:!50":
                return FakeResponse(
                    {
                        "data": {
                            "total": 1,
                            "diff": [{"f14": "半导体", "f3": 4.59}],
                        }
                    }
                )
            if params.get("fs") == "m:2":
                return FakeResponse(
                    {
                        "data": {
                            "total": 1,
                            "diff": [{"f14": "中证人工智能", "f3": 5.54}],
                        }
                    }
                )
            return FakeResponse({"data": {"total": 0, "diff": []}})

    monkeypatch.setattr(
        "app.services.eastmoney_spot_client.httpx.Client",
        FakeClient,
    )

    from app.services.eastmoney_spot_client import fetch_eastmoney_boards

    boards = fetch_eastmoney_boards()
    assert boards["concept"]["半导体"] == 4.59
    assert boards["index"]["中证人工智能"] == 5.54


def test_fetch_eastmoney_boards_limits_hosts_for_short_budget(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None):
            calls.append(url)
            raise RuntimeError("blocked")

    monkeypatch.setattr(
        "app.services.eastmoney_spot_client.httpx.Client",
        FakeClient,
    )

    from app.services.eastmoney_spot_client import fetch_eastmoney_boards

    boards = fetch_eastmoney_boards(timeout=0.2, max_retries=1, max_hosts=1)

    assert boards == {"concept": {}, "industry": {}, "index": {}}
    assert calls
    assert all("://79.push2.eastmoney.com/" in url for url in calls)
