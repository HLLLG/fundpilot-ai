from app.services.portfolio_holdings_cache import (
    bump_holdings_cache_generation,
    get_cached_holdings_response,
    save_cached_holdings_response,
)


def test_holdings_cache_roundtrip():
    bump_holdings_cache_generation()
    payload = {"holdings": [], "source": "empty", "refreshed_at": None}
    save_cached_holdings_response(payload)
    assert get_cached_holdings_response() == payload


def test_holdings_cache_invalidates_after_bump():
    bump_holdings_cache_generation()
    save_cached_holdings_response({"holdings": [], "source": "empty"})
    bump_holdings_cache_generation()
    assert get_cached_holdings_response() is None


def test_holdings_cache_expires_after_ttl(monkeypatch):
    bump_holdings_cache_generation()
    save_cached_holdings_response({"holdings": [], "source": "empty"})

    import app.services.portfolio_holdings_cache as cache_module

    original = cache_module.CACHE_TTL_SECONDS
    monkeypatch.setattr(cache_module, "CACHE_TTL_SECONDS", -1.0)
    try:
        assert get_cached_holdings_response() is None
    finally:
        monkeypatch.setattr(cache_module, "CACHE_TTL_SECONDS", original)
