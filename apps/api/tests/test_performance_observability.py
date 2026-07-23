from __future__ import annotations

import threading
import time

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app.services.performance_metrics import (
    PerformanceMetricsMiddleware,
    performance_snapshot,
    record_db_query,
    reset_performance_metrics_for_tests,
)
from app.startup_readiness import (
    ReadinessGateMiddleware,
    mark_ready,
    readiness_snapshot,
)


def test_request_metrics_use_route_templates_and_safe_request_ids() -> None:
    reset_performance_metrics_for_tests()
    application = FastAPI()
    application.add_middleware(PerformanceMetricsMiddleware)

    @application.get("/items/{item_id}")
    def item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    with TestClient(application) as client:
        response = client.get(
            "/items/123456",
            headers={"X-Request-ID": "unsafe value with spaces"},
        )

    assert response.status_code == 200
    assert len(response.headers["x-request-id"]) == 32
    assert "app;dur=" in response.headers["server-timing"]
    rows = performance_snapshot()["requests"]
    item_row = next(row for row in rows if row["route"] == "/items/{item_id}")
    assert item_row["latency_ms"]["count"] == 1
    assert item_row["response_bytes"] > 0


def test_database_fingerprint_never_retains_literals_or_parameters() -> None:
    reset_performance_metrics_for_tests()
    record_db_query(
        "mysql",
        "SELECT * FROM auth_users WHERE email='private@example.com' AND id=12345",
        0.01,
    )
    payload = str(performance_snapshot()["database"])
    assert "private@example.com" not in payload
    assert "12345" not in payload
    assert "auth_users" in payload


def test_request_metric_cardinality_is_bounded_for_unknown_paths() -> None:
    reset_performance_metrics_for_tests()
    application = FastAPI()
    application.add_middleware(PerformanceMetricsMiddleware)

    with TestClient(application) as client:
        for index in range(300):
            assert client.get(f"/missing-path-{index}-x").status_code == 404

    rows = performance_snapshot()["requests"]
    assert len(rows) <= 257
    assert any(row["method"] == "other" for row in rows)


def test_background_bootstrap_is_guarded_by_readiness(monkeypatch) -> None:
    from app import lifespan as lifespan_module

    started = threading.Event()
    release = threading.Event()

    def fake_initialize(_app: FastAPI, _shutdown: threading.Event) -> None:
        started.set()
        assert release.wait(timeout=5)
        mark_ready()

    monkeypatch.setattr(lifespan_module, "uses_mysql", lambda: True)
    monkeypatch.setattr(lifespan_module, "_initialize_runtime", fake_initialize)

    application = FastAPI(lifespan=lifespan_module.app_lifespan)
    application.add_middleware(ReadinessGateMiddleware)

    @application.get("/health")
    def health(response: Response) -> dict:
        state = readiness_snapshot()
        if not state["ready"]:
            response.status_code = 503
        return state

    @application.get("/business")
    def business() -> dict[str, bool]:
        return {"ok": True}

    try:
        with TestClient(application) as client:
            assert started.wait(timeout=2)
            assert client.get("/business").status_code == 503
            assert client.get("/health").status_code == 503
            release.set()
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if client.get("/health").status_code == 200:
                    break
                time.sleep(0.01)
            assert client.get("/business").status_code == 200
    finally:
        release.set()
        mark_ready()
