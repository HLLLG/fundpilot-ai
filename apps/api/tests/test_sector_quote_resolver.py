from app.services.sector_quote_resolver import resolve_sector_quote


def test_resolve_low_confidence_multiple():
    boards = {
        "index": {},
        "concept": {"国产算力": 1.0, "国产算力ETF": 1.1},
        "industry": {"国产算力": 0.9},
    }
    result = resolve_sector_quote("国产算力", boards)
    assert result.confidence == "low"
    assert len(result.candidates) >= 2


def test_resolve_grid_related_board_without_index_uses_concept_when_no_canonical():
    boards = {
        "index": {"电力设备主题": 1.5},
        "concept": {"电网设备": 0.42},
        "industry": {},
    }
    result = resolve_sector_quote("某自定义主题", boards)
    assert result.confidence == "none"


def test_resolve_uses_persisted_mapping_over_boards():
    boards = {
        "index": {},
        "concept": {"半导体": 2.0, "半导体设备": 3.0},
        "industry": {},
    }
    mapping = {
        "source_type": "concept",
        "source_name": "半导体设备",
    }
    result = resolve_sector_quote("半导体", boards, persisted_mapping=mapping)
    assert result.confidence == "high"
    assert result.change_percent == 3.0
