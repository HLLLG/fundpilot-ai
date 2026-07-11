from __future__ import annotations

from app.services.eastmoney_spot_client import (
    _parse_clist_theme_rows,
    fetch_eastmoney_current_board_flow,
    fetch_eastmoney_clist_change_by_code,
)
from app.services.theme_board_snapshot import (
    _lookup_clist_changes,
    _lookup_clist_flow,
    refresh_theme_board_snapshot,
)


def test_parse_clist_theme_rows_indexes_change_and_flow():
    rows = [
        {
            "f12": "BK1036",
            "f14": "半导体",
            "f3": 2.5,
            "f109": -1.2,
            "f62": 120000000.0,
            "f66": 80000000.0,
        },
        {"f12": "H30184", "f14": "半导体", "f3": 1.1, "f109": 3.4, "f62": -50000000.0},
    ]
    by_code = _parse_clist_theme_rows(rows)
    assert by_code["BK1036"]["change_1d"] == 2.5
    assert by_code["BK1036"]["change_5d"] == -1.2
    assert by_code["BK1036"]["main_force_net_yi"] == 1.2
    assert by_code["BK1036"]["super_large_net_yi"] == 0.8
    assert by_code["H30184"]["main_force_net_yi"] == -0.5


def test_lookup_clist_changes_prefers_source_code():
    entry = {
        "secid": "2.H30184",
        "source_code": "H30184",
        "flow_source_code": "BK1036",
        "board_kind": "index",
    }
    by_code = {
        "H30184": {"change_1d": 1.5, "change_5d": 2.0},
        "BK1036": {"change_1d": 9.9, "change_5d": 8.8},
    }
    change_1d, change_5d = _lookup_clist_changes(entry, by_code)
    assert change_1d == 1.5
    assert change_5d == 2.0


def test_lookup_clist_flow_prefers_flow_source_code():
    entry = {
        "secid": "2.H30184",
        "source_code": "H30184",
        "flow_source_code": "BK1036",
        "board_kind": "index",
    }
    by_code = {
        "H30184": {"main_force_net_yi": -9.9},
        "BK1036": {"main_force_net_yi": 3.3},
    }
    flow = _lookup_clist_flow(entry, by_code)
    assert flow["main_force_net_yi"] == 3.3


def test_lookup_clist_flow_falls_back_to_index_code():
    entry = {
        "secid": "2.931672",
        "source_code": "931672",
        "flow_source_code": None,
        "board_kind": "index",
    }
    by_code = {"931672": {"main_force_net_yi": -65.67}}
    flow = _lookup_clist_flow(entry, by_code)
    assert flow["main_force_net_yi"] == -65.67


def test_refresh_theme_board_snapshot_uses_clist_bulk(monkeypatch):
    universe = [
        {
            "sector_label": "半导体",
            "board_kind": "index",
            "secid": "2.H30184",
            "source_code": "H30184",
            "flow_source_code": "BK1036",
            "change_hint": None,
        },
        {
            "sector_label": "白酒",
            "board_kind": "concept",
            "secid": "90.BK0896",
            "source_code": "BK0896",
            "flow_source_code": None,
            "change_hint": None,
        },
    ]

    monkeypatch.setattr(
        "app.services.theme_board_snapshot.list_theme_board_universe",
        lambda: universe,
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.build_trading_session",
        lambda: {
            "effective_trade_date": "2026-06-27",
            "session_kind": "trading_day_after_close",
        },
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_clist_theme_metrics_by_code",
        lambda **_: {
            "H30184": {"change_1d": 2.2, "change_5d": -3.3, "main_force_net_yi": -1.1},
            "BK0896": {"change_1d": -1.1, "change_5d": 4.4, "main_force_net_yi": 5.5},
        },
    )
    monkeypatch.setattr(
        "app.services.theme_board_snapshot.fetch_eastmoney_kline_close_percent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("kline should not run when clist hits")
        ),
    )
    saved: dict = {}

    monkeypatch.setattr(
        "app.services.theme_board_snapshot.save_spot_snapshot",
        lambda _key, payload: saved.update(payload),
    )

    snapshot = refresh_theme_board_snapshot(trade_date="2026-06-27")
    by_label = {item["sector_label"]: item for item in snapshot["items"]}

    assert by_label["半导体"]["change_1d_percent"] == 2.2
    assert by_label["半导体"]["change_5d_percent"] == -3.3
    assert by_label["半导体"]["main_force_net_yi"] == -1.1
    assert by_label["白酒"]["change_1d_percent"] == -1.1
    assert by_label["白酒"]["main_force_net_yi"] == 5.5
    assert saved["items"] == snapshot["items"]


def test_fetch_eastmoney_clist_change_by_code_delegates_to_theme_metrics(monkeypatch):
    sentinel = {"BK1036": {"change_1d": 1.0, "change_5d": None}}
    captured: dict = {}

    def _mock_fetch(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(
        "app.services.eastmoney_spot_client.fetch_eastmoney_clist_theme_metrics_by_code",
        _mock_fetch,
    )
    assert fetch_eastmoney_clist_change_by_code(timeout=12.0) is sentinel
    assert captured == {"timeout": 12.0, "max_retries": 2, "max_pages": 8}


def test_fetch_current_board_flow_uses_exact_dated_fflow_kline(monkeypatch):
    captured: dict = {"calls": []}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "klines": [
                        "2026-07-09,100000000,200000000,300000000,400000000,500000000",
                        "2026-07-10,-13483589632,9724399616,3810451456,-3402293248,-10081296384",
                    ]
                }
            }

    class _Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, *, params):
            captured["calls"].append((url, params))
            return _Response()

    monkeypatch.setattr("app.services.eastmoney_spot_client.httpx.Client", _Client)

    flow = fetch_eastmoney_current_board_flow(
        "90.BK0800",
        trade_date="2026-07-10",
        timeout=0.25,
        max_retries=1,
        max_hosts=1,
    )

    assert flow == {
        "date": "2026-07-10",
        "main_force_net_yi": -134.84,
        "flow_tiers": {
            "super_large_net_yi": -100.81,
            "large_net_yi": -34.02,
            "medium_net_yi": 38.1,
            "small_net_yi": 97.24,
        },
    }
    assert captured["client"]["timeout"] == 0.25
    assert captured["client"]["trust_env"] is False
    url, params = captured["calls"][0]
    assert url == "https://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get"
    assert params == {
        "lmt": "10",
        "klt": "101",
        "secid": "90.BK0800",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    }


def test_fetch_current_board_flow_rejects_mismatched_response_date(monkeypatch):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"klines": ["2026-07-09,1,2,3,4,5"]}}

    class _Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, _url, *, params):
            return _Response()

    monkeypatch.setattr("app.services.eastmoney_spot_client.httpx.Client", _Client)

    assert (
        fetch_eastmoney_current_board_flow(
            "90.BK0800",
            trade_date="2026-07-10",
            max_retries=1,
            max_hosts=1,
        )
        is None
    )
