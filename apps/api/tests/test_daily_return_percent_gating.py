"""daily_return_percent 计算门槛修复回归（2026-07-04）。

根因：`persist_holdings_after_sector_refresh` 与 `adjust_holding_in_portfolio` 此前用
`total_assets > daily_profit > 0` 做门槛——要求 daily_profit 严格大于 0，导致平盘
（daily_profit=0）或亏损（daily_profit<0）的交易日 daily_return_percent 被永久写成
None，而不是正确算出 0 或负的收益率。正确写法应只要求分母（昨日结算总资产）为正，
参照 `official_nav_settlement.py::_persist_settlement_holdings` 的既有正确实现。

注：`enrich_holdings_estimates`（两条路径共用）会按 `daily_return_percent`（费率）+
`daily_return_percent_source=official_nav` 重新算出 `daily_profit`，不会直接采用调用方
传入的 `daily_profit` 字段本身；因此下面用「费率」而非「金额」驱动测试场景，
更贴合真实代码路径。
"""

from __future__ import annotations

from app.models import AdjustHoldingRequest, Holding, PortfolioSummary


def _make_holding(*, fund_code: str, amount: float, daily_return_percent: float) -> Holding:
    return Holding(
        fund_code=fund_code,
        fund_name=f"银河创新成长{fund_code}",
        holding_amount=amount,
        settled_holding_amount=amount,
        sector_name="半导体",
        sector_return_percent=daily_return_percent,
        daily_return_percent=daily_return_percent,
        daily_return_percent_source="official_nav",
    )


def _patch_persist_deps(monkeypatch, captured: dict) -> None:
    monkeypatch.setattr(
        "app.services.portfolio_persistence.get_most_recent_portfolio_snapshot",
        lambda: None,
    )
    monkeypatch.setattr(
        "app.services.transaction_ledger.confirm_and_compute_overrides",
        lambda _holdings: {},
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.sync_holding_amounts_from_shares",
        lambda holdings, **_kwargs: holdings,
    )
    monkeypatch.setattr("app.services.portfolio_persistence.get_portfolio_summary", lambda: None)

    def fake_save_summary(summary):
        captured["summary"] = summary
        return summary

    monkeypatch.setattr(
        "app.services.portfolio_persistence.save_portfolio_summary", fake_save_summary
    )
    monkeypatch.setattr(
        "app.services.portfolio_persistence.save_daily_snapshot", lambda *_a, **_k: None
    )
    monkeypatch.setattr("app.database.get_fund_profile_by_code", lambda _code: None)
    monkeypatch.setattr(
        "app.services.portfolio_persistence.persist_intraday_curve", lambda *_a, **_k: None
    )


def test_persist_holdings_computes_return_percent_on_loss_day(monkeypatch) -> None:
    """亏损日（费率<0）应该算出负的 daily_return_percent，而不是 None。"""
    from app.services.portfolio_persistence import persist_holdings_after_sector_refresh

    holding = _make_holding(fund_code="519674", amount=10000.0, daily_return_percent=-1.5)
    captured: dict = {}
    _patch_persist_deps(monkeypatch, captured)

    persist_holdings_after_sector_refresh([holding], with_official_nav=False)

    summary: PortfolioSummary = captured["summary"]
    assert summary.daily_profit == -150.0
    assert summary.daily_return_percent == -1.5


def test_persist_holdings_computes_zero_percent_on_flat_day(monkeypatch) -> None:
    """平盘日（费率==0）应该算出 0.0，而不是 None。"""
    from app.services.portfolio_persistence import persist_holdings_after_sector_refresh

    holding = _make_holding(fund_code="519674", amount=10000.0, daily_return_percent=0.0)
    captured: dict = {}
    _patch_persist_deps(monkeypatch, captured)

    persist_holdings_after_sector_refresh([holding], with_official_nav=False)

    summary: PortfolioSummary = captured["summary"]
    assert summary.daily_profit == 0.0
    assert summary.daily_return_percent == 0.0


def test_persist_holdings_still_computes_positive_percent_on_gain_day(monkeypatch) -> None:
    """回归防护：修复不能破坏原本就正常工作的盈利日场景。"""
    from app.services.portfolio_persistence import persist_holdings_after_sector_refresh

    holding = _make_holding(fund_code="519674", amount=10000.0, daily_return_percent=2.0)
    captured: dict = {}
    _patch_persist_deps(monkeypatch, captured)

    persist_holdings_after_sector_refresh([holding], with_official_nav=False)

    summary: PortfolioSummary = captured["summary"]
    assert summary.daily_profit == 200.0
    assert summary.daily_return_percent == 2.0


def test_adjust_holding_computes_return_percent_on_loss_day(monkeypatch) -> None:
    """holding_adjust_service 同一个 bug 的回归：手动调整持仓时亏损日也应算出收益率。

    `AdjustHoldingRequest` 的 `holding_profit`/`holding_return_percent` 调的是"结算持有
    收益"（累计概念），不是"当日收益"；当日收益率/收益额由持仓自身的
    `daily_return_percent` + `daily_return_percent_source=official_nav` 驱动，走的是
    与上面 persist_holdings 测试相同的 `enrich_holdings_estimates` 重算逻辑。这里传入
    `settled_holding_amount` 触发一次无关字段调整，验证当日收益率不会被错误清空。
    """
    from datetime import datetime, timezone

    from app.database import save_portfolio_daily_snapshot
    from app.models import PortfolioDailySnapshot
    from app.services.holding_adjust_service import adjust_holding_in_portfolio

    holding = _make_holding(fund_code="519674", amount=10000.0, daily_return_percent=-1.5)
    save_portfolio_daily_snapshot(
        PortfolioDailySnapshot(
            snapshot_date="2026-07-04",
            total_assets=10000.0,
            holdings=[holding.model_dump()],
            captured_at=datetime.now(timezone.utc),
        )
    )

    monkeypatch.setattr(
        "app.services.holding_adjust_service.get_fund_profile_by_code", lambda _code: None
    )
    monkeypatch.setattr(
        "app.services.holding_adjust_service.build_portfolio_holdings_response",
        lambda holdings, **_kwargs: {"holdings": [h.model_dump() for h in holdings]},
    )

    captured: dict = {}

    def fake_save_summary(summary):
        captured["summary"] = summary
        return summary

    monkeypatch.setattr(
        "app.services.holding_adjust_service.save_portfolio_summary", fake_save_summary
    )
    monkeypatch.setattr(
        "app.services.holding_adjust_service.save_daily_snapshot", lambda *_a, **_k: None
    )

    adjust_holding_in_portfolio(
        "519674",
        AdjustHoldingRequest(settled_holding_amount=10000.0),
    )

    summary: PortfolioSummary = captured["summary"]
    assert summary.daily_profit == -150.0
    assert summary.daily_return_percent == -1.5


def test_adjust_holding_computes_zero_percent_on_flat_day(monkeypatch) -> None:
    from datetime import datetime, timezone

    from app.database import save_portfolio_daily_snapshot
    from app.models import PortfolioDailySnapshot
    from app.services.holding_adjust_service import adjust_holding_in_portfolio

    holding = _make_holding(fund_code="519674", amount=10000.0, daily_return_percent=0.0)
    save_portfolio_daily_snapshot(
        PortfolioDailySnapshot(
            snapshot_date="2026-07-04",
            total_assets=10000.0,
            holdings=[holding.model_dump()],
            captured_at=datetime.now(timezone.utc),
        )
    )

    monkeypatch.setattr(
        "app.services.holding_adjust_service.get_fund_profile_by_code", lambda _code: None
    )
    monkeypatch.setattr(
        "app.services.holding_adjust_service.build_portfolio_holdings_response",
        lambda holdings, **_kwargs: {"holdings": [h.model_dump() for h in holdings]},
    )

    captured: dict = {}

    def fake_save_summary(summary):
        captured["summary"] = summary
        return summary

    monkeypatch.setattr(
        "app.services.holding_adjust_service.save_portfolio_summary", fake_save_summary
    )
    monkeypatch.setattr(
        "app.services.holding_adjust_service.save_daily_snapshot", lambda *_a, **_k: None
    )

    adjust_holding_in_portfolio(
        "519674",
        AdjustHoldingRequest(settled_holding_amount=10000.0),
    )

    summary: PortfolioSummary = captured["summary"]
    assert summary.daily_profit == 0.0
    assert summary.daily_return_percent == 0.0
