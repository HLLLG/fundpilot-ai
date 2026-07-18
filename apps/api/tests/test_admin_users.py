from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app.database import _connect
from app.main import app
from app.services.admin_user_management import promote_bootstrap_admin


def _register(
    client: TestClient,
    *,
    email: str,
    password: str = "Initial123!",
    username: str,
) -> dict:
    response = client.post(
        "/api/auth/register",
        json={"userAccount": email, "password": password, "username": username},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _login(client: TestClient, *, email: str, password: str) -> dict:
    response = client.post(
        "/api/auth/login",
        json={"userAccount": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _admin_client(client: TestClient, *, email: str, password: str) -> TestClient:
    promote_bootstrap_admin(email)
    session = _login(client, email=email, password=password)
    client.headers.update({"Authorization": f"Bearer {session['accessToken']}"})
    return client


def test_admin_list_masks_email_and_non_admin_is_forbidden() -> None:
    client = TestClient(app)
    admin = _register(client, email="owner@example.com", username="Owner")
    member = _register(client, email="member@example.com", username="Member")

    client.headers.update({"Authorization": f"Bearer {member['accessToken']}"})
    forbidden = client.get("/api/admin/users")
    assert forbidden.status_code == 403

    _admin_client(client, email="owner@example.com", password="Initial123!")
    response = client.get("/api/admin/users")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"].startswith("no-store")
    items = response.json()["items"]
    assert response.json()["total"] == 2
    assert all(item["maskedAccount"] != "m***n@local" for item in items)
    member_row = next(item for item in items if item["id"] == member["user"]["id"])
    assert member_row["maskedAccount"] != "member@example.com"
    assert "userAccount" not in member_row

    detail = client.get(f"/api/admin/users/{member['user']['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["userAccount"] == "member@example.com"
    assert set(detail.json()["usage"]) == {
        "reportCount",
        "discoveryReportCount",
        "transactionCount",
        "fundProfileCount",
    }

    searched = client.post(
        "/api/admin/users/search",
        json={"query": "member@example.com", "page": 1, "pageSize": 20},
    )
    assert searched.status_code == 200, searched.text
    assert [item["id"] for item in searched.json()["items"]] == [member["user"]["id"]]


def test_disable_restore_revoke_and_last_admin_guards_are_immediate() -> None:
    client = TestClient(app)
    admin = _register(client, email="owner@example.com", username="Owner")
    member = _register(client, email="member@example.com", username="Member")
    member_token = member["accessToken"]
    _admin_client(client, email="owner@example.com", password="Initial123!")

    own_detail = client.get(f"/api/admin/users/{admin['user']['id']}").json()
    self_disable = client.post(
        f"/api/admin/users/{admin['user']['id']}/disable",
        json={
            "expectedUpdatedAt": own_detail["updatedAt"],
            "reason": "self protection test",
        },
    )
    assert self_disable.status_code == 403
    self_demote = client.patch(
        f"/api/admin/users/{admin['user']['id']}",
        json={
            "expectedUpdatedAt": own_detail["updatedAt"],
            "userRole": "user",
            "reason": "last admin protection test",
        },
    )
    assert self_demote.status_code == 403

    member_detail = client.get(f"/api/admin/users/{member['user']['id']}").json()
    disabled = client.post(
        f"/api/admin/users/{member['user']['id']}/disable",
        json={
            "expectedUpdatedAt": member_detail["updatedAt"],
            "reason": "suspicious session review",
        },
    )
    assert disabled.status_code == 200, disabled.text

    client.headers.update({"Authorization": f"Bearer {member_token}"})
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/admin/users").status_code == 401

    admin_session = _login(client, email="owner@example.com", password="Initial123!")
    client.headers.update({"Authorization": f"Bearer {admin_session['accessToken']}"})
    restored = client.post(
        f"/api/admin/users/{member['user']['id']}/restore",
        json={
            "expectedUpdatedAt": disabled.json()["updatedAt"],
            "reason": "review completed",
        },
    )
    assert restored.status_code == 200, restored.text

    member_session = _login(client, email="member@example.com", password="Initial123!")
    client.headers.update({"Authorization": f"Bearer {admin_session['accessToken']}"})
    revoked = client.post(
        f"/api/admin/users/{member['user']['id']}/revoke-sessions",
        json={"reason": "end all remembered sessions"},
    )
    assert revoked.status_code == 200, revoked.text
    client.headers.update({"Authorization": f"Bearer {member_session['accessToken']}"})
    assert client.get("/api/auth/me").status_code == 401


def test_password_reset_link_is_one_time_and_revokes_old_sessions() -> None:
    client = TestClient(app)
    _register(client, email="owner@example.com", username="Owner")
    member = _register(client, email="member@example.com", username="Member")
    old_token = member["accessToken"]
    _admin_client(client, email="owner@example.com", password="Initial123!")

    issued = client.post(
        f"/api/admin/users/{member['user']['id']}/password-reset-link",
        json={"reason": "user identity verified by support"},
    )
    assert issued.status_code == 200, issued.text
    assert issued.headers["cache-control"].startswith("no-store")
    reset_token = issued.json()["resetToken"]

    client.headers.pop("Authorization", None)
    completed = client.post(
        "/api/auth/password-reset/complete",
        json={"token": reset_token, "newPassword": "Updated123!"},
    )
    assert completed.status_code == 200, completed.text
    reused = client.post(
        "/api/auth/password-reset/complete",
        json={"token": reset_token, "newPassword": "Another123!"},
    )
    assert reused.status_code == 400

    old_login = client.post(
        "/api/auth/login",
        json={"userAccount": "member@example.com", "password": "Initial123!"},
    )
    assert old_login.status_code == 401
    _login(client, email="member@example.com", password="Updated123!")

    client.headers.update({"Authorization": f"Bearer {old_token}"})
    assert client.get("/api/auth/me").status_code == 401


def test_stale_admin_update_conflicts_and_audit_is_append_only() -> None:
    client = TestClient(app)
    _register(client, email="owner@example.com", username="Owner")
    member = _register(client, email="member@example.com", username="Member")
    _admin_client(client, email="owner@example.com", password="Initial123!")

    detail = client.get(f"/api/admin/users/{member['user']['id']}").json()
    updated = client.patch(
        f"/api/admin/users/{member['user']['id']}",
        json={
            "expectedUpdatedAt": detail["updatedAt"],
            "username": "Renamed Member",
            "reason": "verified profile correction",
        },
    )
    assert updated.status_code == 200, updated.text
    stale = client.patch(
        f"/api/admin/users/{member['user']['id']}",
        json={
            "expectedUpdatedAt": detail["updatedAt"],
            "username": "Stale Write",
            "reason": "simulate concurrent editor",
        },
    )
    assert stale.status_code == 409

    audit = client.get("/api/admin/audit-events")
    assert audit.status_code == 200, audit.text
    event = next(
        item
        for item in audit.json()["items"]
        if item["action"] == "user_profile_updated"
    )
    assert "passwordHash" not in event["before"]
    assert "passwordHash" not in event["after"]

    with _connect() as connection:
        try:
            connection.execute(
                "UPDATE admin_audit_events SET reason = ? WHERE eventId = ?",
                ("tampered", event["eventId"]),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("admin audit event unexpectedly allowed UPDATE")
