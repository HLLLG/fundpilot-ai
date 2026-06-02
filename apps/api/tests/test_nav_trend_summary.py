from app.models import FundNavHistory, FundNavPoint
from app.services.nav_trend_summary import summarize_nav_history


def _history_with_navs(navs: list[float], *, source: str = "akshare") -> FundNavHistory:
    points = [
        FundNavPoint(date=f"2026-01-{idx:02d}", nav=nav)
        for idx, nav in enumerate(navs, start=1)
    ]
    return FundNavHistory(
        fund_code="015608",
        fund_name="测试基金",
        source=source,
        points=points,
    )


def test_summarize_nav_history_computes_period_and_recent_changes():
    history = _history_with_navs([1.0, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06])
    summary = summarize_nav_history(history, recent_sample=4)

    assert summary is not None
    assert summary["period_days"] == 7
    assert summary["period_change_percent"] == 6.0
    assert summary["recent_5d_change_percent"] == 4.95
    assert summary["distance_from_high_percent"] == 0.0
    assert len(summary["recent_nav_series"]) == 4
    assert "上行" in summary["trend_label"] or "上升" in summary["trend_label"]


def test_summarize_nav_history_returns_none_for_error_source():
    history = _history_with_navs([1.0, 1.1], source="error")
    assert summarize_nav_history(history) is None


def test_summarize_nav_history_returns_none_without_points():
    history = FundNavHistory(
        fund_code="015608",
        fund_name="测试",
        source="akshare",
        points=[],
    )
    assert summarize_nav_history(history) is None
