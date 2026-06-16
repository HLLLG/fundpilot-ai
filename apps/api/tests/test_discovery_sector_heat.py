from app.services.discovery_sector_heat import (
    build_sector_heat_ranking,
    build_sector_heat_ranking_for_ui,
    fallback_sector_heat_rows,
)
from app.services.sector_canonical import list_discovery_sector_labels


def test_fallback_sector_heat_rows_covers_all_labels():
    rows = fallback_sector_heat_rows()
    labels = list_discovery_sector_labels()
    assert len(rows) == len(labels)
    assert {row["sector_label"] for row in rows} == set(labels)


def test_build_sector_heat_ranking_returns_fallback_when_network_empty(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_sector_heat._build_sector_heat_rows",
        lambda **_kwargs: [],
    )
    rows = build_sector_heat_ranking(force_refresh=True)
    assert len(rows) == len(list_discovery_sector_labels())


def test_build_sector_heat_ranking_for_ui_respects_budget(monkeypatch):
    import time

    def slow_row(*_args, **_kwargs):
        time.sleep(0.05)
        return {
            "sector_label": "半导体",
            "change_1d_percent": 1.0,
            "change_5d_percent": None,
            "heat_score": 1.0,
        }

    monkeypatch.setattr(
        "app.services.discovery_sector_heat._sector_heat_row",
        slow_row,
    )
    start = time.time()
    rows = build_sector_heat_ranking_for_ui()
    elapsed = time.time() - start
    assert len(rows) == len(list_discovery_sector_labels())
    assert elapsed < 20.0
