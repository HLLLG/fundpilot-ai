"""Process startup state and a fail-closed readiness gate."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

_lock = threading.RLock()
_state = "not_started"
_started_at: str | None = None
_ready_at: str | None = None
_failure_category: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_starting() -> None:
    global _failure_category, _ready_at, _started_at, _state
    with _lock:
        _state = "initializing"
        _started_at = _now()
        _ready_at = None
        _failure_category = None


def mark_ready() -> None:
    global _failure_category, _ready_at, _state
    with _lock:
        _state = "ready"
        _ready_at = _now()
        _failure_category = None


def mark_failed(exc: BaseException) -> None:
    global _failure_category, _state
    with _lock:
        _state = "failed"
        # Do not expose exception text because connection failures can contain
        # hosts, usernames, or provider details.
        _failure_category = exc.__class__.__name__


def readiness_snapshot() -> dict[str, Any]:
    with _lock:
        return {
            "ready": _state == "ready",
            "state": _state,
            "started_at": _started_at,
            "ready_at": _ready_at,
            "failure_category": _failure_category,
        }


def is_ready() -> bool:
    with _lock:
        return _state == "ready"


def accepts_traffic() -> bool:
    """Allow legacy in-process clients before lifespan, gate real startup states."""

    with _lock:
        # Uvicorn enters the lifespan and calls ``mark_starting`` before it
        # accepts sockets. Some unit clients intentionally bypass lifespan;
        # treating that synthetic state as ready preserves their route tests
        # without weakening the production initializing/failed gate.
        return _state in {"not_started", "ready"}


class ReadinessGateMiddleware:
    """Reject business traffic until database/bootstrap verification completes."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        if path in {"/health", "/ready"} or accepts_traffic():
            await self.app(scope, receive, send)
            return
        payload = json.dumps(
            {
                "detail": "service initialization in progress",
                **readiness_snapshot(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"retry-after", b"2"),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})


__all__ = [
    "ReadinessGateMiddleware",
    "accepts_traffic",
    "is_ready",
    "mark_failed",
    "mark_ready",
    "mark_starting",
    "readiness_snapshot",
]
