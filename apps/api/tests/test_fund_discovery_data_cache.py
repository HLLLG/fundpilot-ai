from __future__ import annotations

from app.services.fund_discovery_data_cache import (
    _build_universe_snapshot,
    _universe_rows_with_snapshot_contract,
)


def _raw_row(code: str = "000001") -> dict:
    return {
        "fund_code": code,
        "fund_name": "测试基金",
        "fund_type": "gp",
        "return_3m_percent": 4.2,
        "fund_scale_yi": 12.5,
    }


def test_universe_snapshot_is_stamped_once_at_write() -> None:
    snapshot = _build_universe_snapshot([_raw_row(), _raw_row("000002")])
    first = snapshot["rows"][0]

    assert first["membership_available_at"] == snapshot["snapshot_available_at"]
    assert first["return_3m_percent_available_at"] == snapshot["snapshot_available_at"]
    assert first["fund_scale_yi_available_at"] == snapshot["snapshot_available_at"]


def test_stamped_universe_rows_are_shared_not_copied() -> None:
    snapshot = _build_universe_snapshot([_raw_row()])
    first = _universe_rows_with_snapshot_contract(snapshot)
    second = _universe_rows_with_snapshot_contract(snapshot)

    assert first[0] is snapshot["rows"][0]
    assert second[0] is first[0]


def test_legacy_unstamped_rows_still_receive_contract_fields() -> None:
    payload = {
        "snapshot_available_at": "2026-08-07T02:00:00+00:00",
        "source": "legacy",
        "rows": [_raw_row()],
    }

    rows = _universe_rows_with_snapshot_contract(payload)

    assert rows[0] is not payload["rows"][0]
    assert rows[0]["membership_available_at"] == "2026-08-07T02:00:00+00:00"
    assert rows[0]["return_3m_percent_available_at"] == "2026-08-07T02:00:00+00:00"
