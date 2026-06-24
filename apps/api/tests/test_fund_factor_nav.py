"""因子 NAV 共享 helper 测试。

设计文档：docs/superpowers/specs/2026-06-24-factor-ic-backtest-design.md 第 5 章。
"""
from __future__ import annotations

from app.services.fund_factor_nav import factor_input_from_navs, window_return_percent


def test_window_return_percent_known():
    # 1.0→1.1，window 覆盖全段 → +10%
    assert round(window_return_percent([1.0, 1.05, 1.1], 60), 2) == 10.0


def test_window_return_percent_partial_window():
    # window 小于序列长度，只看尾部 window 段
    navs = [1.0, 1.0, 1.0, 1.1]  # 近 1 段：1.0→1.1
    assert round(window_return_percent(navs, 1), 2) == 10.0


def test_window_return_percent_too_short():
    assert window_return_percent([1.0], 60) is None


def test_factor_input_rising_series_positive_momentum():
    navs = [1.0 + 0.01 * i for i in range(120)]
    fi = factor_input_from_navs("000001", "测试", navs)
    assert fi.return_3m_percent is not None and fi.return_3m_percent > 0
    assert fi.max_drawdown_1y_percent is not None
    assert fi.fund_scale_yi is None


def test_factor_input_empty_no_crash():
    fi = factor_input_from_navs("000001", "测试", [])
    assert fi.return_3m_percent is None
    assert fi.fund_code == "000001"
