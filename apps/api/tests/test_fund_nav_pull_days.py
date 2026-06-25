"""F4 回归：FundDataService 拉满 252 + 摘要窗口 66，复用持仓详情预热缓存。"""

from __future__ import annotations

from app.config import get_settings, refresh_settings
from app.models import FundNavHistory, FundNavPoint, FundSnapshot, Holding
from app.services.fund_data import FundDataService


def test_get_snapshots_uses_nav_cache_pull_days_default(monkeypatch):
    """默认 trading_days=None 时应取 settings.nav_cache_pull_days (默认 252)。"""
    refresh_settings()
    captured = {"trading_days": None}

    def fake_snapshot_and_trend(self, holding, *, trading_days):
        captured["trading_days"] = trading_days
        return (
            FundSnapshot(fund_code=holding.fund_code, fund_name="", source="test"),
            FundNavHistory(
                fund_code=holding.fund_code,
                fund_name="",
                source="akshare",
                points=[],
            ),
        )

    monkeypatch.setattr(
        "app.services.fund_data.FundDataService._snapshot_and_trend_for_holding",
        fake_snapshot_and_trend,
    )

    FundDataService().get_snapshots_with_nav_trends(
        [Holding(fund_code="519674", fund_name="x", holding_amount=10000)]
    )

    assert captured["trading_days"] == 252


def test_summary_uses_nav_trend_window(monkeypatch):
    """摘要应传 window_days=settings.nav_trend_window (默认 66)。"""
    points = [
        FundNavPoint(date=f"2026-01-{(i % 28) + 1:02d}", nav=1.0 + i * 0.01)
        for i in range(100)
    ]
    hist = FundNavHistory(
        fund_code="519674",
        fund_name="x",
        source="akshare",
        points=points,
    )

    def fake_snapshot(self, holding, *, trading_days):
        return FundSnapshot(fund_code=holding.fund_code, fund_name="", source="test"), hist

    monkeypatch.setattr(
        "app.services.fund_data.FundDataService._snapshot_and_trend_for_holding",
        fake_snapshot,
    )

    _snapshots, trends = FundDataService().get_snapshots_with_nav_trends(
        [Holding(fund_code="519674", fund_name="x", holding_amount=10000)]
    )

    summary = trends["519674"]
    assert summary["period_days"] == 66  # window 已生效


def test_legacy_nav_trend_days_property_maps_to_new_setting():
    """旧 settings.nav_trend_days property 仍可读，等于 nav_cache_pull_days。"""
    settings = get_settings()
    assert settings.nav_trend_days == settings.nav_cache_pull_days
    assert settings.nav_trend_days == 252
    assert settings.nav_trend_window == 66
