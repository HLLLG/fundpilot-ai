import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from app.services.fund_nav_service import get_official_nav_return, _NAV_CACHE


@pytest.fixture(autouse=True)
def clear_cache():
    _NAV_CACHE.clear()
    yield
    _NAV_CACHE.clear()


def _make_nav_df(date_str: str, nav: float, growth: float) -> pd.DataFrame:
    return pd.DataFrame({
        "净值日期": [date_str],
        "单位净值": [nav],
        "日增长率": [growth],
    })


def test_returns_nav_when_today_matches():
    with patch("app.services.fund_nav_service._fetch_nav_df") as mock_fetch:
        mock_fetch.return_value = _make_nav_df("2026-06-05", 1.234, -2.45)
        result = get_official_nav_return("015945", "2026-06-05")
    assert result == pytest.approx(-2.45)


def test_returns_none_when_date_does_not_match():
    with patch("app.services.fund_nav_service._fetch_nav_df") as mock_fetch:
        mock_fetch.return_value = _make_nav_df("2026-06-04", 1.234, -2.45)
        result = get_official_nav_return("015945", "2026-06-05")
    assert result is None


def test_returns_none_when_akshare_raises():
    with patch("app.services.fund_nav_service._fetch_nav_df") as mock_fetch:
        mock_fetch.side_effect = Exception("network error")
        result = get_official_nav_return("015945", "2026-06-05")
    assert result is None


def test_returns_none_when_df_empty():
    with patch("app.services.fund_nav_service._fetch_nav_df") as mock_fetch:
        mock_fetch.return_value = pd.DataFrame()
        result = get_official_nav_return("015945", "2026-06-05")
    assert result is None


def test_caches_positive_result():
    with patch("app.services.fund_nav_service._fetch_nav_df") as mock_fetch:
        mock_fetch.return_value = _make_nav_df("2026-06-05", 1.234, -2.45)
        get_official_nav_return("015945", "2026-06-05")
        get_official_nav_return("015945", "2026-06-05")
    assert mock_fetch.call_count == 1


def test_does_not_cache_none_result_indefinitely():
    """None result is cached only for TTL_MISS seconds, not forever."""
    with patch("app.services.fund_nav_service._fetch_nav_df") as mock_fetch:
        mock_fetch.return_value = _make_nav_df("2026-06-04", 1.234, -2.45)
        get_official_nav_return("015945", "2026-06-05")
        # Manually expire the cache entry
        key = "015945:2026-06-05"
        _NAV_CACHE[key] = (_NAV_CACHE[key][0], 0)  # set expires_at=0 (already expired)
        get_official_nav_return("015945", "2026-06-05")
    assert mock_fetch.call_count == 2
