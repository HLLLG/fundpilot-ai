"""Ops panel: durable error capture, traffic rollups, and admin endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import refresh_settings
from app.database import _connect
from app.main import app
from app.routes.ops import reset_ops_rate_limit_for_tests
from app.services import ops_observability as ops
from tests.conftest import register_and_login


@pytest.fixture
def ops_enabled(monkeypatch):
    """Turn capture on for this test only; the suite disables it by default."""

    monkeypatch.setenv("FUND_AI_OPS_ERROR_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("FUND_AI_OPS_TRAFFIC_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("FUND_AI_OPS_CLIENT_ERROR_INGEST_ENABLED", "true")
    # Writer thread stays off so assertions never race with scheduling.
    monkeypatch.setenv("FUND_AI_OPS_WRITER_THREAD_ENABLED", "false")
    refresh_settings()
    ops.reset_ops_observability_for_tests()
    reset_ops_rate_limit_for_tests()
    yield
    ops.reset_ops_observability_for_tests()
    reset_ops_rate_limit_for_tests()


def _promote_to_admin(user_id: int) -> None:
    with _connect() as connection:
        connection.execute(
            "UPDATE users SET userRole = 'admin' WHERE id = ?",
            (user_id,),
        )


def _admin_client() -> TestClient:
    client = TestClient(app)
    token = register_and_login(client)
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    _promote_to_admin(int(me.json()["id"]))
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ---------------------------------------------------------------------------
# Fingerprint grouping
# ---------------------------------------------------------------------------


def test_equivalent_failures_share_one_fingerprint() -> None:
    stack = '  File "/app/x.py", line 12, in load\n    raise ValueError(code)'
    first = ops.build_fingerprint(
        source="backend",
        error_type="ValueError",
        message="fund 000001 not found",
        stack=stack,
        route="/api/funds/000001",
    )
    second = ops.build_fingerprint(
        source="backend",
        error_type="ValueError",
        message="fund 519888 not found",
        stack=stack,
        route="/api/funds/519888",
    )
    assert first == second


def test_fingerprint_separates_type_source_and_route() -> None:
    base = {"error_type": "ValueError", "message": "boom", "route": "/api/a"}
    backend = ops.build_fingerprint(source="backend", **base)
    assert backend != ops.build_fingerprint(
        source="frontend", **base
    ), "同一文案但来自前端应是不同分组"
    assert backend != ops.build_fingerprint(
        source="backend", **{**base, "error_type": "KeyError"}
    )
    assert backend != ops.build_fingerprint(
        source="backend", **{**base, "route": "/api/b"}
    )


def test_normalize_error_message_strips_request_specific_detail() -> None:
    normalized = ops.normalize_error_message(
        "user 41 failed at https://x.test/a?b=1 with id "
        "3f2504e0-4f89-11d3-9a0c-0305e82c3301 and 'literal'"
    )
    assert "41" not in normalized
    assert "3f2504e0" not in normalized
    assert "<url>" in normalized and "<id>" in normalized and "<str>" in normalized


# ---------------------------------------------------------------------------
# Store behaviour
# ---------------------------------------------------------------------------


def test_capture_is_a_noop_while_disabled(_auth_env) -> None:
    """The default suite configuration must not write ops rows."""

    assert ops.record_error_event(
        source="backend",
        error_type="RuntimeError",
        message="should not be stored",
    ) is None
    ops.flush_ops_writes()
    assert ops.list_error_groups(hours=1, status="all")["total"] == 0


def test_error_events_persist_with_stack_and_group_state(ops_enabled) -> None:
    fingerprint = ops.record_error_event(
        source="backend",
        error_type="ValueError",
        message="持仓同步失败",
        stack='Traceback:\n  File "/app/svc.py", line 9, in sync\n    raise ValueError()',
        route="/api/portfolio/holdings",
        method="GET",
        status_code=500,
        request_id="req-0123456789",
        user_id=7,
        context={"logger": "app.services.sync"},
    )
    assert fingerprint
    ops.flush_ops_writes()

    detail = ops.get_error_group(fingerprint)
    assert detail is not None
    assert detail["group"]["errorType"] == "ValueError"
    assert detail["group"]["status"] == ops.STATUS_OPEN
    assert detail["group"]["eventCount"] == 1
    assert detail["group"]["affectedUserCount"] == 1

    event = detail["events"][0]
    assert "raise ValueError()" in event["stack"]
    assert event["requestId"] == "req-0123456789"
    assert event["statusCode"] == 500
    assert event["context"]["logger"] == "app.services.sync"


def test_group_event_count_stays_truthful_when_details_are_sampled(
    ops_enabled,
    monkeypatch,
) -> None:
    """A retry storm must be counted fully but stored sparsely."""

    monkeypatch.setenv(
        "FUND_AI_OPS_ERROR_EVENTS_PER_FINGERPRINT_PER_MINUTE",
        "2",
    )
    refresh_settings()
    for _ in range(9):
        fingerprint = ops.record_error_event(
            source="backend",
            error_type="RuntimeError",
            message="burst failure",
            route="/api/burst",
            status_code=500,
        )
    ops.flush_ops_writes()

    detail = ops.get_error_group(str(fingerprint))
    assert detail is not None
    assert detail["group"]["eventCount"] == 9
    assert detail["storedEventCount"] == 2


def test_resolved_group_reopens_on_regression(ops_enabled) -> None:
    def report() -> str:
        return str(
            ops.record_error_event(
                source="backend",
                error_type="RuntimeError",
                message="回归错误",
                route="/api/regress",
                status_code=500,
            )
        )

    fingerprint = report()
    ops.flush_ops_writes()
    resolved = ops.set_error_group_status(
        fingerprint,
        status=ops.STATUS_RESOLVED,
        actor_id=1,
        note="已修复",
    )
    assert resolved is not None
    assert resolved["status"] == ops.STATUS_RESOLVED
    assert resolved["resolvedAt"] is not None

    report()
    ops.flush_ops_writes()
    detail = ops.get_error_group(fingerprint)
    assert detail is not None
    assert detail["group"]["status"] == ops.STATUS_OPEN, "复发必须自动重开"
    assert detail["group"]["resolvedAt"] is None
    assert detail["group"]["eventCount"] == 2


def test_traffic_rollups_report_counts_latency_and_routes(ops_enabled) -> None:
    for index in range(20):
        ops.record_request_traffic(
            method="GET",
            route="/api/portfolio/holdings",
            status_code=500 if index % 10 == 0 else 200,
            duration_ms=100.0 + index,
            response_bytes=512,
        )
    ops.record_request_traffic(
        method="POST",
        route="/api/auth/login",
        status_code=401,
        duration_ms=20.0,
    )
    ops.flush_ops_writes()

    overview = ops.ops_overview(hours=1)
    totals = overview["totals"]
    assert overview["available"] is True
    assert totals["request_count"] == 21
    assert totals["server_error_count"] == 2
    assert totals["client_error_count"] == 1
    assert totals["mean_ms"] is not None
    assert totals["p95_ms"] is not None
    # Gaps are emitted so an outage reads as a hole rather than a flat line.
    assert len(overview["series"]) == 60
    assert sum(point["request_count"] for point in overview["series"]) == 21

    routes = {(row["method"], row["route"]): row for row in overview["top_routes"]}
    holdings = routes[("GET", "/api/portfolio/holdings")]
    assert holdings["request_count"] == 20
    assert holdings["server_error_count"] == 2
    assert holdings["p95_ms"] is not None


def test_retention_prune_drops_expired_rows_only(ops_enabled) -> None:
    ops.record_error_event(
        source="backend",
        error_type="RuntimeError",
        message="旧错误",
        route="/api/old",
    )
    ops.record_request_traffic(
        method="GET",
        route="/api/old",
        status_code=200,
        duration_ms=5.0,
    )
    ops.flush_ops_writes()

    assert ops.prune_ops_data()["events"] == 0

    far_future = datetime.now(timezone.utc) + timedelta(days=400)
    removed = ops.prune_ops_data(now=far_future)
    assert removed["events"] == 1
    assert removed["traffic_minutes"] == 1
    assert ops.list_error_groups(hours=1, status="all")["total"] == 0


# ---------------------------------------------------------------------------
# Browser ingest endpoint
# ---------------------------------------------------------------------------


def test_client_error_ingest_accepts_anonymous_reports(ops_enabled) -> None:
    """Login-page crashes happen before a token exists."""

    with TestClient(app) as client:
        response = client.post(
            "/api/telemetry/client-errors",
            json={
                "message": "Cannot read properties of undefined (reading 'map')",
                "errorType": "TypeError",
                "stack": "TypeError: x\n    at Page (/_next/static/chunks/a.js:1:2)",
                "componentStack": "\n    at Dashboard\n    at Layout",
                "kind": "react_render",
                "path": "/login",
                "viewport": "1440x900",
                "breadcrumbs": ["route:/", "route:/login"],
            },
        )
        assert response.status_code == 202, response.text
        body = response.json()
        assert body["accepted"] is True
        assert body["fingerprint"]

        ops.flush_ops_writes()
        detail = ops.get_error_group(body["fingerprint"])
        assert detail is not None
        assert detail["group"]["source"] == ops.SOURCE_FRONTEND
        assert detail["group"]["route"] == "/login"
        event = detail["events"][0]
        # Both stacks are kept: minified frames plus the component path.
        assert "React component stack" in event["stack"]
        assert "at Dashboard" in event["stack"]
        assert event["context"]["kind"] == "react_render"
        assert event["context"]["breadcrumbs"] == ["route:/", "route:/login"]
        assert event["userId"] is None


def test_client_error_ingest_records_user_when_token_present(ops_enabled) -> None:
    with TestClient(app) as client:
        token = register_and_login(client)
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        user_id = int(me.json()["id"])
        response = client.post(
            "/api/telemetry/client-errors",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "已登录用户的前端错误", "errorType": "Error"},
        )
        assert response.status_code == 202, response.text
        ops.flush_ops_writes()
        detail = ops.get_error_group(response.json()["fingerprint"])
        assert detail is not None
        assert detail["events"][0]["userId"] == user_id


def test_client_error_ingest_tolerates_an_invalid_token(ops_enabled) -> None:
    """An expired session must not stop a crash report from arriving."""

    with TestClient(app) as client:
        response = client.post(
            "/api/telemetry/client-errors",
            headers={"Authorization": "Bearer not-a-real-token"},
            json={"message": "token 失效时的前端错误"},
        )
        assert response.status_code == 202, response.text


def test_client_error_ingest_rejects_oversized_and_malformed_bodies(
    ops_enabled,
) -> None:
    with TestClient(app) as client:
        too_big = client.post(
            "/api/telemetry/client-errors",
            content=b'{"message":"' + b"x" * 40_000 + b'"}',
            headers={"Content-Type": "application/json"},
        )
        assert too_big.status_code == 413

        malformed = client.post(
            "/api/telemetry/client-errors",
            json={"errorType": "Error"},
        )
        assert malformed.status_code == 422


def test_client_error_ingest_rate_limits_per_client(ops_enabled, monkeypatch) -> None:
    monkeypatch.setenv("FUND_AI_OPS_CLIENT_ERROR_RATE_LIMIT_PER_MINUTE", "2")
    refresh_settings()
    reset_ops_rate_limit_for_tests()
    with TestClient(app) as client:
        payload = {"message": "刷上报"}
        assert client.post("/api/telemetry/client-errors", json=payload).status_code == 202
        assert client.post("/api/telemetry/client-errors", json=payload).status_code == 202
        blocked = client.post("/api/telemetry/client-errors", json=payload)
        assert blocked.status_code == 429


def test_client_error_ingest_drops_silently_when_disabled(
    ops_enabled,
    monkeypatch,
) -> None:
    monkeypatch.setenv("FUND_AI_OPS_CLIENT_ERROR_INGEST_ENABLED", "false")
    refresh_settings()
    with TestClient(app) as client:
        response = client.post(
            "/api/telemetry/client-errors",
            json={"message": "被丢弃的上报"},
        )
        # Still 202 so the browser does not retry, but nothing is stored.
        assert response.status_code == 202
        assert response.json() == {"accepted": False, "fingerprint": None}
    ops.flush_ops_writes()
    assert ops.list_error_groups(hours=1, status="all")["total"] == 0


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/api/admin/ops/overview"),
        ("get", "/api/admin/ops/errors"),
        ("get", "/api/admin/ops/errors/deadbeef"),
        ("get", "/api/admin/ops/capture"),
    ],
)
def test_admin_ops_endpoints_reject_non_admins(auth_client, method, path) -> None:
    response = getattr(auth_client, method)(path)
    assert response.status_code == 403, response.text


def test_admin_ops_endpoints_reject_anonymous_callers() -> None:
    with TestClient(app) as client:
        assert client.get("/api/admin/ops/overview").status_code == 401


def test_admin_overview_and_error_triage_flow(ops_enabled) -> None:
    with _admin_client() as client:
        report = client.post(
            "/api/telemetry/client-errors",
            json={
                "message": "结算页崩溃",
                "errorType": "TypeError",
                "path": "/settle",
                "kind": "window_error",
            },
        )
        assert report.status_code == 202, report.text
        fingerprint = report.json()["fingerprint"]

        overview = client.get("/api/admin/ops/overview?hours=1")
        assert overview.status_code == 200, overview.text
        payload = overview.json()
        assert payload["contract_version"] == ops.CONTRACT_VERSION
        assert payload["errors"]["frontend_event_count"] == 1
        assert payload["capture"]["errorCaptureEnabled"] is True
        # The admin request itself is traffic, so the window is not empty.
        assert payload["totals"]["request_count"] >= 1

        listed = client.get("/api/admin/ops/errors?hours=1&status=open")
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["fingerprint"] == fingerprint

        filtered = client.get("/api/admin/ops/errors?hours=1&source=backend")
        assert filtered.json()["total"] == 0

        searched = client.get("/api/admin/ops/errors?hours=1&query=崩溃")
        assert searched.json()["total"] == 1

        detail = client.get(f"/api/admin/ops/errors/{fingerprint}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["group"]["message"] == "结算页崩溃"

        updated = client.post(
            f"/api/admin/ops/errors/{fingerprint}/status",
            json={"status": "resolved", "note": "已发版修复"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["status"] == "resolved"
        assert updated.json()["note"] == "已发版修复"

        assert client.get("/api/admin/ops/errors?hours=1&status=open").json()["total"] == 0
        assert (
            client.get("/api/admin/ops/errors?hours=1&status=resolved").json()["total"]
            == 1
        )


def test_admin_error_detail_returns_404_for_unknown_fingerprint(ops_enabled) -> None:
    with _admin_client() as client:
        assert client.get("/api/admin/ops/errors/0" * 1).status_code == 404
        assert (
            client.post(
                "/api/admin/ops/errors/unknown/status",
                json={"status": "resolved"},
            ).status_code
            == 404
        )


def test_unhandled_server_error_is_captured_with_request_context(
    ops_enabled,
    monkeypatch,
) -> None:
    """A real 500 must leave a traceback plus the identity of the failing call.

    This is the whole point of the feature: the operator should not need the
    user to describe anything.
    """

    import app.main as main_module

    def explode() -> dict:
        raise RuntimeError("行情会话提供方异常")

    monkeypatch.setattr(main_module, "build_trading_session", explode)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/trading-session")
        assert response.status_code == 500
        request_id = response.headers.get("X-Request-ID")
        # Support can map a user's screenshot onto one stored traceback.
        assert request_id

        ops.flush_ops_writes()
        listed = ops.list_error_groups(hours=1, status="all")
        assert listed["total"] == 1
        group = listed["items"][0]
        assert group["errorType"] == "RuntimeError"
        assert group["message"] == "行情会话提供方异常"
        assert group["route"] == "/api/trading-session"
        assert group["source"] == ops.SOURCE_BACKEND

        detail = ops.get_error_group(group["fingerprint"])
        assert detail is not None
        event = detail["events"][0]
        assert event["method"] == "GET"
        assert event["statusCode"] == 500
        assert event["requestId"] == request_id
        assert "RuntimeError" in event["stack"]
        assert "explode" in event["stack"]


def test_logged_error_inside_a_request_carries_route_context(ops_enabled) -> None:
    """`logger.exception` from service code is captured with the live request."""

    import logging

    from app.services.ops_error_logging import (
        install_ops_error_log_handler,
        uninstall_ops_error_log_handler,
    )

    install_ops_error_log_handler()
    try:
        with TestClient(app) as client:
            token = register_and_login(client)
            client.headers.update({"Authorization": f"Bearer {token}"})
            # /health runs inside the metrics middleware, so the contextvar is
            # live while this handler logs.
            logger = logging.getLogger("app.services.pytest_probe")

            captured: dict[str, str | None] = {}

            @app.get("/api/pytest-ops-probe")
            def _probe() -> dict:
                try:
                    raise KeyError("missing-nav")
                except KeyError:
                    logger.exception("净值缺失")
                from app.services.performance_metrics import current_request_context

                captured.update(current_request_context())
                return {"ok": True}

            response = client.get("/api/pytest-ops-probe")
            assert response.status_code == 200, response.text
            assert captured.get("path") == "/api/pytest-ops-probe"

            ops.flush_ops_writes()
            groups = ops.list_error_groups(hours=1, status="all")["items"]
            probe = [item for item in groups if item["errorType"] == "KeyError"]
            assert probe, groups
            assert probe[0]["route"] == "/api/pytest-ops-probe"
            detail = ops.get_error_group(probe[0]["fingerprint"])
            assert detail is not None
            event = detail["events"][0]
            assert event["method"] == "GET"
            assert "KeyError" in event["stack"]
            assert event["context"]["logger"] == "app.services.pytest_probe"
    finally:
        uninstall_ops_error_log_handler()
        app.router.routes = [
            route
            for route in app.router.routes
            if getattr(route, "path", "") != "/api/pytest-ops-probe"
        ]


def test_capture_state_reports_pipeline_health(ops_enabled) -> None:
    with _admin_client() as client:
        response = client.get("/api/admin/ops/capture")
        assert response.status_code == 200, response.text
        state = response.json()
        assert state["errorCaptureEnabled"] is True
        assert state["droppedEventCount"] == 0
        assert state["persistFailureCount"] == 0
        assert state["instanceId"]


# ---------------------------------------------------------------------------
# Schema upgrade path
# ---------------------------------------------------------------------------


def test_existing_v22_database_gains_the_ops_tables_on_upgrade() -> None:
    """An already-deployed database must pick up the ops schema in place.

    Production databases predate this feature, so the upgrade — not the fresh
    create — is the path that actually runs. Simulated by building the current
    schema, then rewinding the marker and dropping the new tables.
    """

    import sqlite3

    from app.db_migrations import SCHEMA_VERSION, run_migrations

    ops_tables = (
        "ops_error_groups",
        "ops_error_events",
        "ops_traffic_minutes",
        "ops_route_hours",
    )
    connection = sqlite3.connect(":memory:")
    try:
        run_migrations(connection)
        for table in ops_tables:
            connection.execute(f"DROP TABLE {table}")
        connection.execute("UPDATE schema_meta SET version = 22 WHERE id = 1")
        connection.commit()

        run_migrations(connection)

        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table in ops_tables:
            assert table in names, f"{table} 未在 v22→v23 升级中创建"
        assert (
            connection.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()[0]
            == SCHEMA_VERSION
        )

        # Indexes matter as much as the tables: the panel range-scans by time.
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert "idx_ops_error_events_occurred" in indexes
        assert "idx_ops_error_groups_status_seen" in indexes
        assert "idx_ops_traffic_minutes_bucket" in indexes
        assert "idx_ops_route_hours_bucket" in indexes
    finally:
        connection.close()


def test_repeated_migrations_keep_the_ops_schema_intact() -> None:
    """Re-running migrations on an up-to-date database must be a no-op."""

    import sqlite3

    from app.db_migrations import run_migrations

    connection = sqlite3.connect(":memory:")
    try:
        run_migrations(connection)
        connection.execute(
            """
            INSERT INTO ops_error_groups (
                fingerprint, source, level, error_type, message, route,
                first_seen_at, last_seen_at, event_count, status
            ) VALUES ('abc', 'backend', 'error', 'E', 'm', '/x',
                      '2026-08-06T00:00:00+00:00', '2026-08-06T00:00:00+00:00', 1, 'open')
            """
        )
        connection.commit()

        run_migrations(connection)

        assert (
            connection.execute("SELECT COUNT(*) FROM ops_error_groups").fetchone()[0] == 1
        ), "重复迁移不得清空已有数据"
    finally:
        connection.close()


def test_mysql_bootstrap_declares_the_same_ops_tables() -> None:
    """The MySQL DDL must stay aligned with the SQLite contract.

    Development runs on SQLite while production runs on MySQL, so a table that
    exists in only one dialect fails exactly where it matters least visibly.
    """

    from app.mysql_bootstrap import MYSQL_SCHEMA_VERSION, ensure_mysql_schema
    from app.db_migrations import SCHEMA_VERSION

    assert MYSQL_SCHEMA_VERSION == SCHEMA_VERSION

    statements: list[str] = []

    class Cursor:
        def execute(self, statement, params=()):  # noqa: ANN001, ANN202
            statements.append(str(statement))

    class Connection:
        def cursor(self):  # noqa: ANN202
            return Cursor()

        def commit(self) -> None:
            return None

    ensure_mysql_schema(Connection())
    joined = "\n".join(statements)

    for table in (
        "ops_error_groups",
        "ops_error_events",
        "ops_traffic_minutes",
        "ops_route_hours",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in joined, f"MySQL 缺少 {table}"
    # ``release`` is reserved in MySQL; the column must stay suffixed.
    assert "release_tag" in joined
    assert "\n            release " not in joined
