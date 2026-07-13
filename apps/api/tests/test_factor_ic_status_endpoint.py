from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.services.factor_ic_snapshot import (
    publish_factor_ic_snapshot,
    validate_publish_request,
)
from tests.test_factor_ic_snapshot import valid_payload


STATUS_PATH = "/api/diagnostics/factor-ic-status"


def test_factor_ic_status_endpoint_requires_jwt() -> None:
    response = TestClient(app).get(STATUS_PATH)
    assert response.status_code == 401


def test_factor_ic_status_endpoint_returns_latest_snapshot(auth_client) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    request = validate_publish_request(valid_payload(now.isoformat()), now=now)
    published = publish_factor_ic_snapshot(request, now=now)

    response = auth_client.get(STATUS_PATH)

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["snapshot_id"] == published["snapshot_id"]
    assert body["source"] == "database"
    assert body["stale"] is False
    assert body["universe_size"] == 300
