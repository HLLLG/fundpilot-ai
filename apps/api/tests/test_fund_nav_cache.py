"""基金净值全局缓存 + 持仓详情三层预热。"""

from app.models import FundNavHistory, FundNavPoint, Holding, PortfolioSummary
from app.services.fund_nav_cache import get_cached_fund_nav, save_cached_fund_nav, warm_fund_nav
from app.services.holding_intraday_warmup import (
    collect_unique_fund_codes,
    warm_holding_details,
    warm_holdings_cache,
)


def test_fund_nav_cache_roundtrip(monkeypatch):
    history = FundNavHistory(
        fund_code="519674",
        fund_name="银河创新成长",
        source="akshare",
        points=[
            FundNavPoint(date="2026-06-24", nav=14.192, daily_return_percent=5.27),
        ],
        latest_nav=14.192,
        latest_date="2026-06-24",
        period_change_percent=12.5,
    )
    save_cached_fund_nav("519674", 252, history)
    cached = get_cached_fund_nav("519674", 252)
    assert cached is not None
    assert cached.latest_nav == 14.192


def test_warm_fund_nav_skips_when_cached(monkeypatch):
    save_cached_fund_nav(
        "008586",
        252,
        FundNavHistory(
            fund_code="008586",
            fund_name="测试",
            source="akshare",
            points=[FundNavPoint(date="2026-06-24", nav=1.0)],
            latest_nav=1.0,
            latest_date="2026-06-24",
        ),
    )

    def _should_not_fetch(*_args, **_kwargs):
        raise AssertionError("should not fetch when cache warm")

    monkeypatch.setattr(
        "app.services.fund_data.FundDataService.get_nav_history",
        _should_not_fetch,
    )
    assert warm_fund_nav("008586", trading_days=252) is True


def test_collect_unique_fund_codes():
    holdings = [
        Holding(fund_code="519674", fund_name="A", holding_amount=1),
        Holding(fund_code="519674", fund_name="A dup", holding_amount=2),
        Holding(fund_code="000000", fund_name="X", holding_amount=1),
        Holding(fund_code="008586", fund_name="B", holding_amount=3),
    ]
    assert collect_unique_fund_codes(holdings) == ["519674", "008586"]


def test_warm_holding_details_uses_user_context(monkeypatch):
    saved: list[tuple[str, str, dict]] = []

    monkeypatch.setattr(
        "app.services.holding_intraday_warmup.get_cached_holding_detail",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.holding_intraday_warmup.build_holding_detail",
        lambda holdings, index, **kwargs: type(
            "Detail",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "index": index,
                    "holding": holdings[index].model_dump(mode="json"),
                }
            },
        )(),
    )
    monkeypatch.setattr(
        "app.services.holding_intraday_warmup.save_cached_holding_detail",
        lambda code, fp, payload: saved.append((code, fp, payload)),
    )
    monkeypatch.setattr(
        "app.services.holding_intraday_warmup.set_request_user_id",
        lambda user_id: object(),
    )
    monkeypatch.setattr(
        "app.services.holding_intraday_warmup.reset_request_user_id",
        lambda _token: None,
    )

    holdings = [
        Holding(fund_code="519674", fund_name="A", sector_name="半导体", holding_amount=100),
    ]
    count = warm_holding_details(
        holdings,
        user_id=42,
        portfolio_summary=PortfolioSummary(total_assets=100, daily_profit=1),
    )
    assert count == 1
    assert saved[0][0] == "519674"


def test_warm_holdings_cache_runs_all_layers(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        "app.services.holding_intraday_warmup.warm_fund_nav_histories",
        lambda *_args, **_kwargs: (calls.append("nav") or 2),
    )
    monkeypatch.setattr(
        "app.services.holding_intraday_warmup.warm_holdings_intraday",
        lambda *_args, **_kwargs: (calls.append("intraday") or 1),
    )
    monkeypatch.setattr(
        "app.services.holding_intraday_warmup.warm_holding_details",
        lambda *_args, **_kwargs: (calls.append("detail") or 1),
    )

    holdings = [Holding(fund_code="519674", fund_name="A", holding_amount=1)]
    result = warm_holdings_cache(holdings, user_id=7)
    assert result == {"nav": 2, "intraday": 1, "detail": 1}
    assert calls == ["nav", "intraday", "detail"]
