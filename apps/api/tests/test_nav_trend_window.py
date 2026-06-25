"""F4 回归：summarize_nav_history window_days 在摘要时窗口化。"""

from __future__ import annotations

from app.models import FundNavHistory, FundNavPoint
from app.services.nav_trend_summary import summarize_nav_history


def _linear_history(days: int, start: float = 1.0, step: float = 0.01) -> FundNavHistory:
    """构造 days 条点序列：单位净值线性递增，便于断言区间涨跌。"""
    points = [
        FundNavPoint(
            date=f"2026-01-{i + 1:02d}",
            nav=round(start + step * i, 4),
            daily_return_percent=None,
        )
        for i in range(days)
    ]
    return FundNavHistory(
        fund_code="000001",
        fund_name="测试",
        source="akshare",
        points=points,
    )


def test_window_days_caps_to_66_by_default():
    """传 100 日 → 默认 window_days=66 → period_change 只反映末 66 日。"""
    hist = _linear_history(100)  # nav 从 1.00 线性到 1.99
    summary = summarize_nav_history(hist)  # 默认 window_days=66
    # 末 66 日：第 35 日 nav (索引 34) = 1.34，末日 (索引 99) = 1.99
    expected = round((1.99 / 1.34 - 1) * 100, 2)
    assert summary["period_days"] == 66
    assert summary["period_change_percent"] == expected


def test_window_days_explicit_override():
    """显式 window_days=30 → period_days=30。"""
    hist = _linear_history(100)
    summary = summarize_nav_history(hist, window_days=30)
    assert summary["period_days"] == 30


def test_window_days_none_keeps_full_series():
    """window_days=None → 使用全部点（向后兼容旧行为）。"""
    hist = _linear_history(100)
    summary = summarize_nav_history(hist, window_days=None)
    assert summary["period_days"] == 100


def test_window_smaller_than_points_unchanged():
    """点数少于 window_days → 全用，不截。"""
    hist = _linear_history(20)
    summary = summarize_nav_history(hist, window_days=66)
    assert summary["period_days"] == 20


def test_recent_5d_unaffected_by_window():
    """recent_5d_change_percent 只看末 6 点，与 window 无关。"""
    hist = _linear_history(100)
    summary = summarize_nav_history(hist, window_days=66)
    # 末 6 点：索引 94→99，nav 从 1.94 到 1.99
    expected_5d = round((1.99 / 1.94 - 1) * 100, 2)
    assert summary["recent_5d_change_percent"] == expected_5d
