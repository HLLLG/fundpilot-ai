from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest

from app.services import fund_holdings_distribution as service


def _snapshot(
    *,
    period: str,
    as_of: str,
    available_at: str,
    holdings: list[dict],
) -> dict:
    normalized_holdings = []
    for row in holdings:
        normalized = dict(row)
        code = str(normalized.get("security_code") or "")
        if len(code) == 6 and code.isdigit():
            normalized.setdefault("security_id", f"CN:{code}")
        normalized_holdings.append(normalized)
    holdings = normalized_holdings
    weight_sum = sum(float(row["weight_percent"]) for row in holdings)
    return {
        "status": "qualified",
        "qualified": True,
        "report_period": period,
        "as_of_date": as_of,
        "available_at": available_at,
        "freshness": {"label": "fresh"},
        "coverage": {
            "portfolio_weight_coverage_percent": weight_sum,
            "weight_sum_percent": weight_sum,
        },
        "holdings": holdings,
        "reason_codes": [],
    }


@pytest.fixture
def snapshots() -> tuple[dict, dict]:
    current = _snapshot(
        period="2026-Q1",
        as_of="2026-03-31",
        available_at="2026-04-23T00:00:00+08:00",
        holdings=[
            {
                "rank": 1,
                "security_code": "688012",
                "security_name": "中微公司",
                "weight_percent": 0.6,
            },
            {
                "rank": 2,
                "security_code": "002371",
                "security_name": "北方华创",
                "weight_percent": 0.52,
            },
            {
                "rank": 3,
                "security_code": "688072",
                "security_name": "拓荆科技",
                "weight_percent": 0.3,
            },
        ],
    )
    previous = _snapshot(
        period="2025-Q3",
        as_of="2025-09-30",
        available_at="2025-10-29T00:00:00+08:00",
        holdings=[
            {
                "rank": 1,
                "security_code": "688012",
                "security_name": "中微公司",
                "weight_percent": 0.4,
            },
            {
                "rank": 2,
                "security_code": "002371",
                "security_name": "北方华创",
                "weight_percent": 0.6,
            },
        ],
    )
    return current, previous


@pytest.fixture(autouse=True)
def no_live_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "_fetch_stock_quotes",
        lambda *_args, **_kwargs: {},
    )


def test_distribution_uses_stock_book_denominator_without_hiding_nav_weight(
    monkeypatch: pytest.MonkeyPatch,
    snapshots: tuple[dict, dict],
) -> None:
    current, previous = snapshots
    monkeypatch.setattr(
        service,
        "_resolve_snapshot_pair",
        lambda *_args, **_kwargs: (deepcopy(current), deepcopy(previous)),
    )
    monkeypatch.setattr(
        service,
        "_fetch_stock_allocations",
        lambda *_args, **_kwargs: {"20260331": 4.0, "20250930": 2.0},
    )
    saved: list[dict] = []
    monkeypatch.setattr(service, "save_spot_snapshot", lambda _key, value: saved.append(value))

    result = service.build_fund_holdings_distribution("020357", force_refresh=True)

    assert result["status"] == "available"
    assert result["display_weight_basis"] == "stock_position"
    assert result["stock_allocation_percent"] == 4.0
    assert result["previous_report_period"] == "2025-Q3"
    by_code = {row["security_code"]: row for row in result["holdings"]}
    assert by_code["688012"]["display_weight_percent"] == 15.0
    assert by_code["688012"]["nav_weight_percent"] == 0.6
    assert by_code["688012"]["change_direction"] == "decreased"
    assert by_code["688012"]["change_percent_points"] == -5.0
    assert by_code["002371"]["change_direction"] == "decreased"
    assert by_code["688072"]["change_direction"] == "new"
    assert saved and saved[0]["fund_code"] == result["fund_code"]
    assert "quote_change_percent" not in saved[0]["holdings"][0]
    assert result["holdings"][0]["quote_change_percent"] is None


def test_distribution_joins_one_batch_quote_snapshot_without_caching_it_as_disclosure(
    monkeypatch: pytest.MonkeyPatch,
    snapshots: tuple[dict, dict],
) -> None:
    current, previous = snapshots
    monkeypatch.setattr(
        service,
        "_resolve_snapshot_pair",
        lambda *_args, **_kwargs: (deepcopy(current), deepcopy(previous)),
    )
    monkeypatch.setattr(
        service,
        "_fetch_stock_allocations",
        lambda *_args, **_kwargs: {"20260331": 4.0, "20250930": 2.0},
    )
    quoted_at = int(
        datetime(2026, 7, 21, 15, 0, tzinfo=service.CN_TZ).timestamp()
    )
    monkeypatch.setattr(
        service,
        "_fetch_stock_quotes",
        lambda secids, **_kwargs: {
            "1.688012": {
                "secid": "1.688012",
                "change_percent": 18.83,
                "quote_timestamp": quoted_at,
            },
            "0.002371": {
                "secid": "0.002371",
                "change_percent": -2.16,
                "quote_timestamp": quoted_at,
            },
            "1.688072": {
                "secid": "1.688072",
                "change_percent": 20.0,
                "quote_timestamp": quoted_at - 24 * 60 * 60,
            },
        },
    )
    saved: list[dict] = []
    monkeypatch.setattr(service, "save_spot_snapshot", lambda _key, value: saved.append(value))

    result = service.build_fund_holdings_distribution("020357", force_refresh=True)

    assert result["quote_session_date"] == "2026-07-21"
    assert result["quote_updated_at"] == "2026-07-21T15:00:00+08:00"
    assert result["quote_source"] == "eastmoney_realtime_quote"
    by_code = {row["security_code"]: row for row in result["holdings"]}
    assert by_code["688012"]["quote_change_percent"] == 18.83
    assert by_code["002371"]["quote_change_percent"] == -2.16
    assert by_code["688072"]["quote_change_percent"] is None
    assert "quote_change_percent" not in saved[0]["holdings"][0]


def test_distribution_falls_back_to_official_nav_weight_when_allocation_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    snapshots: tuple[dict, dict],
) -> None:
    current, previous = snapshots
    monkeypatch.setattr(
        service,
        "_resolve_snapshot_pair",
        lambda *_args, **_kwargs: (deepcopy(current), deepcopy(previous)),
    )
    monkeypatch.setattr(service, "_fetch_stock_allocations", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(service, "save_spot_snapshot", lambda *_args, **_kwargs: None)

    result = service.build_fund_holdings_distribution("20357", force_refresh=True)

    assert result["fund_code"] == "020357"
    assert result["display_weight_basis"] == "fund_nav"
    first = result["holdings"][0]
    assert first["display_weight_percent"] == 0.6
    assert first["nav_weight_percent"] == 0.6
    assert first["comparison_basis"] == "fund_nav"
    assert first["change_percent_points"] == 0.2


def test_distribution_returns_stable_empty_contract_for_unqualified_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "_resolve_snapshot_pair",
        lambda *_args, **_kwargs: (
            {
                "status": "unavailable",
                "qualified": False,
                "reason_codes": ["portfolio_rows_missing"],
            },
            None,
        ),
    )

    result = service.build_fund_holdings_distribution("000001", force_refresh=True)

    assert result["status"] == "unavailable"
    assert result["holdings"] == []
    assert result["reason_codes"] == ["portfolio_rows_missing"]


def test_distribution_rejects_non_numeric_fund_code() -> None:
    with pytest.raises(ValueError, match="基金代码"):
        service.build_fund_holdings_distribution("ABC", force_refresh=True)


def test_holdings_distribution_endpoint_exposes_the_read_only_contract(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = service._unavailable_payload(
        "020357",
        reason_codes=["test_fixture"],
    )
    monkeypatch.setattr(
        service,
        "build_fund_holdings_distribution",
        lambda fund_code, **_kwargs: {**expected, "fund_code": fund_code},
    )

    response = client.get("/api/funds/020357/holdings-distribution")

    assert response.status_code == 200
    assert response.json()["fund_code"] == "020357"
    assert response.json()["status"] == "unavailable"
