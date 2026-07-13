from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth.middleware import _is_public_path
from app.config import refresh_settings
from app.main import app


PATH = "/api/internal/factor-ic-universe-snapshots"
TOKEN = "factor-ic-universe-test-token"


def _configure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FUND_AI_DATABASE_URL", "")
    monkeypatch.setenv("FUND_AI_DB_PATH", str(tmp_path / "endpoint.db"))
    monkeypatch.setenv("FUND_AI_FACTOR_IC_PUBLISH_TOKEN", TOKEN)
    refresh_settings()


def test_internal_universe_path_is_exactly_exempt_from_jwt() -> None:
    assert _is_public_path(PATH)
    assert not _is_public_path(PATH + "/extra")


def test_post_reuses_publish_token_and_maps_service(monkeypatch, tmp_path) -> None:
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.main.validate_factor_ic_universe_publish_request",
        lambda body: body,
    )
    monkeypatch.setattr(
        "app.main.publish_factor_ic_universe_snapshot",
        lambda request: {"created": True, "snapshot_id": request["id"]},
    )
    client = TestClient(app)
    assert client.post(PATH, json={"id": "x"}).status_code == 401
    response = client.post(
        PATH,
        headers={"X-Factor-IC-Publish-Token": TOKEN},
        json={"id": "snapshot"},
    )
    assert response.status_code == 200
    assert response.json() == {"created": True, "snapshot_id": "snapshot"}


def test_get_is_token_protected_and_passes_bounded_options(monkeypatch, tmp_path) -> None:
    _configure(monkeypatch, tmp_path)
    calls: list[dict] = []

    def read(**kwargs):
        calls.append(kwargs)
        return {"snapshot_count": 0, "snapshots": []}

    monkeypatch.setattr("app.main.read_factor_ic_universe_history", read)
    response = TestClient(app).get(
        PATH + "?days=30&max_snapshots=5&stride_days=7&include_members=false",
        headers={"X-Factor-IC-Publish-Token": TOKEN},
    )
    assert response.status_code == 200
    assert calls == [
        {
            "start_date": None,
            "end_date": None,
            "days": 30,
            "max_snapshots": 5,
            "stride_days": 7,
            "include_members": False,
        }
    ]
