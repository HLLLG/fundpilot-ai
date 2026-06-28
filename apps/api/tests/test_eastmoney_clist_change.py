from __future__ import annotations

from app.services.eastmoney_spot_client import (
    _parse_clist_theme_rows,
    fetch_eastmoney_clist_change_by_code,
    fetch_eastmoney_clist_theme_metrics_by_code,
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


def test_sector_heat_skips_kline_when_theme_has_5d(monkeypatch):
    from app.services.discovery_sector_heat import build_sector_heat_ranking

    monkeypatch.setattr(
        "app.services.discovery_sector_heat.list_theme_board_labels",
        lambda: ["白酒"],
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_theme_board_snapshot",
        lambda **_kwargs: {
            "available": True,
            "items": [
                {
                    "sector_label": "白酒",
                    "change_1d_percent": -1.0,
                    "change_5d_percent": -7.5,
                }
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat._merge_5d_kline_into_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("kline merge should be skipped")
        ),
    )

    rows = build_sector_heat_ranking(include_5d=True, force_refresh=True)
    assert rows[0]["change_5d_percent"] == -7.5


def test_fetch_eastmoney_clist_theme_metrics_live_smoke():
    by_code = fetch_eastmoney_clist_theme_metrics_by_code(timeout=20.0)
    assert len(by_code) > 100
    sample = by_code.get("BK1036") or by_code.get("H30184") or by_code.get("931672")
    assert sample is not None
    assert sample.get("change_1d") is not None or sample.get("change_5d") is not None
    assert fetch_eastmoney_clist_change_by_code(timeout=20.0)  # alias still works
