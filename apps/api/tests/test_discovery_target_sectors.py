from app.services.discovery_target_sectors import select_target_sectors


def test_dip_swing_selects_deepest_sectors_first():
    heat = [
        {"sector_label": "半导体", "heat_score": 1, "change_5d_percent": -8.0},
        {"sector_label": "银行", "heat_score": 2, "change_5d_percent": -1.0},
        {"sector_label": "光伏", "heat_score": 3, "change_5d_percent": -5.0},
    ]
    sectors = select_target_sectors([], None, heat, scan_mode="dip_swing", max_sectors=3)
    assert sectors[0] == "半导体"
    assert "银行" not in sectors[:2]


def test_dip_swing_focus_sectors_still_first():
    heat = [
        {"sector_label": "半导体", "heat_score": 1, "change_5d_percent": -8.0},
        {"sector_label": "银行", "heat_score": 2, "change_5d_percent": -1.0},
    ]
    sectors = select_target_sectors([], ["银行"], heat, scan_mode="dip_swing", max_sectors=2)
    assert sectors[0] == "银行"
