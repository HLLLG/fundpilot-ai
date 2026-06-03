def test_fetch_live_boards_fills_missing_via_akshare(monkeypatch):
    from app.services import sector_quote_provider as provider

    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **kwargs: {
            "concept": {},
            "industry": {},
            "index": {"人工智能": 5.5},
        },
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {"商业航天": 2.5}, "industry": {"半导体": 4.2}, "index": {}},
    )

    boards = provider._fetch_live_boards()
    assert boards["index"]["人工智能"] == 5.5
    assert boards["concept"]["商业航天"] == 2.5
    assert boards["industry"]["半导体"] == 4.2


def test_fetch_live_boards_uses_akshare_when_httpx_empty(monkeypatch):
    from app.services import sector_quote_provider as provider

    monkeypatch.setattr(
        provider,
        "fetch_eastmoney_boards",
        lambda **kwargs: {"concept": {}, "industry": {}, "index": {}},
    )
    monkeypatch.setattr(
        provider,
        "fetch_boards_via_akshare",
        lambda **_: {"concept": {"商业航天": 2.5}, "industry": {}, "index": {"人工智能": 5.5}},
    )

    boards = provider._fetch_live_boards()
    assert boards["concept"]["商业航天"] == 2.5
    assert boards["index"]["人工智能"] == 5.5
