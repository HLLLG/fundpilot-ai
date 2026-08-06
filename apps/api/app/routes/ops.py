"""Ops panel API: browser error ingest plus admin-only triage endpoints.

The ingest endpoint is deliberately unauthenticated. A JavaScript crash on the
login or register page is exactly the failure a user cannot describe, and it
happens before any token exists. Accepting anonymous reports is therefore a
requirement, not a convenience — so the endpoint is defended instead:

* a hard request-body cap, checked before the JSON is parsed;
* per-IP and process-wide rate limits;
* strict field-length limits on every accepted value;
* fingerprint grouping and a per-minute storage cap in the store itself.

Set ``FUND_AI_OPS_CLIENT_ERROR_INGEST_ENABLED=false`` to drop reports while
still answering ``202``, which stops abuse without a client-side deploy.
"""

from __future__ import annotations

import threading
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from app.config import get_settings
from app.services.ops_observability import (
    SOURCE_FRONTEND,
    VALID_GROUP_STATUSES,
    flush_ops_writes,
    get_error_group,
    list_error_groups,
    ops_capture_state,
    ops_overview,
    record_error_event,
    set_error_group_status,
)

router = APIRouter(tags=["ops"])

#: Anything larger is abuse or a runaway stack; 32 KiB holds a deep JS stack
#: plus a React component stack with room to spare.
MAX_CLIENT_REPORT_BYTES = 32 * 1024

_BreadcrumbText = Annotated[str, StringConstraints(max_length=200)]

_rate_lock = threading.Lock()
_rate_windows: dict[str, tuple[int, int]] = {}
_MAX_RATE_KEYS = 4096
_GLOBAL_RATE_KEY = "*"


class ClientErrorReport(BaseModel):
    """One browser-side failure. Every field is optional except the message."""

    model_config = {"extra": "ignore"}

    message: str = Field(min_length=1, max_length=2000)
    errorType: str = Field(default="Error", max_length=180)
    stack: str | None = Field(default=None, max_length=20_000)
    componentStack: str | None = Field(default=None, max_length=8000)
    level: Literal["warning", "error", "fatal"] = "error"
    kind: Literal[
        "window_error",
        "unhandled_rejection",
        "react_render",
        "resource_load",
        "api_failure",
        "manual",
    ] = "manual"
    path: str = Field(default="/", max_length=240)
    release: str | None = Field(default=None, max_length=120)
    requestId: str | None = Field(default=None, max_length=128)
    statusCode: int | None = Field(default=None, ge=0, le=599)
    viewport: str | None = Field(default=None, max_length=32)
    referrer: str | None = Field(default=None, max_length=500)
    # What the user did just before the crash. Callers must not put form values
    # or credentials here; the frontend only records route and API-failure hops.
    breadcrumbs: list[_BreadcrumbText] = Field(default_factory=list, max_length=20)


class ErrorGroupStatusRequest(BaseModel):
    status: Literal["open", "resolved", "ignored"]
    note: str | None = Field(default=None, max_length=500)


def _require_admin(request: Request) -> int:
    principal = request.scope.get("state", {}).get("auth_principal")
    if not isinstance(principal, dict) or str(principal.get("userRole")) != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以访问运维监控面板")
    return int(principal["id"])


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


def _client_key(request: Request) -> str:
    """Best-effort client identity for rate limiting.

    ``X-Forwarded-For`` is spoofable unless a trusted proxy rewrites it, so the
    per-IP limit is a courtesy bound; the process-wide limit is what actually
    caps the damage from a rotating attacker.
    """

    forwarded = str(request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:64]
    client = request.client
    return (client.host if client else "unknown")[:64]


def _consume_rate_budget(key: str, limit: int, minute: int) -> bool:
    """Fixed-window counter. Returns False once ``limit`` is exhausted."""

    if limit <= 0:
        return False
    window, count = _rate_windows.get(key, (minute, 0))
    if window != minute:
        window, count = minute, 0
    if count >= limit:
        _rate_windows[key] = (window, count)
        return False
    _rate_windows[key] = (window, count + 1)
    return True


def _allow_client_report(request: Request) -> bool:
    settings = get_settings()
    minute = int(time.time() // 60)
    with _rate_lock:
        if len(_rate_windows) > _MAX_RATE_KEYS:
            # Drop everything from closed windows rather than tracking an
            # unbounded set of source addresses.
            stale = [key for key, (window, _) in _rate_windows.items() if window != minute]
            for key in stale:
                _rate_windows.pop(key, None)
            if len(_rate_windows) > _MAX_RATE_KEYS:
                _rate_windows.clear()
        if not _consume_rate_budget(
            _GLOBAL_RATE_KEY,
            int(settings.ops_client_error_global_rate_limit_per_minute),
            minute,
        ):
            return False
        return _consume_rate_budget(
            _client_key(request),
            int(settings.ops_client_error_rate_limit_per_minute),
            minute,
        )


def reset_ops_rate_limit_for_tests() -> None:
    with _rate_lock:
        _rate_windows.clear()


@router.post("/api/telemetry/client-errors", status_code=202)
async def client_errors(request: Request, response: Response) -> dict[str, Any]:
    """Accept one browser error report.

    Always answers ``202`` for a well-formed request, whether or not the report
    was stored: the browser must not retry, and a client must not be able to
    probe capture settings.
    """

    _no_store(response)
    declared = str(request.headers.get("content-length") or "").strip()
    if declared.isdigit() and int(declared) > MAX_CLIENT_REPORT_BYTES:
        raise HTTPException(status_code=413, detail="上报内容过大")
    body = await request.body()
    if len(body) > MAX_CLIENT_REPORT_BYTES:
        raise HTTPException(status_code=413, detail="上报内容过大")

    try:
        report = ClientErrorReport.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="上报格式不正确") from exc

    if not get_settings().ops_client_error_ingest_enabled:
        return {"accepted": False, "fingerprint": None}
    if not _allow_client_report(request):
        # 429 lets the client back off; the reporter treats it as terminal.
        raise HTTPException(status_code=429, detail="上报过于频繁，请稍后再试")

    principal = request.scope.get("state", {}).get("auth_principal")
    user_id = principal.get("id") if isinstance(principal, dict) else None
    fingerprint = record_error_event(
        source=SOURCE_FRONTEND,
        error_type=report.errorType,
        message=report.message,
        # The React component stack names the failing component, which the raw
        # JS stack usually cannot after minification. Keep both.
        stack=(
            f"{report.stack}\n\n--- React component stack ---{report.componentStack}"
            if report.stack and report.componentStack
            else (report.stack or report.componentStack)
        ),
        level=report.level,
        route=report.path,
        status_code=report.statusCode,
        request_id=report.requestId,
        user_id=user_id,
        release=report.release,
        user_agent=request.headers.get("user-agent"),
        context={
            "kind": report.kind,
            "viewport": report.viewport,
            "referrer": report.referrer,
            "breadcrumbs": report.breadcrumbs or None,
        },
    )
    return {"accepted": fingerprint is not None, "fingerprint": fingerprint}


@router.get("/api/admin/ops/overview")
def admin_ops_overview(
    response: Response,
    hours: int = Query(default=24, ge=1, le=168),
    _actor_id: int = Depends(_require_admin),
) -> dict[str, Any]:
    _no_store(response)
    # Flush first: the in-progress minute and any queued errors are still in
    # memory, and a panel that omits the last minute looks broken during an
    # incident. Sync endpoints run in a worker thread, so this cannot block
    # the event loop.
    flush_ops_writes()
    return ops_overview(hours=hours)


@router.get("/api/admin/ops/errors")
def admin_ops_errors(
    response: Response,
    hours: int = Query(default=24, ge=1, le=168),
    source: Literal["all", "frontend", "backend", "worker"] = "all",
    status: Literal["all", "open", "resolved", "ignored"] = "open",
    query: str = Query(default="", max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _actor_id: int = Depends(_require_admin),
) -> dict[str, Any]:
    _no_store(response)
    flush_ops_writes()
    return list_error_groups(
        hours=hours,
        source=source,
        status=status,
        query=query,
        page=page,
        page_size=page_size,
    )


@router.get("/api/admin/ops/errors/{fingerprint}")
def admin_ops_error_detail(
    fingerprint: str,
    response: Response,
    hours: int = Query(default=168, ge=1, le=168),
    event_limit: int = Query(default=20, ge=1, le=100),
    _actor_id: int = Depends(_require_admin),
) -> dict[str, Any]:
    _no_store(response)
    flush_ops_writes()
    detail = get_error_group(fingerprint, hours=hours, event_limit=event_limit)
    if detail is None:
        raise HTTPException(status_code=404, detail="错误分组不存在")
    return detail


@router.post("/api/admin/ops/errors/{fingerprint}/status")
def admin_ops_set_error_status(
    fingerprint: str,
    body: ErrorGroupStatusRequest,
    response: Response,
    actor_id: int = Depends(_require_admin),
) -> dict[str, Any]:
    _no_store(response)
    if body.status not in VALID_GROUP_STATUSES:
        raise HTTPException(status_code=400, detail="状态取值不合法")
    updated = set_error_group_status(
        fingerprint,
        status=body.status,
        actor_id=actor_id,
        note=body.note,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="错误分组不存在")
    return updated


@router.get("/api/admin/ops/capture")
def admin_ops_capture(
    response: Response,
    _actor_id: int = Depends(_require_admin),
) -> dict[str, Any]:
    """Health of the telemetry pipeline itself (drops, queue depth, writer)."""

    _no_store(response)
    return ops_capture_state()
