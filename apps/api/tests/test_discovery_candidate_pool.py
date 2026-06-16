from app.models import Holding
from app.services.discovery_candidate_pool import _matches_fund_type_preference, build_candidate_pool


def test_build_candidate_pool_balanced_prefers_moderate_1y(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    rank_rows = [
        {
            "fund_code": "000001",
            "fund_name": "热门半导体A",
            "return_1y_percent": 150.0,
            "return_6m_percent": 40.0,
            "return_3m_percent": 10.0,
            "fund_scale_yi": 50.0,
        },
        {
            "fund_code": "000002",
            "fund_name": "稳健半导体B",
            "return_1y_percent": 35.0,
            "return_6m_percent": 28.0,
            "return_3m_percent": 18.0,
            "fund_scale_yi": 50.0,
        },
    ]
    pool = build_candidate_pool(
        ["半导体"],
        exclude_codes=set(),
        selection_strategy="balanced",
        fetch_rank=lambda **kwargs: rank_rows,
    )
    codes = [item["fund_code"] for item in pool if item.get("selection_reason") == "排行筛选"]
    assert codes and codes[0] == "000002"


def test_build_candidate_pool_excludes_held_codes(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.fetch_open_fund_rank",
        lambda **kwargs: [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "return_1y_percent": 20.0,
                "fund_scale_yi": 50.0,
            },
            {
                "fund_code": "015945",
                "fund_name": "测试航天",
                "return_1y_percent": 15.0,
                "fund_scale_yi": 10.0,
            },
        ],
    )
    pool = build_candidate_pool(
        ["半导体", "商业航天"],
        exclude_codes={"519674"},
        fetch_rank=lambda **kwargs: [
            {
                "fund_code": "519674",
                "fund_name": "银河创新成长",
                "return_1y_percent": 20.0,
                "fund_scale_yi": 50.0,
            },
            {
                "fund_code": "015945",
                "fund_name": "测试航天",
                "return_1y_percent": 15.0,
                "fund_scale_yi": 10.0,
            },
        ],
    )
    codes = {item["fund_code"] for item in pool}
    assert "519674" not in codes
    assert "015945" in codes


def test_build_candidate_pool_uses_seed(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    pool = build_candidate_pool(
        ["半导体"],
        exclude_codes=set(),
        fetch_rank=lambda **kwargs: [],
    )
    codes = {item["fund_code"] for item in pool}
    assert "519674" in codes


def test_build_candidate_pool_seed_uses_resolved_fund_name(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.lookup_fund_name_by_code",
        lambda code: {
            "025856": "华夏中证电网设备主题ETF发起式联接A",
            "015945": "易方达国防军工混合C",
        }.get(code.zfill(6)),
    )
    pool = build_candidate_pool(
        ["电网设备", "商业航天"],
        exclude_codes=set(),
        fetch_rank=lambda **kwargs: [],
    )
    by_code = {item["fund_code"]: item for item in pool}
    assert by_code["025856"]["fund_name"] == "华夏中证电网设备主题ETF发起式联接A"
    assert "种子基金" not in by_code["025856"]["fund_name"]
    assert by_code["015945"]["fund_name"] == "易方达国防军工混合C"


def test_build_candidate_pool_primary_sector_uses_resolved_fund_name(monkeypatch):
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.list_fund_primary_sectors",
        lambda: [{"fund_code": "010524", "sector_name": "5G"}],
    )
    monkeypatch.setattr(
        "app.services.discovery_candidate_pool.lookup_fund_name_by_code",
        lambda code: {"010524": "银华中证5G通信主题ETF联接C"}.get(code.zfill(6)),
    )
    pool = build_candidate_pool(
        ["5G"],
        exclude_codes=set(),
        fetch_rank=lambda **kwargs: [],
    )
    by_code = {item["fund_code"]: item for item in pool}
    assert by_code["010524"]["fund_name"] == "银华中证5G通信主题ETF联接C"


def test_matches_fund_type_preference():
    assert _matches_fund_type_preference("华夏半导体ETF联接A", "etf_link") is True
    assert _matches_fund_type_preference("华夏半导体ETF联接A", "no_c_class") is True
    assert _matches_fund_type_preference("某某成长混合C", "no_c_class") is False
    assert _matches_fund_type_preference("某某成长混合A", "any") is True
