from __future__ import annotations

import asyncio
import threading

from fastapi import Request
from starlette.responses import Response

from app.auth.middleware import AuthMiddleware


def test_authoritative_principal_read_runs_off_event_loop(monkeypatch) -> None:
    event_loop_thread = threading.get_ident()
    principal_threads: list[int] = []

    monkeypatch.setattr(
        "app.auth.middleware.decode_access_token",
        lambda _token: {"sub": "7", "ver": 3},
    )

    def load_principal(user_id: int) -> dict:
        principal_threads.append(threading.get_ident())
        return {
            "id": user_id,
            "isDeleted": 0,
            "authVersion": 3,
            "userRole": "user",
        }

    monkeypatch.setattr("app.auth.middleware.get_auth_principal", load_principal)
    middleware = AuthMiddleware(lambda _scope, _receive, _send: None)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/investor-profile",
            "headers": [(b"authorization", b"Bearer test-token")],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }
    )

    async def call_next(_request: Request) -> Response:
        return Response(status_code=204)

    response = asyncio.run(middleware.dispatch(request, call_next))

    assert response.status_code == 204
    assert principal_threads
    assert principal_threads[0] != event_loop_thread
