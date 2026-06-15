from app.services.discovery_target_sectors import select_target_sectors


def test_select_target_sectors_prioritizes_focus():
    heat = [
        {"sector_label": "半导体", "heat_score": 3.0},
        {"sector_label": "商业航天", "heat_score": 2.0},
        {"sector_label": "人工智能", "heat_score": 1.0},
    ]
    sectors = select_target_sectors([], ["商业航天"], heat)
    assert sectors[0] == "商业航天"


def test_list_discovery_sector_labels_dedupes_aliases():
    from app.services.sector_canonical import list_discovery_sector_labels

    labels = list_discovery_sector_labels()
    assert len(labels) == 19
    assert "互联网" in labels
    assert "有色金属" in labels
    assert "中证半导体" not in labels
    assert "中证人工智能" not in labels


def test_select_full_market_sectors_by_heat():
    heat = [
        {"sector_label": "半导体", "heat_score": 1.0},
        {"sector_label": "互联网", "heat_score": 3.0},
        {"sector_label": "有色金属", "heat_score": 2.0},
    ]
    from app.models import Holding

    sectors = select_target_sectors(
        [Holding(fund_code="111111", fund_name="A", holding_amount=10000, sector_name="半导体")],
        [],
        heat,
        scan_mode="full_market",
    )
    assert sectors[0] == "互联网"
    assert "半导体" in sectors


def test_build_sector_heat_ranking_with_close_percent_only(monkeypatch):
    from app.services import discovery_sector_heat as module
    from app.services.sector_canonical import list_discovery_sector_labels

    monkeypatch.setattr(module, "fetch_eastmoney_daily_kline_series", lambda *_a, **_k: [])
    monkeypatch.setattr(module, "get_spot_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "save_spot_snapshot", lambda *args, **kwargs: None)
    rows = module.build_sector_heat_ranking(
        fetch_close_percent=lambda *_a, **_k: 1.5,
        fetch_series=lambda *_a, **_k: [],
    )
    assert rows
    assert rows[0]["change_1d_percent"] == 1.5
    assert rows[0]["heat_score"] == 1.5
    assert len(rows) == len(list_discovery_sector_labels())


def test_build_sector_heat_ranking_uses_cache(monkeypatch):
    from app.services import discovery_sector_heat as module

    cached_rows = [{"sector_label": "半导体", "heat_score": 9.9, "change_1d_percent": 2.0}]
    monkeypatch.setattr(
        module,
        "get_spot_snapshot",
        lambda *args, **kwargs: {"sectors": cached_rows},
    )

    def fail_build(*args, **kwargs):
        raise AssertionError("should not rebuild when cache hit")

    monkeypatch.setattr(module, "_build_sector_heat_rows", fail_build)
    rows = module.build_sector_heat_ranking()
    assert rows == cached_rows


def test_build_sector_heat_ranking_with_series(monkeypatch):
    from app.services import discovery_sector_heat as module

    def fake_series(_secid, **kwargs):
        return [
            {"date": "2026-06-09", "change_percent": 0.5},
            {"date": "2026-06-10", "change_percent": 1.0},
            {"date": "2026-06-11", "change_percent": 2.0},
        ]

    def fake_close_percent(_secid, **kwargs):
        return 2.0

    monkeypatch.setattr(module, "get_spot_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "save_spot_snapshot", lambda *args, **kwargs: None)
    rows = module.build_sector_heat_ranking(
        fetch_close_percent=fake_close_percent,
        fetch_series=fake_series,
    )
    assert rows
    assert "sector_label" in rows[0]
