"""持仓详情按用户缓存 + 板块分时后台预热。"""

from app.models import Holding
from app.services.holding_detail_cache import (
    bump_holding_detail_cache_generation,
    get_cached_holding_detail,
    holding_detail_fingerprint,
    save_cached_holding_detail,
)
from app.services.holding_intraday_warmup import collect_intraday_queries


def test_holding_detail_cache_hit_and_miss(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_detail_cache.get_request_user_id",
        lambda: 42,
    )
    bump_holding_detail_cache_generation()

    payload = {"holding": {"fund_code": "008586", "fund_name": "测试"}}
    fp = holding_detail_fingerprint(fund_code="008586", holding_amount=1000.0)
    assert get_cached_holding_detail("008586", fp) is None

    save_cached_holding_detail("008586", fp, payload)
    assert get_cached_holding_detail("008586", fp) == payload

    bump_holding_detail_cache_generation()
    assert get_cached_holding_detail("008586", fp) is None


def test_collect_intraday_queries_dedupes_by_sector(monkeypatch):
    monkeypatch.setattr(
        "app.services.holding_intraday_warmup.get_fund_profile_by_code",
        lambda _code: None,
    )
    monkeypatch.setattr(
        "app.services.holding_intraday_warmup._resolve_intraday_for_holding",
        lambda holding, _profile: ("index", holding.sector_name or ""),
    )
    holdings = [
        Holding(fund_code="008586", fund_name="A", sector_name="人工智能", holding_amount=1),
        Holding(fund_code="025857", fund_name="B", sector_name="人工智能", holding_amount=2),
        Holding(fund_code="519674", fund_name="C", sector_name="半导体", holding_amount=3),
    ]
    queries = collect_intraday_queries(holdings)
    assert queries == [("index", "人工智能"), ("index", "半导体")]
