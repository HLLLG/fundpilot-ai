from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.auth.middleware import _is_public_path
from app.config import refresh_settings
from app.main import app
from app.services.factor_ic_snapshot import FactorIcStorageUnavailable
from tests.test_factor_ic_snapshot import valid_payload


PUBLISH_PATH = "/api/internal/factor-ic-snapshots"
TOKEN = "factor-ic-test-token-at-least-32-characters"


def _configure_publish(monkeypatch, tmp_path, *, token: str | None = TOKEN) -> None:
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "publish.db"))
    if token is None:
        monkeypatch.delenv("FUND_AI_FACTOR_IC_PUBLISH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("FUND_AI_FACTOR_IC_PUBLISH_TOKEN", token)
    refresh_settings()


def test_publish_endpoint_rejects_missing_server_token(monkeypatch, tmp_path) -> None:
    _configure_publish(monkeypatch, tmp_path, token=None)

    response = TestClient(app).post(PUBLISH_PATH, json=valid_payload())

    assert response.status_code == 503
    assert "发布未配置" in response.text


def test_publish_endpoint_rejects_wrong_token_without_leaking_it(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_publish(monkeypatch, tmp_path)
    supplied = "wrong-secret-token-value"

    response = TestClient(app).post(
        PUBLISH_PATH,
        headers={"X-Factor-IC-Publish-Token": supplied},
        json=valid_payload(),
    )

    assert response.status_code == 401
    assert supplied not in response.text


def test_publish_token_uses_constant_time_comparison(monkeypatch, tmp_path) -> None:
    _configure_publish(monkeypatch, tmp_path)
    calls: list[tuple[str, str]] = []

    def compare_digest(supplied: str, expected: str) -> bool:
        calls.append((supplied, expected))
        return False

    monkeypatch.setattr("app.main.secrets.compare_digest", compare_digest)
    response = TestClient(app).post(
        PUBLISH_PATH,
        headers={"X-Factor-IC-Publish-Token": "supplied-token"},
        json=valid_payload(),
    )

    assert response.status_code == 401
    assert calls == [("supplied-token", TOKEN)]


def test_publish_endpoint_accepts_valid_token_and_is_idempotent(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_publish(monkeypatch, tmp_path)
    payload = valid_payload()
    client = TestClient(app)

    created = client.post(
        PUBLISH_PATH,
        headers={"X-Factor-IC-Publish-Token": TOKEN},
        json=payload,
    )
    duplicate = client.post(
        PUBLISH_PATH,
        headers={"X-Factor-IC-Publish-Token": TOKEN},
        json=payload,
    )

    assert created.status_code == 200
    assert created.json()["created"] is True
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False
    assert TOKEN not in created.text + duplicate.text


def test_publish_endpoint_rejects_invalid_quality(monkeypatch, tmp_path) -> None:
    _configure_publish(monkeypatch, tmp_path)
    payload = valid_payload()
    payload["summary"]["universe_size"] = 239

    response = TestClient(app).post(
        PUBLISH_PATH,
        headers={"X-Factor-IC-Publish-Token": TOKEN},
        json=payload,
    )

    assert response.status_code == 422
    assert "有效基金数不足" in response.text


def test_publish_endpoint_rejects_snapshot_older_than_database(
    monkeypatch,
    tmp_path,
) -> None:
    _configure_publish(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    newer = valid_payload((now - timedelta(minutes=5)).isoformat())
    older = valid_payload((now - timedelta(minutes=10)).isoformat())
    older["source_commit"] = "b" * 40
    client = TestClient(app)
    headers = {"X-Factor-IC-Publish-Token": TOKEN}

    assert client.post(PUBLISH_PATH, headers=headers, json=newer).status_code == 200
    response = client.post(PUBLISH_PATH, headers=headers, json=older)

    assert response.status_code == 409
    assert "已有更新" in response.text


def test_publish_endpoint_maps_storage_failure_to_503(monkeypatch, tmp_path) -> None:
    _configure_publish(monkeypatch, tmp_path)

    def unavailable(_request):
        raise FactorIcStorageUnavailable("database unavailable")

    monkeypatch.setattr("app.main.publish_factor_ic_snapshot", unavailable)
    response = TestClient(app).post(
        PUBLISH_PATH,
        headers={"X-Factor-IC-Publish-Token": TOKEN},
        json=valid_payload(),
    )

    assert response.status_code == 503
    assert "database unavailable" in response.text


def test_only_exact_publish_path_bypasses_user_jwt() -> None:
    assert _is_public_path(PUBLISH_PATH) is True
    assert _is_public_path(f"{PUBLISH_PATH}/extra") is False
    assert _is_public_path("/api/internal") is False
