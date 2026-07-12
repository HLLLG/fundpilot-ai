from types import SimpleNamespace

from app.services.factor_ic_backtest import NavPoint, _aggregate, compute_factor_ic
from app.services.fund_factor_nav import (
    build_total_return_index,
    factor_input_from_points,
)


def test_daily_growth_prevents_dividend_drop_from_becoming_fake_loss() -> None:
    series = build_total_return_index(
        [
            {"date": "2026-01-01", "nav": 2.0, "daily_growth": 0.0},
            # 单位净值因分红下降 50%，但含分红日收益为 0。
            {"date": "2026-01-02", "nav": 1.0, "daily_growth": 0.0},
            {"date": "2026-01-03", "nav": 1.1, "daily_growth": 10.0},
        ]
    )
    assert [round(value, 4) for _, value in series.points] == [1.0, 1.0, 1.1]
    assert series.daily_return_points == 2
    assert series.nav_ratio_points == 0


def test_total_return_falls_back_to_nav_ratio_when_growth_missing() -> None:
    series = build_total_return_index(
        [
            {"date": "2026-01-01", "nav": 1.0},
            {"date": "2026-01-02", "nav": 1.1},
        ]
    )
    assert round(series.points[-1][1], 4) == 1.1
    assert series.nav_ratio_points == 1


def test_online_factor_input_uses_same_total_return_reconstruction() -> None:
    points = [
        SimpleNamespace(date=f"2026-01-{index:02d}", nav=1.0, daily_return_percent=1.0)
        for index in range(1, 10)
    ]
    result = factor_input_from_points("000001", "测试", points)
    assert result.return_3m_percent is not None
    assert result.return_3m_percent > 8


def test_complete_online_factor_window_is_capped_to_offline_lookback() -> None:
    points = [
        SimpleNamespace(
            date=f"D{index:04d}",
            nav=1.0,
            daily_return_percent=(10.0 if index < 20 else 0.1),
        )
        for index in range(270)
    ]
    result = factor_input_from_points(
        "000001",
        "测试",
        points,
        require_complete=True,
        minimum_points=250,
    )
    assert result.return_1y_percent is not None
    assert result.return_1y_percent < 40


def test_ic_requires_complete_factor_window() -> None:
    calendar = [f"D{index:04d}" for index in range(320)]
    panel = {
        f"{fund:06d}": [
            NavPoint(day, (1.0 + fund * 0.0001) ** offset)
            for offset, day in enumerate(calendar[-100:])
        ]
        for fund in range(1, 20)
    }
    result = compute_factor_ic(
        nav_panel=panel,
        calendar=calendar,
        factor_lookback=250,
    )
    assert all(row.n_periods == 0 for row in result.factors)


def test_newey_west_fields_and_oos_stability_are_emitted() -> None:
    stats = _aggregate(
        "momentum",
        [0.04, 0.06, 0.03, 0.05, 0.02, 0.07] * 6,
        hac_lags=2,
    )
    assert stats.standard_error is not None
    assert stats.ci_low is not None
    assert stats.ci_high is not None
    assert stats.oos_mean_ic is not None
    assert stats.direction_stable is True
