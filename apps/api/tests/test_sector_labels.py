from app.services.sector_labels import build_sector_candidates, normalize_sector_label


def test_normalize_sector_label():
    assert normalize_sector_label("中证电网设备..") == "中证电网设备"
    assert normalize_sector_label("  半导体  ") == "半导体"


def test_build_sector_candidates():
    candidates = build_sector_candidates("中证人工智能")
    assert "中证人工智能" in candidates
    assert "人工智能" in candidates
