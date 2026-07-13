from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models import DiscoveryRequest


def test_dip_radar_endpoint_is_retired(client: TestClient) -> None:
    response = client.get("/api/market/dip-radar")

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scan_mode", "dip_swing"),
        ("selection_strategy", "dip_rebound"),
    ],
)
def test_retired_discovery_modes_are_rejected(
    client: TestClient,
    field: str,
    value: str,
) -> None:
    response = client.post(
        "/api/fund-discovery/async",
        json={"profile": {}, field: value},
    )

    assert response.status_code == 422


def test_retired_dip_request_fields_are_absent_from_schema() -> None:
    assert "dip_lookback_days" not in DiscoveryRequest.model_fields
    assert "dip_min_drop_percent" not in DiscoveryRequest.model_fields
