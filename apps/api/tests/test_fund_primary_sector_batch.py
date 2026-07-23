from __future__ import annotations

from app.services import fund_primary_sector_service as service


def test_primary_sector_batch_context_loads_each_store_once(monkeypatch) -> None:
    user_calls: list[set[str]] = []
    global_calls: list[set[str]] = []

    def load_user(codes: set[str]):
        user_calls.append(set(codes))
        return {
            "000001": {
                "fund_code": "000001",
                "sector_name": "半导体",
                "source": "manual",
            }
        }

    def load_global(codes: set[str]):
        global_calls.append(set(codes))
        return {}

    monkeypatch.setattr(service, "get_fund_primary_sectors_by_codes", load_user)
    monkeypatch.setattr(
        service,
        "get_fund_primary_sectors_global_by_codes",
        load_global,
    )
    monkeypatch.setattr(
        service,
        "get_fund_primary_sector",
        lambda _code: (_ for _ in ()).throw(
            AssertionError("batch load must not issue point queries")
        ),
    )

    context = service.PrimarySectorBatchContext.load(
        ["1", "000001", "000002", "not-a-code", "000000"]
    )

    assert user_calls == [{"000001", "000002"}]
    assert global_calls == [{"000001", "000002"}]
    assert context.user_row("1")["sector_name"] == "半导体"
    assert context.user_row("2") is None
