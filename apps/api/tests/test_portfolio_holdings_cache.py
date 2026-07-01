from app.services.portfolio_holdings_cache import (
    bump_holdings_cache_generation,
    get_cached_holdings_response,
    get_holdings_cache_generation,
    save_cached_holdings_response,
)


def test_late_holdings_read_cannot_overwrite_newer_generation() -> None:
    old_generation = get_holdings_cache_generation()
    old_payload = {"holdings": [{"fund_code": "016665"}]}
    empty_payload = {"holdings": []}

    bump_holdings_cache_generation()
    save_cached_holdings_response(empty_payload)
    save_cached_holdings_response(old_payload, expected_generation=old_generation)

    assert get_cached_holdings_response() == empty_payload
