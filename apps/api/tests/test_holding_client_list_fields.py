from datetime import date

from app.models import FundProfile, Holding
from app.request_context import reset_request_user_id, set_request_user_id
from app.services.holding_client import serialize_holding_for_client
from app.services.holding_detail_cache import (
    bump_holding_detail_cache_generation,
    holding_detail_fingerprint,
    save_cached_holding_detail,
)
from app.services.holding_detail_service import resolve_holding_list_metrics
from app.services.portfolio_holdings_service import _fast_serialize_holding_for_client


def _holding() -> Holding:
    return Holding(
        fund_code="110022",
        fund_name="易方达消费行业股票",
        holding_amount=3513.5,
        return_percent=8.42,
        settled_holding_amount=3513.5,
    )


def test_serialize_includes_shares_cost_and_days() -> None:
    profile = FundProfile(
        fund_code="110022",
        fund_name="易方达消费行业股票",
        holding_shares=4177.76,
        holding_cost=0.8378,
        first_purchase_date="2026-08-02",
    )

    payload = serialize_holding_for_client(_holding(), profile=profile)

    assert payload["holding_shares"] == 4177.76
    assert payload["holding_cost"] == 0.8378
    assert payload["holding_days"] == (date.today() - date.fromisoformat("2026-08-02")).days


def test_fast_serialize_reads_profile_metrics() -> None:
    profile = FundProfile(
        fund_code="110022",
        fund_name="易方达消费行业股票",
        holding_shares=4177.76,
        holding_cost=0.8378,
        first_purchase_date="2026-08-02",
    )

    payload = _fast_serialize_holding_for_client(_holding(), profile=profile)

    assert payload["holding_shares"] == 4177.76
    assert payload["holding_cost"] == 0.8378
    assert payload["holding_days"] == (date.today() - date.fromisoformat("2026-08-02")).days


def test_list_metrics_infer_shares_from_cached_nav(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.fund_nav_service.get_latest_unit_nav",
        lambda _code, *, allow_fetch=True: 0.841,
    )

    shares, cost, days = resolve_holding_list_metrics(_holding(), profile=None)

    assert shares == 4177.76
    assert cost == 0.7757
    assert days is None


def test_list_metrics_infer_shares_from_nav_history_cache(monkeypatch) -> None:
    from app.models import FundNavHistory, FundNavPoint

    monkeypatch.setattr(
        "app.services.fund_nav_service.get_latest_unit_nav",
        lambda _code, *, allow_fetch=True: None,
    )
    monkeypatch.setattr(
        "app.services.fund_data.FundDataService.get_nav_history",
        lambda self, _code, _name="", *, trading_days=90, cache_only=False: FundNavHistory(
            fund_code="110022",
            fund_name="易方达消费行业股票",
            source="akshare",
            latest_nav=0.841,
            latest_date="2026-08-14",
            points=[FundNavPoint(date="2026-08-14", nav=0.841)],
        ),
    )

    shares, cost, days = resolve_holding_list_metrics(_holding(), profile=None)

    assert shares == 4177.76
    assert cost == 0.7757
    assert days is None


def test_list_metrics_prefer_detail_cache() -> None:
    token = set_request_user_id(42)
    bump_holding_detail_cache_generation()
    try:
        holding = _holding()
        save_cached_holding_detail(
            holding.fund_code,
            holding_detail_fingerprint(
                fund_code=holding.fund_code,
                holding_amount=3513.5,
            ),
            {
                "holding_shares": 4000.0,
                "holding_cost": 0.8,
                "holding_days": 21,
            },
        )
        shares, cost, days = resolve_holding_list_metrics(holding, profile=None)
        assert shares == 4000.0
        assert cost == 0.8
        assert days == 21
    finally:
        reset_request_user_id(token)
        bump_holding_detail_cache_generation()
