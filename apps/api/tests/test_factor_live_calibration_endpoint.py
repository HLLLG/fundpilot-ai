from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.factor_live_calibration import FactorLiveCalibrationStorageUnavailable


PATH = "/api/diagnostics/factor-live-calibration"


def test_factor_live_calibration_endpoint_requires_jwt() -> None:
    assert TestClient(app).get(PATH).status_code == 401


def test_factor_live_calibration_endpoint_is_read_only_and_user_scoped(
    auth_client, monkeypatch
) -> None:
    captured: dict[str, int] = {}

    def fake_status(*, user_id: int):
        captured["user_id"] = user_id
        return {
            "schema_version": "factor_live_calibration.v1",
            "state": "insufficient",
            "mode": "shadow_read_only",
            "auto_tuning_eligible": False,
        }

    monkeypatch.setattr(
        "app.main.build_factor_live_calibration_status",
        fake_status,
    )

    response = auth_client.get(PATH)

    assert response.status_code == 200
    assert response.json()["auto_tuning_eligible"] is False
    assert captured["user_id"] > 0


def test_factor_live_calibration_endpoint_reports_primary_store_failure(
    auth_client, monkeypatch
) -> None:
    def unavailable(*, user_id: int):
        _ = user_id
        raise FactorLiveCalibrationStorageUnavailable("主证据库不可用")

    monkeypatch.setattr(
        "app.main.build_factor_live_calibration_status",
        unavailable,
    )

    response = auth_client.get(PATH)

    assert response.status_code == 503
    assert "主证据库不可用" in response.json()["detail"]
