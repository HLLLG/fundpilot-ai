def _settings(
    *,
    relay_url=None,
    browser_command=None,
    browser_enabled=False,
):
    class Settings:
        sector_quotes_enabled = True
        sector_quotes_ttl_seconds = 60
        sector_quotes_relay_url = relay_url
        sector_quotes_relay_timeout_seconds = 2.5
        sector_quotes_browser_command = browser_command
        sector_quotes_browser_enabled = browser_enabled
        sector_quotes_browser_timeout_seconds = 4.0

    return Settings()


def test_fetch_spot_boards_result_uses_short_eastmoney_timeout(monkeypatch):
    from app.services import sector_quote_provider as provider

    calls = []

    monkeypatch.setattr(provider, "get_settings", lambda: _settings())
    monkeypatch.setattr(provider, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(provider, "save_spot_snapshot", lambda *a, **k: None)

    def fake_eastmoney(**kwargs):
        calls.append(kwargs)
        return {"concept": {"商业航天": 1.2}, "industry": {}, "index": {}}

    monkeypatch.setattr(provider, "fetch_eastmoney_boards", fake_eastmoney)
    monkeypatch.setattr(provider, "fetch_boards_via_relay", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )

    result = provider.fetch_spot_boards_result(force_refresh=True, timeout_seconds=5.0)

    assert result.provider_path == "eastmoney_live"
    assert result.from_stale_cache is False
    assert calls == [{"timeout": 0.5, "max_retries": 1, "max_hosts": 1}]


def test_fetch_spot_boards_result_prefers_relay_when_eastmoney_fails(monkeypatch):
    from app.services import sector_quote_provider as provider

    monkeypatch.setattr(provider, "get_settings", lambda: _settings(relay_url="http://relay.test"))
    monkeypatch.setattr(provider, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(provider, "save_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_relay",
        lambda **_: {"concept": {"商业航天": 1.8}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )

    result = provider.fetch_spot_boards_result(force_refresh=True, timeout_seconds=5.0)

    assert result.provider_path == "relay_live"
    assert result.boards["concept"]["商业航天"] == 1.8


def test_fetch_spot_boards_result_prefers_browser_command_after_relay(monkeypatch):
    from app.services import sector_quote_provider as provider

    monkeypatch.setattr(
        provider,
        "get_settings",
        lambda: _settings(browser_enabled=True, browser_command="node relay.js"),
    )
    monkeypatch.setattr(provider, "get_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(provider, "save_spot_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(provider, "fetch_boards_via_relay", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {"半导体": 4.2}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )

    result = provider.fetch_spot_boards_result(force_refresh=True, timeout_seconds=5.0)

    assert result.provider_path == "browser_live"
    assert result.boards["industry"]["半导体"] == 4.2


def test_fetch_spot_boards_result_returns_stale_cache_with_metadata(monkeypatch):
    from app.services import sector_quote_provider as provider

    stale = {
        "concept": {
            "旧板块A": 0.5,
            "旧板块B": 0.4,
            "旧板块C": 0.3,
            "旧板块D": 0.2,
            "旧板块E": 0.1,
            "旧板块F": 0.05,
            "旧板块G": 0.04,
            "旧板块H": 0.03,
        },
        "industry": {},
        "index": {},
    }

    monkeypatch.setattr(provider, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        provider,
        "get_spot_snapshot",
        lambda _key, ttl_seconds: None if ttl_seconds == 60 else stale,
    )
    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(provider, "fetch_boards_via_relay", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )

    result = provider.fetch_spot_boards_result(force_refresh=True, timeout_seconds=0.1)

    assert result.boards == stale
    assert result.provider_path == "stale_cache"
    assert result.from_stale_cache is True
    assert result.live_attempted is True


def test_fetch_spot_boards_result_ignores_sparse_stale_cache(monkeypatch):
    from app.services import sector_quote_provider as provider

    sparse = {"concept": {"旧板块": 0.5}, "industry": {"旧行业": 0.3}, "index": {}}

    monkeypatch.setattr(provider, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        provider,
        "get_spot_snapshot",
        lambda _key, ttl_seconds: None if ttl_seconds == 60 else sparse,
    )
    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(provider, "fetch_boards_via_relay", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )

    result = provider.fetch_spot_boards_result(force_refresh=True, timeout_seconds=0.1)

    assert result.provider_path == "empty"
    assert result.from_stale_cache is False
    assert result.boards == {"concept": {}, "industry": {}, "index": {}}


def test_fetch_live_boards_skips_akshare_when_budget_is_exhausted(monkeypatch):
    from app.services import sector_quote_provider as provider

    akshare_called = False

    def fake_akshare(**kwargs):
        nonlocal akshare_called
        akshare_called = True
        return {"concept": {"不应调用": 1.0}, "industry": {}, "index": {}}

    monkeypatch.setattr(provider, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(provider, "fetch_boards_via_relay", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(provider, "fetch_boards_via_akshare", fake_akshare)

    result = provider._fetch_live_boards(timeout_seconds=0.01)

    assert result.boards == {"index": {}, "concept": {}, "industry": {}}
    assert result.provider_path == "empty"
    assert akshare_called is False


def test_fetch_live_boards_skips_akshare_for_frontend_budget(monkeypatch):
    from app.services import sector_quote_provider as provider

    akshare_called = False

    def fake_akshare(**kwargs):
        nonlocal akshare_called
        akshare_called = True
        return {"concept": {"不应调用": 1.0}, "industry": {}, "index": {}}

    monkeypatch.setattr(provider, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(provider, "fetch_boards_via_relay", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(provider, "fetch_boards_via_akshare", fake_akshare)

    result = provider._fetch_live_boards(timeout_seconds=5.0)

    assert result.provider_path == "empty"
    assert akshare_called is False


def test_fetch_live_boards_fills_missing_via_akshare(monkeypatch):
    from app.services import sector_quote_provider as provider

    monkeypatch.setattr(provider, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **kwargs: {
            "concept": {},
            "industry": {},
            "index": {"人工智能": 5.5},
        },
    )
    monkeypatch.setattr(provider, "fetch_boards_via_relay", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {"商业航天": 2.5}, "industry": {"半导体": 4.2}, "index": {}},
    )

    boards = provider._fetch_live_boards().boards

    assert boards["index"]["人工智能"] == 5.5
    assert boards["concept"]["商业航天"] == 2.5
    assert boards["industry"]["半导体"] == 4.2


def test_fetch_live_boards_uses_akshare_when_everything_else_is_empty(monkeypatch):
    from app.services import sector_quote_provider as provider

    monkeypatch.setattr(provider, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **kwargs: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(provider, "fetch_boards_via_relay", lambda **_: {"concept": {}, "industry": {}, "index": {}})
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_browser_command",
        lambda **_: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {"商业航天": 2.5}, "industry": {}, "index": {"人工智能": 5.5}},
    )

    boards = provider._fetch_live_boards().boards

    assert boards["concept"]["商业航天"] == 2.5
    assert boards["index"]["人工智能"] == 5.5
