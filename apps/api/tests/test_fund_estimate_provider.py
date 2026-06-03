import time

from app.models import Holding
from app.services import fund_estimate_provider as provider


def test_fetch_fund_estimate_quotes_fetches_codes_concurrently(monkeypatch):
    holdings = [
        Holding(fund_code=f"00000{index}", fund_name=f"基金{index}", holding_amount=100, return_percent=0)
        for index in range(1, 5)
    ]

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    def fake_fetch(_session, code, *, timeout_seconds):
        time.sleep(0.2)
        return {"fund_code": code, "change_percent": float(code[-1])}

    monkeypatch.setattr(provider, "_build_session", lambda: FakeSession())
    monkeypatch.setattr(provider, "_fetch_fund_estimate_quote", fake_fetch)

    start = time.perf_counter()
    result = provider.fetch_fund_estimate_quotes(holdings, timeout_seconds=5.0)
    elapsed = time.perf_counter() - start

    assert sorted(result) == ["000001", "000002", "000003", "000004"]
    assert elapsed < 0.5
