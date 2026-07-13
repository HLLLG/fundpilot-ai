from app.services.portfolio_holdings_cache import (
    bump_holdings_cache_generation,
    get_cached_holdings_response,
    get_holdings_cache_generation,
    save_cached_holdings_response,
)
from app.services import portfolio_holdings_cache as cache_module


def test_late_holdings_read_cannot_overwrite_newer_generation() -> None:
    old_generation = get_holdings_cache_generation()
    old_payload = {"holdings": [{"fund_code": "016665"}]}
    empty_payload = {"holdings": []}

    bump_holdings_cache_generation()
    save_cached_holdings_response(empty_payload)
    save_cached_holdings_response(old_payload, expected_generation=old_generation)

    assert get_cached_holdings_response() == empty_payload


def test_holdings_cache_prunes_expired_entry(monkeypatch) -> None:
    monkeypatch.setattr(cache_module, "get_request_user_id", lambda: 42)
    key = "portfolio:holdings:42"
    with cache_module._MEMORY_LOCK:
        cache_module._MEMORY.clear()
        cache_module._MEMORY[key] = (
            cache_module.get_holdings_cache_generation(),
            0.0,
            {"holdings": []},
        )

    assert cache_module.get_cached_holdings_response() is None
    assert key not in cache_module._MEMORY


def test_holdings_cache_is_lru_bounded(monkeypatch) -> None:
    current_user = [1]
    monkeypatch.setattr(
        cache_module,
        "get_request_user_id",
        lambda: current_user[0],
    )
    monkeypatch.setattr(cache_module, "_MEMORY_MAX_ENTRIES", 2)
    with cache_module._MEMORY_LOCK:
        cache_module._MEMORY.clear()

    cache_module.save_cached_holdings_response({"user": 1})
    current_user[0] = 2
    cache_module.save_cached_holdings_response({"user": 2})
    current_user[0] = 1
    assert cache_module.get_cached_holdings_response() == {"user": 1}
    current_user[0] = 3
    cache_module.save_cached_holdings_response({"user": 3})

    assert list(cache_module._MEMORY) == [
        "portfolio:holdings:1",
        "portfolio:holdings:3",
    ]
