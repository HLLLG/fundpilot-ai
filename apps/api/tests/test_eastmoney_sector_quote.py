def test_fetch_eastmoney_sector_quote_finds_exact_name(monkeypatch):
    from app.services import eastmoney_spot_client as client

    def fake_page(_client, _params, *, max_retries):
        return {
            "diff": [
                {"f14": "商业航天", "f3": 4.88},
                {"f14": "半导体", "f3": 2.1},
            ],
            "total": 2,
        }

    monkeypatch.setattr(client, "_request_board_page", fake_page)
    change = client.fetch_eastmoney_sector_quote("商业航天", source_type="concept")
    assert change == 4.88
