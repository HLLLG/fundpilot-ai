from __future__ import annotations

from app.services.discovery_sector_heat import build_sector_heat_ranking, build_sector_heat_ranking_for_ui
from app.services.discovery_target_sectors import select_target_sectors
from app.services.sector_registry import list_theme_board_labels


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

    rows = build_sector_heat_ranking(force_refresh=True)

    assert len(rows) == len(theme_labels) + 1
    by_label = {row["sector_label"]: row for row in rows}
    assert by_label["半导体"]["change_1d_percent"] == 2.5
    assert set(theme_labels).issubset(by_label.keys())


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
