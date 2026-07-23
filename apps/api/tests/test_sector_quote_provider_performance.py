from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import time

from app.services import sector_quote_provider as provider


def _boards(prefix: str = "board") -> dict[str, dict[str, float]]:
    return {
        "index": {},
        "concept": {f"{prefix}-{index}": 1.0 for index in range(8)},
        "industry": {},
    }


def test_fast_providers_race_and_first_cacheable_result_wins(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "get_settings",
        lambda: SimpleNamespace(
            sector_quotes_relay_url="http://relay.invalid",
            sector_quotes_browser_enabled=False,
            sector_quotes_browser_command="",
        ),
    )

    def slow_eastmoney(**_kwargs):
        time.sleep(0.2)
        return _boards("eastmoney")

    monkeypatch.setattr(provider, "fetch_eastmoney_boards", slow_eastmoney)
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_relay",
        lambda **_kwargs: _boards("relay"),
    )

    path, boards = provider._race_fast_board_providers(
        start_time=time.time(),
        timeout_seconds=1.0,
    )

    assert path == "relay_live"
    assert boards == _boards("relay")


def test_cross_process_singleflight_rechecks_cache_before_live_refresh(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        provider,
        "get_settings",
        lambda: SimpleNamespace(
            sector_quotes_enabled=True,
            sector_quotes_ttl_seconds=240,
        ),
    )
    responses = iter((None, None, _boards("winner")))
    monkeypatch.setattr(
        provider,
        "get_spot_snapshot",
        lambda *_args, **_kwargs: next(responses),
    )

    @contextmanager
    def acquired(*_args, **_kwargs):
        yield

    monkeypatch.setattr(provider, "cross_process_lock", acquired)
    monkeypatch.setattr(
        provider,
        "_fetch_live_boards",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache winner must suppress a duplicate provider call")
        ),
    )

    result = provider.fetch_spot_boards_result(timeout_seconds=1.0)

    assert result.provider_path == "singleflight_cache"
    assert result.live_attempted is False
    assert result.boards == _boards("winner")
