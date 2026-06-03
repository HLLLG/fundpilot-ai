from app.services.sector_quote_resolver import resolve_sector_quote


def test_resolve_exact_index_match():
    boards = {
        "index": {"上证指数": 0.52, "中证人工智能": -1.2},
        "concept": {"半导体": 2.5},
        "industry": {},
    }
    result = resolve_sector_quote("中证人工智能", boards)
    assert result.confidence == "high"
    assert result.change_percent == -1.2
    assert result.matched_name == "中证人工智能"


def test_resolve_concept_match():
    boards = {
        "index": {},
        "concept": {"半导体": 4.57, "商业航天": 3.19},
        "industry": {},
    }
    result = resolve_sector_quote("半导体", boards)
    assert result.confidence == "high"
    assert result.change_percent == 4.57


def test_resolve_low_confidence_multiple():
    boards = {
        "index": {},
        "concept": {"人工智能": 1.0, "人工智能ETF": 1.1},
        "industry": {"人工智能": 0.9},
    }
    result = resolve_sector_quote("人工智能", boards)
    assert result.confidence == "low"
    assert len(result.candidates) >= 2


def test_resolve_prefers_index_ai_for_csi_ai_label():
    boards = {
        "index": {"人工智能": 5.54, "新兴成指人工智能": 2.8},
        "concept": {"人工智能": 0.07},
        "industry": {},
    }
    result = resolve_sector_quote("中证人工智能", boards)
    assert result.confidence == "high"
    assert result.matched_name == "人工智能"
    assert result.change_percent == 5.54


def test_resolve_prefers_power_equipment_theme_for_grid_label():
    boards = {
        "index": {"电力设备主题": 1.5, "中证全指电网": 0.97},
        "concept": {},
        "industry": {"电网设备": -0.58},
    }
    result = resolve_sector_quote("中证电网设备", boards)
    assert result.confidence == "high"
    assert result.matched_name == "电力设备主题"
    assert result.change_percent == 1.5


def test_resolve_prefers_concept_commercial_aerospace():
    boards = {
        "index": {},
        "concept": {"商业航天": 3.19},
        "industry": {},
    }
    result = resolve_sector_quote("商业航天", boards)
    assert result.confidence == "high"
    assert result.matched_name == "商业航天"
    assert result.source_type == "concept"
    assert result.change_percent == 3.19


def test_resolve_prefers_industry_semiconductor():
    boards = {
        "index": {"半导体": 5.25},
        "concept": {"核心半导体": 3.33},
        "industry": {"半导体": 4.59},
    }
    result = resolve_sector_quote("半导体", boards)
    assert result.confidence == "high"
    assert result.matched_name == "半导体"
    assert result.source_type == "industry"
    assert result.change_percent == 4.59
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
