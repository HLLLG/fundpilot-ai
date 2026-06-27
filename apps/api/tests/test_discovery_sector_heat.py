from __future__ import annotations

import time

from app.services.discovery_sector_heat import build_sector_heat_ranking, build_sector_heat_ranking_for_ui
from app.services.discovery_target_sectors import select_target_sectors
from app.services.sector_registry import list_theme_board_labels


def test_sector_heat_5d_merge_respects_budget(monkeypatch):
    theme_labels = ["半导体", "白酒", "军工"]

    monkeypatch.setattr(
        "app.services.discovery_sector_heat.list_theme_board_labels",
        lambda: theme_labels,
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_theme_board_snapshot",
        lambda **_kwargs: {
            "available": True,
            "items": [
                {"sector_label": "半导体", "change_1d_percent": 1.0},
                {"sector_label": "白酒", "change_1d_percent": -1.0},
                {"sector_label": "军工", "change_1d_percent": -2.0},
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )

    def slow_fetcher(canon, *, lightweight: bool = False, network_timeout: float = 12.0):
        time.sleep(0.2)
        return [{"date": "2026-06-26", "change_percent": -1.0}] * 6

    started = time.monotonic()
    rows = build_sector_heat_ranking(
        include_5d=True,
        fetch_canon_series=slow_fetcher,
        force_refresh=True,
        budget_seconds=0.05,
    )
    elapsed = time.monotonic() - started

    assert len(rows) == len(theme_labels) + 1  # 国防军工 alias
    assert elapsed < 0.35


def test_sector_heat_ranking_uses_theme_board_label_count(monkeypatch):
    theme_labels = list_theme_board_labels()
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_theme_board_snapshot",
        lambda **_kwargs: {
            "available": True,
            "items": [{"sector_label": "半导体", "change_1d_percent": 2.5}],
        },
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )

    rows = build_sector_heat_ranking(force_refresh=True, include_5d=False)

    assert len(rows) == len(theme_labels) + 1
    by_label = {row["sector_label"]: row for row in rows}
    assert by_label["半导体"]["change_1d_percent"] == 2.5
    assert set(theme_labels).issubset(by_label.keys())


def test_sector_heat_ranking_include_5d_merges_into_rows(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.list_theme_board_labels",
        lambda: ["白酒"],
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_theme_board_snapshot",
        lambda **_kwargs: {
            "available": True,
            "items": [{"sector_label": "白酒", "change_1d_percent": -1.0}],
        },
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat._resolve_kline_canon",
        lambda _label: object(),
    )

    def fake_fetcher(_canon, *, lightweight: bool = False, network_timeout: float = 12.0):
        return [
            {"date": "2026-06-20", "change_percent": 0.5},
            {"date": "2026-06-23", "change_percent": -1.0},
            {"date": "2026-06-24", "change_percent": -2.0},
            {"date": "2026-06-25", "change_percent": -1.5},
            {"date": "2026-06-26", "change_percent": -3.0},
        ]

    rows = build_sector_heat_ranking(
        include_5d=True,
        fetch_canon_series=fake_fetcher,
        force_refresh=True,
        budget_seconds=5.0,
    )
    by_label = {row["sector_label"]: row for row in rows}

    assert by_label["白酒"]["change_1d_percent"] == -1.0
    assert by_label["白酒"]["change_5d_percent"] == -7.5


def test_sector_heat_5d_only_fetches_top_15_by_1d_drop(monkeypatch):
    labels = [f"板块{i:02d}" for i in range(20)]
    items = [
        {"sector_label": label, "change_1d_percent": -float(i)}
        for i, label in enumerate(labels)
    ]
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.list_theme_board_labels",
        lambda: labels,
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_theme_board_snapshot",
        lambda **_kwargs: {"available": True, "items": items},
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat._resolve_kline_canon",
        lambda _label: object(),
    )

    fetched: list[str] = []

    def fetch_with_track(label, trade_date, series_fetcher, network_timeout):
        fetched.append(label)
        return label, -5.0

    monkeypatch.setattr(
        "app.services.discovery_sector_heat._fetch_sector_5d_change",
        fetch_with_track,
    )

    rows = build_sector_heat_ranking(
        include_5d=True,
        force_refresh=True,
        budget_seconds=5.0,
    )
    by_label = {row["sector_label"]: row for row in rows}

    expected = {f"板块{i:02d}" for i in range(5, 20)}
    assert set(fetched) == expected
    assert len(fetched) == 15
    assert by_label["板块19"]["change_5d_percent"] == -5.0
    assert by_label["板块00"]["change_5d_percent"] is None


def test_sector_heat_ui_reads_theme_board_snapshot(monkeypatch):
    theme_labels = list_theme_board_labels()
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_theme_board_snapshot",
        lambda **_kwargs: {
            "available": True,
            "items": [
                {"sector_label": "半导体", "change_1d_percent": 1.23},
                {"sector_label": "白酒", "change_1d_percent": -1.5},
                {"sector_label": "军工", "change_1d_percent": -2.25},
            ],
        },
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )

    rows = build_sector_heat_ranking_for_ui()

    assert len(rows) == len(theme_labels) + 1
    by_label = {row["sector_label"]: row for row in rows}
    assert set(theme_labels).issubset(by_label.keys())
    assert by_label["半导体"]["change_1d_percent"] == 1.23
    assert by_label["人工智能"]["change_1d_percent"] is None


def test_sector_heat_ui_merges_discovery_5d_cache(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_theme_board_snapshot",
        lambda **_kwargs: {
            "available": True,
            "items": [{"sector_label": "白酒", "change_1d_percent": -1.0}],
        },
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_spot_snapshot",
        lambda *_args, **_kwargs: {
            "sectors": [
                {
                    "sector_label": "白酒",
                    "change_1d_percent": -2.0,
                    "change_5d_percent": -8.5,
                    "heat_score": -5.0,
                }
            ]
        },
    )

    rows = build_sector_heat_ranking_for_ui()
    by_label = {row["sector_label"]: row for row in rows}

    assert by_label["白酒"]["change_1d_percent"] == -1.0
    assert by_label["白酒"]["change_5d_percent"] == -8.5


def test_sector_heat_ui_adds_defense_alias_row(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_theme_board_snapshot",
        lambda **_kwargs: {
            "available": True,
            "items": [{"sector_label": "军工", "change_1d_percent": -2.25}],
        },
    )
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_spot_snapshot",
        lambda *_args, **_kwargs: None,
    )

    rows = build_sector_heat_ranking_for_ui()
    by_label = {row["sector_label"]: row for row in rows}

    assert "国防军工" in by_label
    assert by_label["国防军工"]["change_1d_percent"] == -2.25


def test_sector_heat_ui_fallback_when_theme_snapshot_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_sector_heat.get_theme_board_snapshot",
        lambda **_kwargs: {"available": False, "items": []},
    )

    rows = build_sector_heat_ranking_for_ui()

    assert len(rows) == len(list_theme_board_labels())
    assert all(row["change_1d_percent"] is None for row in rows)


def test_select_target_sectors_auto_picks_from_theme_heat_ranking():
    heat = [
        {"sector_label": "低空经济", "heat_score": 3.0, "change_1d_percent": 3.0},
        {"sector_label": "半导体", "heat_score": 2.0, "change_1d_percent": 2.0},
        {"sector_label": "白酒", "heat_score": 1.0, "change_1d_percent": 1.0},
    ]
    picked = select_target_sectors([], None, heat, scan_mode="full_market", max_sectors=2)
    assert picked == ["低空经济", "半导体"]
