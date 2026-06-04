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
        "concept": {"国产算力": 1.0, "国产算力ETF": 1.1},
        "industry": {"国产算力": 0.9},
    }
    result = resolve_sector_quote("国产算力", boards)
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


def test_resolve_prefers_csi_grid_index_for_intraday_label():
    boards = {
        "index": {"中证电网设备": 1.59, "电力设备主题": 1.5, "中证全指电网": 0.97},
        "concept": {"电网设备": -0.58},
        "industry": {},
    }
    result = resolve_sector_quote(
        "电网设备",
        boards,
        quote_label="中证电网设备",
    )
    assert result.confidence == "high"
    assert result.matched_name == "中证电网设备"
    assert result.change_percent == 1.59


def test_resolve_ai_related_board_uses_csi_index_not_concept():
    boards = {
        "index": {"中证人工智能": -0.83},
        "concept": {"人工智能": -0.81},
        "industry": {},
    }
    result = resolve_sector_quote(
        "人工智能",
        boards,
        quote_label="中证人工智能",
    )
    assert result.confidence == "high"
    assert result.matched_name == "中证人工智能"
    assert result.change_percent == -0.83


def test_resolve_ai_related_board_without_index_blocks_concept_homonym():
    boards = {
        "index": {"新兴成指人工智能": 2.8},
        "concept": {"人工智能": -0.81},
        "industry": {},
    }
    result = resolve_sector_quote("人工智能", boards, quote_label="中证人工智能")
    assert result.confidence == "none"
    assert "中证人工智能" in (result.message or "")


def test_resolve_grid_related_board_without_index_blocks_concept_homonym():
    """养基宝：电网设备 ETF 联接应走中证电网设备指数，不能误用概念板块「电网设备」。"""
    boards = {
        "index": {"电力设备主题": 1.5},
        "concept": {"电网设备": -2.26},
        "industry": {},
    }
    result = resolve_sector_quote("电网设备", boards, quote_label="中证电网设备")
    assert result.confidence == "none"
    assert "中证电网设备" in (result.message or "")


def test_resolve_grid_related_board_without_index_uses_concept_when_no_canonical():
    boards = {
        "index": {"电力设备主题": 1.5},
        "concept": {"电网设备": 0.42},
        "industry": {},
    }
    result = resolve_sector_quote("某自定义主题", boards)
    assert result.confidence == "none"


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
