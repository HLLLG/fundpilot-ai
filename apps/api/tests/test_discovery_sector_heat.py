from app.services.discovery_target_sectors import select_target_sectors


def test_select_target_sectors_prioritizes_focus():
    heat = [
        {"sector_label": "半导体", "heat_score": 3.0},
        {"sector_label": "商业航天", "heat_score": 2.0},
        {"sector_label": "人工智能", "heat_score": 1.0},
    ]
    sectors = select_target_sectors([], ["商业航天"], heat)
    assert sectors[0] == "商业航天"


def test_build_sector_heat_ranking_with_mock_series(monkeypatch):
    from app.services import discovery_sector_heat as module

    def fake_series(_secid, **kwargs):
        return [
            {"date": "2026-06-09", "change_percent": 0.5},
            {"date": "2026-06-10", "change_percent": 1.0},
            {"date": "2026-06-11", "change_percent": 2.0},
        ]

    monkeypatch.setattr(module, "fetch_eastmoney_daily_kline_series", fake_series)
    rows = module.build_sector_heat_ranking(fetch_series=fake_series)
    assert rows
    assert "sector_label" in rows[0]
