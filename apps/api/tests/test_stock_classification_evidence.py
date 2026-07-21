from __future__ import annotations

from typing import Any


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _disable_cache(monkeypatch, service) -> list[tuple[str, dict[str, Any]]]:
    saved: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(service, "get_spot_snapshot", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "get_spot_snapshot_any_age", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        service,
        "save_spot_snapshot",
        lambda key, payload: saved.append((key, payload)),
    )
    return saved


def test_stock_industry_client_requests_only_minimal_fields(monkeypatch) -> None:
    from app.services import stock_classification_evidence as service

    saved = _disable_cache(monkeypatch, service)
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, _url: str, *, params: dict[str, Any]):
            calls.append(dict(params))
            return _Response(
                {
                    "data": {
                        "f57": "688409",
                        "f58": "富创精密",
                        "f127": "半导体",
                        "f198": "BK1036",
                    }
                }
            )

    monkeypatch.setattr(service.httpx, "Client", FakeClient)
    result = service.fetch_current_stock_industry_evidence(
        [{"security_code": "688409", "security_name": "富创精密"}],
    )

    evidence = result["688409"]
    assert evidence["value"] == "半导体"
    assert evidence["pit_qualified"] is True
    assert evidence["available_at"]
    assert len(evidence["ref_id"]) == 64
    assert calls[0]["fields"] == "f57,f58,f127,f198"
    assert saved[0][0].endswith("688409")


def test_board_constituent_client_captures_auditable_membership(monkeypatch) -> None:
    from app.services import stock_classification_evidence as service

    _disable_cache(monkeypatch, service)
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def get(self, _url: str, *, params: dict[str, Any]):
            calls.append(dict(params))
            return _Response(
                {
                    "data": {
                        "diff": [
                            {"f12": "688409", "f13": 1, "f14": "富创精密"},
                            {"f12": "688120", "f13": 1, "f14": "华海清科"},
                        ]
                    }
                }
            )

    monkeypatch.setattr(service.httpx, "Client", FakeClient)
    result = service.fetch_current_board_constituent_evidence(["BK1326"])

    evidence = result["BK1326"]
    assert evidence["codes"] == ["688120", "688409"]
    assert evidence["pit_qualified"] is True
    assert evidence["source"] == "eastmoney_push2_clist_board_members"
    assert calls[0]["fs"] == "b:BK1326 f:!50"
    assert calls[0]["fields"] == "f12,f13,f14"


def test_holdings_infer_background_persists_global_without_user_context(
    monkeypatch,
) -> None:
    from app.services import fund_primary_sector_service as service
    from app.services.fund_holdings_sector_infer import HoldingStockRow

    stocks = [
        HoldingStockRow(
            name=f"银行股{index}",
            stock_code=f"60000{index}",
            weight=20.0,
            industry="银行",
            industry_available_at="2026-07-21T09:00:00+08:00",
            industry_source="eastmoney_push2_stock_get_f127",
            industry_ref_id=f"industry-{index}",
            industry_pit_qualified=True,
            coverage={"portfolio_weight_coverage_percent": 60.0},
        )
        for index in range(1, 4)
    ]
    promoted = []
    monkeypatch.setattr(service, "try_get_request_user_id", lambda: None)
    monkeypatch.setattr(
        service,
        "get_fund_primary_sector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("background inference must not read a per-user row")
        ),
    )
    monkeypatch.setattr(
        service,
        "promote_record_to_global",
        lambda record: promoted.append(record) or {"sector_name": record.sector_name},
    )

    record = service._resolve_from_holdings_infer(
        "000001",
        persist=True,
        stocks=stocks,
    )
    assert record is not None
    assert record.sector_name == "银行"
    assert len(promoted) == 1


def test_full_refresh_replaces_existing_name_inferred_sector(monkeypatch) -> None:
    from app.models import Holding
    from app.services import fund_holdings_sector_infer as holdings_service
    from app.services import fund_primary_sector_service as service
    from app.services.fund_primary_sector_types import PrimarySectorRecord

    holding = Holding(
        fund_code="017811",
        fund_name="东方人工智能主题混合C",
        holding_amount=10_000,
        sector_name="人工智能",
    )
    monkeypatch.setattr(
        service,
        "get_fund_primary_sector",
        lambda _code: {
            "fund_code": "017811",
            "sector_name": "人工智能",
            "source": "alipay_overview",
        },
    )
    monkeypatch.setattr(
        service,
        "apply_primary_sector_to_holding",
        lambda current, **_kwargs: current,
    )
    monkeypatch.setattr(
        holdings_service,
        "fetch_portfolio_stocks_with_industry_evidence",
        lambda _code: {"stocks": []},
    )
    monkeypatch.setattr(
        service,
        "_resolve_from_holdings_infer",
        lambda *_args, **_kwargs: PrimarySectorRecord(
            fund_code="017811",
            sector_name="半导体材料",
            intraday_index_name=None,
            source="holdings_infer",
            confidence=0.92,
        ),
    )

    refreshed = service.refresh_benchmark_sectors_for_holdings(
        [holding],
        fetch_missing_benchmark=False,
        fetch_holdings_infer=True,
    )
    assert refreshed[0].sector_name == "半导体材料"
