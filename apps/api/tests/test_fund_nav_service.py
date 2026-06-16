import pytest
import pandas as pd
from unittest.mock import patch

from app.services.fund_nav_service import get_official_nav_return, _NAV_CACHE


def _make_nav_df(date_str: str, growth: float) -> pd.DataFrame:
    return pd.DataFrame({"净值日期": [date_str], "单位净值": [1.234], "日增长率": [growth]})


def test_returns_nav_when_today_matches():
    with patch("app.services.fund_nav_service._fetch_nav_df") as mock_fetch:
        mock_fetch.return_value = _make_nav_df("2026-06-05", -2.45)
        result = get_official_nav_return("015945", "2026-06-05")
    assert result == pytest.approx(-2.45)


def test_returns_none_when_date_does_not_match():
    _NAV_CACHE.clear()
    with patch("app.services.fund_nav_service._fetch_nav_df") as mock_fetch:
        mock_fetch.return_value = _make_nav_df("2026-06-04", -2.45)
        result = get_official_nav_return("015945", "2026-06-05")
    assert result is None


def test_caches_positive_result():
    _NAV_CACHE.clear()
    with patch("app.services.fund_nav_service._fetch_nav_df") as mock_fetch:
        mock_fetch.return_value = _make_nav_df("2026-06-05", -2.45)
        get_official_nav_return("015945", "2026-06-05")
        get_official_nav_return("015945", "2026-06-05")
    assert mock_fetch.call_count == 1
    _NAV_CACHE.clear()
