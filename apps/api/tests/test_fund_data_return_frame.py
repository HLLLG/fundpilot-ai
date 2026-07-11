import math

import pandas as pd

from app.services.fund_data import _parse_return_frame


def _frame(values: list[object]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "净值日期": [f"2026-01-{index + 1:02d}" for index in range(len(values))],
            "累计收益率": values,
        }
    )


def test_parse_return_frame_treats_values_as_cumulative_percentages():
    result = _parse_return_frame(_frame([1.0, 21.0]))
    assert result["return_1y_percent"] == 19.8
    assert result["max_drawdown_1y_percent"] == 0.0


def test_parse_return_frame_computes_drawdown_on_growth_index():
    result = _parse_return_frame(_frame([0.0, 20.0, 10.0]))
    assert result["return_1y_percent"] == 10.0
    assert result["max_drawdown_1y_percent"] == -8.33


def test_parse_return_frame_handles_crossing_zero_cumulative_return():
    result = _parse_return_frame(_frame([-10.0, 10.0]))
    assert result["return_1y_percent"] == 22.22
    assert result["max_drawdown_1y_percent"] == 0.0


def test_parse_return_frame_skips_invalid_growth_indices_and_non_finite_values():
    result = _parse_return_frame(_frame([-100.0, math.nan, "bad", 0.0, 10.0]))
    assert result == {
        "return_1y_percent": 10.0,
        "max_drawdown_1y_percent": 0.0,
    }


def test_parse_return_frame_returns_empty_when_fewer_than_two_valid_points():
    assert _parse_return_frame(_frame([-100.0, "bad", 10.0])) == {}


def test_parse_return_frame_rejects_excessive_computed_return():
    assert _parse_return_frame(_frame([-99.0, 20.0])) == {}


def test_parse_return_frame_rejects_non_finite_computed_return():
    near_total_loss = math.nextafter(-100.0, math.inf)
    assert _parse_return_frame(_frame([near_total_loss, 1e308])) == {}
