"""Shared test constants. Do not use sk- prefix — avoids GitHub secret scanning false positives."""

import pytest

PYTEST_VALID_DEEPSEEK_KEY = "fundpilot-pytest-only-not-a-real-api-key-ok"
PYTEST_PLACEHOLDER_DEEPSEEK_KEY = "replace-me-not-a-real-deepseek-key"


@pytest.fixture(autouse=True)
def _clear_trade_calendar_cache():
    from app.services.trade_calendar_cache import get_trade_date_set

    get_trade_date_set.cache_clear()
    yield
    get_trade_date_set.cache_clear()
