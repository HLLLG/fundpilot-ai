from __future__ import annotations

from app.models import Holding
from app.services.holding_filters import is_test_holding


def _holding(code: str, name: str, amount: float = 1_000) -> Holding:
    return Holding(
        fund_code=code,
        fund_name=name,
        holding_amount=amount,
        return_percent=0,
    )


def test_non_empty_snapshot_membership_is_not_recovered_from_stale_profiles(
    monkeypatch,
) -> None:
    from app.services import portfolio_holdings_service as service

    snapshot_holdings = [
        _holding("010236", "广发电子信息传媒股票C"),
        _holding("015945", "易方达国防军工混合C"),
        _holding("025856", "华夏中证电网设备主题ETF联接A"),
    ]
    monkeypatch.setattr(
        service,
        "get_most_recent_portfolio_snapshot",
        lambda: {
            "snapshot_date": "2026-07-15",
            "captured_at": "2026-07-15T15:00:00+08:00",
            "total_assets": 3_000,
            "holdings": [item.model_dump(mode="json") for item in snapshot_holdings],
        },
    )
    monkeypatch.setattr(
        service,
        "_lightweight_profile_holdings",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("profiles must not replace snapshot membership")
        ),
    )
    monkeypatch.setattr(service, "enrich_loaded_holdings", lambda rows, **_kwargs: rows)

    resolved, source, snapshot_date, _ = service.load_persisted_holdings(
        fetch_benchmark=False
    )

    assert source == "snapshot"
    assert snapshot_date == "2026-07-15"
    assert [item.fund_code for item in resolved] == ["010236", "015945", "025856"]


def test_audit_named_profile_is_recognized_as_test_data() -> None:
    assert is_test_holding(_holding("008586", "audit")) is True
    assert is_test_holding(_holding("008586", "AUDIT")) is True
    assert is_test_holding(_holding("008586", "华夏人工智能ETF联接C")) is False
