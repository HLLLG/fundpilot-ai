"""Capture every ERROR-level log as a durable ops error event.

Hooking the logging system rather than only the HTTP 500 handler is deliberate.
``unhandled_exception_handler`` already calls ``logger.exception(...)``, so a
single capture point covers request failures *and* the background worker,
scheduled jobs, and startup bootstrap — none of which have a request to hook.

Two properties matter more than completeness here:

* **No recursion.** Persisting an event can log; capturing that log would loop.
  Records from the telemetry modules themselves are skipped, and
  ``record_error_event`` additionally refuses reentrant calls per thread.
* **No blocking.** ``emit`` only appends to a bounded in-memory queue; a
  daemon thread performs the database write.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

from app.config import get_settings
from app.services.ops_observability import (
    OPS_INTERNAL_LOGGER_PREFIXES,
    SOURCE_BACKEND,
    SOURCE_WORKER,
    record_error_event,
)
from app.services.performance_metrics import current_request_context

_MAX_LOGGED_MESSAGE_CHARS = 2000
_handler: "OpsErrorLogHandler | None" = None

#: Set via ``logger.exception(..., extra={SKIP_CAPTURE_FLAG: True})`` by call
#: sites that already recorded the failure themselves with richer context.
SKIP_CAPTURE_FLAG = "ops_skip_capture"


def _level_name(levelno: int) -> str:
    if levelno >= logging.CRITICAL:
        return "fatal"
    if levelno >= logging.ERROR:
        return "error"
    return "warning"


class OpsErrorLogHandler(logging.Handler):
    """Route ERROR and CRITICAL records into the ops error store."""

    def __init__(self, level: int = logging.ERROR) -> None:
        super().__init__(level=level)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._capture(record)
        except Exception:  # noqa: BLE001 - a log handler must never raise.
            pass

    def _capture(self, record: logging.LogRecord) -> None:
        name = str(record.name or "")
        if name.startswith(OPS_INTERNAL_LOGGER_PREFIXES):
            return
        if getattr(record, SKIP_CAPTURE_FLAG, False):
            return
        if not get_settings().ops_error_capture_enabled:
            return

        error_type, message, stack = _describe(record)
        request = current_request_context()
        source = SOURCE_BACKEND if request else _idle_source()
        context: dict[str, Any] = {
            "logger": name,
            "module": getattr(record, "module", None),
            "function": getattr(record, "funcName", None),
            "lineno": getattr(record, "lineno", None),
            "thread": getattr(record, "threadName", None),
            "process": getattr(record, "process", None),
        }
        task_name = getattr(record, "taskName", None)
        if task_name:
            context["task"] = task_name

        record_error_event(
            source=source,
            error_type=error_type,
            message=message,
            stack=stack,
            level=_level_name(record.levelno),
            route=request.get("path"),
            method=request.get("method"),
            status_code=request.get("status_code"),
            request_id=request.get("request_id"),
            context=context,
        )


def _idle_source() -> str:
    """Attribute non-request errors to the worker when this process is one."""

    try:
        return (
            SOURCE_WORKER
            if get_settings().runtime_role == "worker"
            else SOURCE_BACKEND
        )
    except Exception:  # noqa: BLE001
        return SOURCE_BACKEND


def _describe(record: logging.LogRecord) -> tuple[str, str, str | None]:
    """Derive (error_type, message, stack) from a log record."""

    try:
        rendered = record.getMessage()
    except Exception:  # noqa: BLE001 - bad format args must not lose the event.
        rendered = str(record.msg)
    rendered = rendered[:_MAX_LOGGED_MESSAGE_CHARS]

    exc_info = record.exc_info
    if exc_info and exc_info[0] is not None:
        exc_type, exc_value, exc_tb = exc_info
        stack = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        detail = str(exc_value or "").strip()
        # Keep the log line as context for the exception text: the log call
        # usually names the operation ("Unhandled error on GET /x") while the
        # exception names the cause.
        message = f"{rendered}: {detail}" if detail and detail != rendered else rendered
        return (exc_type.__name__, message[:_MAX_LOGGED_MESSAGE_CHARS], stack)

    if record.stack_info:
        return (f"LoggedError({record.name})", rendered, str(record.stack_info))
    # No exception attached: the logger name is the only stable grouping key,
    # so it becomes part of the type rather than being buried in context.
    return (f"LoggedError({record.name})", rendered, None)


def capture_request_failure(
    request: Any,
    exc: BaseException,
    *,
    status_code: int = 500,
) -> str | None:
    """Record an unhandled request failure and return its request id.

    Called from the global 500 handler, which runs in ``ServerErrorMiddleware``
    — outside ``PerformanceMetricsMiddleware`` — so the per-request contextvar
    has already been reset. The ASGI scope state survives, and it also carries
    the authenticated principal, giving this path richer context than a plain
    log capture: the operator learns *which user* hit the failure.
    """

    try:
        scope = getattr(request, "scope", None) or {}
        state = scope.get("state") if isinstance(scope, dict) else None
        state = state if isinstance(state, dict) else {}
        request_id = state.get("request_id")
        principal = state.get("auth_principal")
        user_id = principal.get("id") if isinstance(principal, dict) else None
        path = str(getattr(getattr(request, "url", None), "path", "") or "")
        record_error_event(
            source=SOURCE_BACKEND,
            error_type=type(exc).__name__,
            message=str(exc).strip() or type(exc).__name__,
            stack="".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
            level="error",
            route=path,
            method=getattr(request, "method", None),
            status_code=status_code,
            request_id=request_id,
            user_id=user_id,
            user_agent=_header(request, "user-agent"),
            context={
                "handler": "unhandled_exception_handler",
                "referer": _header(request, "referer"),
            },
        )
        return str(request_id) if request_id else None
    except Exception:  # noqa: BLE001 - never shadow the original failure.
        return None


def _header(request: Any, name: str) -> str | None:
    try:
        return request.headers.get(name)
    except Exception:  # noqa: BLE001
        return None


def install_ops_error_log_handler() -> None:
    """Attach the capture handler to the root logger. Idempotent."""

    global _handler
    if _handler is not None:
        return
    handler = OpsErrorLogHandler()
    logging.getLogger().addHandler(handler)
    _handler = handler


def uninstall_ops_error_log_handler() -> None:
    """Detach the capture handler. Idempotent."""

    global _handler
    handler, _handler = _handler, None
    if handler is None:
        return
    logging.getLogger().removeHandler(handler)
    handler.close()


def ops_error_log_handler_installed() -> bool:
    return _handler is not None


__all__ = [
    "OpsErrorLogHandler",
    "SKIP_CAPTURE_FLAG",
    "capture_request_failure",
    "install_ops_error_log_handler",
    "ops_error_log_handler_installed",
    "uninstall_ops_error_log_handler",
]
