from app.models import Holding
from app.services.discovery_candidate_pool import build_candidate_pool


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
