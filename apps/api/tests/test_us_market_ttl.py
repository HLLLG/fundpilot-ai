from app.services.us_market_service import _ttl_for


def test_ttl_matches_shared_refresh_cadence() -> None:
    assert _ttl_for("pre_market") == 1200.0
    assert _ttl_for("regular") == 1200.0
    assert _ttl_for("after_hours") == 1200.0
    assert _ttl_for("closed") == 10800.0
