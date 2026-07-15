from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth.middleware import _is_public_path
from app.config import refresh_settings
from app.main import app
from app.services.decision_quality_snapshot import DecisionQualitySnapshotStorageError


PATH = "/api/internal/decision-quality/evaluations/latest"
TOKEN = "decision-quality-read-token-at-least-32-characters"


def _configure(monkeypatch, *, token: str | None = TOKEN) -> None:
    if token is None:
        monkeypatch.delenv("FUND_AI_DECISION_QUALITY_READ_TOKEN", raising=False)
    else:
        monkeypatch.setenv("FUND_AI_DECISION_QUALITY_READ_TOKEN", token)
    refresh_settings()


def _payload() -> dict:
    return {
        "schema_version": "decision_quality_snapshot_read.v1",
        "snapshot_id": "dqs_" + "a" * 64,
        "content_hash": "a" * 64,
        "evaluation_as_of": "2026-07-14T00:00:00+00:00",
        "status": "unavailable",
        "readiness": {
            "status": "insufficient_data",
            "mature_decision_day_count": 0,
        },
        "overall": {"formal_event_horizon_count": 0},
        "automatic_promotion_allowed": False,
        "notices": ["只描述历史冻结样本，不构成投资建议。"],
    }


def test_internal_snapshot_path_is_token_public_but_hidden_from_openapi() -> None:
    assert _is_public_path(PATH)
    assert PATH not in app.openapi()["paths"]


def test_endpoint_uses_only_independent_token_and_returns_no_store_etag(
    monkeypatch,
) -> None:
    _configure(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(
        "app.main.read_latest_decision_quality_snapshot",
        lambda *, user_id: calls.append(user_id) or _payload(),
    )

    response = TestClient(app).get(
        PATH,
        params={"user_id": 17},
        headers={"X-Decision-Quality-Read-Token": TOKEN},
    )

    assert response.status_code == 200
    assert calls == [17]
    assert response.headers["etag"] == '"' + "a" * 64 + '"'
    assert "no-store" in response.headers["cache-control"]
    assert response.json()["automatic_promotion_allowed"] is False
    assert "evaluation" not in response.json()
    assert "input_manifest" not in response.json()


def test_endpoint_supports_conditional_read_without_recomputation(monkeypatch) -> None:
    _configure(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(
        "app.main.read_latest_decision_quality_snapshot",
        lambda *, user_id: calls.append(user_id) or _payload(),
    )

    response = TestClient(app).get(
        PATH,
        params={"user_id": 17},
        headers={
            "X-Decision-Quality-Read-Token": TOKEN,
            "If-None-Match": 'W/"' + "a" * 64 + '"',
        },
    )

    assert response.status_code == 304
    assert response.content == b""
    assert calls == [17]
    assert "no-store" in response.headers["cache-control"]


def test_endpoint_rejects_unconfigured_or_wrong_token_without_reading(
    monkeypatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        "app.main.read_latest_decision_quality_snapshot",
        lambda *, user_id: calls.append(user_id) or _payload(),
    )
    _configure(monkeypatch, token=None)
    unconfigured = TestClient(app).get(PATH, params={"user_id": 1})
    assert unconfigured.status_code == 503

    _configure(monkeypatch)
    supplied = "wrong-sensitive-token"
    wrong = TestClient(app).get(
        PATH,
        params={"user_id": 1},
        headers={"X-Decision-Quality-Read-Token": supplied},
    )
    assert wrong.status_code == 401
    assert supplied not in wrong.text
    assert calls == []


def test_endpoint_maps_missing_and_storage_unavailable_without_cache(
    monkeypatch,
) -> None:
    _configure(monkeypatch)
    headers = {"X-Decision-Quality-Read-Token": TOKEN}
    monkeypatch.setattr(
        "app.main.read_latest_decision_quality_snapshot",
        lambda **_kwargs: None,
    )
    missing = TestClient(app).get(PATH, params={"user_id": 1}, headers=headers)
    assert missing.status_code == 404
    assert "no-store" in missing.headers["cache-control"]

    def unavailable(**_kwargs):
        raise DecisionQualitySnapshotStorageError("database unavailable")

    monkeypatch.setattr(
        "app.main.read_latest_decision_quality_snapshot",
        unavailable,
    )
    failed = TestClient(app).get(PATH, params={"user_id": 1}, headers=headers)
    assert failed.status_code == 503
    assert "database unavailable" not in failed.text
    assert "no-store" in failed.headers["cache-control"]


def test_endpoint_invalid_user_id_is_also_no_store(monkeypatch) -> None:
    _configure(monkeypatch)
    response = TestClient(app).get(
        PATH,
        params={"user_id": "not-an-id"},
        headers={"X-Decision-Quality-Read-Token": TOKEN},
    )
    assert response.status_code == 422
    assert "no-store" in response.headers["cache-control"]
