from app.services import index_daily_client
from app.services.index_daily_client import fetch_index_daily_history, index_display_name


def test_index_display_name():
    assert index_display_name("000300") == "沪深300"


def test_fetch_index_daily_history_parses_rows(monkeypatch):
    class FakeResponse:
        text = """[
            {"day":"2026-03-05","close":"3900.1"},
            {"day":"2026-03-06","close":"3910.2"}
        ]"""

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "app.services.index_daily_client.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )
    index_daily_client._INDEX_TTL_CACHE.clear()
    index_daily_client._fetch_index_daily_history_impl.cache_clear()
    result = fetch_index_daily_history("000300", 30)
    assert result is not None
    assert result["source"] == "sina"
    assert len(result["data"]) == 2
    assert result["data"][0]["date"] == "2026-03-05"
