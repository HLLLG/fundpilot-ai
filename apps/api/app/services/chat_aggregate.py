"""Pure aggregation helper for non-streaming (`/chat/sync`) endpoints.

The追问 (follow-up chat) SSE generators (`stream_report_chat` and the荐基
counterpart) ``yield`` a sequence of JSON event payloads. ``wx.request`` in the
小程序 cannot consume ``text/event-stream`` responses, so the future
``POST .../chat/sync`` endpoints reuse those same generators but aggregate the
streamed events into a single JSON response on the server side.

``aggregate_chat_stream`` performs that aggregation as a *pure*, importable,
side-effect-free function so it can be unit/property tested in isolation
(Property 14). It does **not** touch the existing SSE routes, so the Web
experience is unaffected.

Event shapes produced by the generators / route layer:

* ``{"type": "user_message", "message": {...}}``
* ``{"type": "status", "content": "..."}``        (ignored)
* ``{"type": "token", "content": "..."}``          (accumulated)
* ``{"type": "done", "message": {...}, "chat_mode": ..., "model": ...}``
* ``{"type": "error", "message": "..."}``          (raises ``ValueError``)

Events may be passed as plain ``dict`` objects or as the JSON ``str`` payloads
the generators yield (optionally prefixed with ``data:`` as in raw SSE frames).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = ["AggregatedChat", "aggregate_chat_stream"]


@dataclass
class AggregatedChat:
    """Result of aggregating a追问 event stream.

    Attributes:
        content: Full assistant text, the in-order concatenation of every
            ``token`` event's ``content``.
        message: The final assistant message object carried by the ``done``
            event, or ``None`` if no ``done`` event was seen.
        user_message: The user message object carried by the ``user_message``
            event, or ``None`` if absent.
        chat_mode: The ``chat_mode`` echoed by the ``done`` event, if present.
        model: The ``model`` echoed by the ``done`` event, if present.
    """

    content: str
    message: dict[str, Any] | None = None
    user_message: dict[str, Any] | None = None
    chat_mode: Any | None = None
    model: Any | None = None


def _parse_event(event: Any) -> dict[str, Any]:
    """Normalize a raw event into a ``dict``.

    Accepts ``dict`` events directly and decodes ``str``/``bytes`` JSON
    payloads (tolerating a leading ``data:`` SSE prefix and ``[DONE]``
    sentinels). Anything unparseable yields an empty ``dict`` so callers can
    skip it.
    """

    if isinstance(event, dict):
        return event
    if isinstance(event, (bytes, bytearray)):
        try:
            event = event.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if isinstance(event, str):
        payload = event.strip()
        if payload.startswith("data:"):
            payload = payload[5:].strip()
        if not payload or payload == "[DONE]":
            return {}
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _error_text(event: dict[str, Any]) -> str:
    """Extract a human-readable error string from an ``error`` event."""

    for key in ("message", "content", "error", "detail"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "追问失败"


def aggregate_chat_stream(events: Iterable[Any]) -> AggregatedChat:
    """Aggregate a追问 event stream into a single response object.

    Iterates ``events`` (a generator/iterable of追问 events), accumulating
    ``token`` content, capturing the ``done`` event's final message, and the
    ``user_message`` event. If an ``error`` event is encountered, a
    :class:`ValueError` carrying its text is raised (the ``/chat/sync``
    endpoints map this to HTTP 400).

    Args:
        events: Iterable of追问 events, either ``dict`` objects or JSON
            string payloads as produced by the SSE generators.

    Returns:
        An :class:`AggregatedChat` with the full assistant ``content`` and the
        final ``message`` / ``user_message`` / ``chat_mode`` / ``model``.

    Raises:
        ValueError: If the stream contains an ``error`` event.
    """

    parts: list[str] = []
    final_message: dict[str, Any] | None = None
    user_message: dict[str, Any] | None = None
    chat_mode: Any | None = None
    model: Any | None = None

    for raw in events:
        event = _parse_event(raw)
        if not event:
            continue

        event_type = event.get("type")
        if event_type == "token":
            content = event.get("content")
            if isinstance(content, str):
                parts.append(content)
        elif event_type == "done":
            message = event.get("message")
            if isinstance(message, dict):
                final_message = message
            if "chat_mode" in event:
                chat_mode = event.get("chat_mode")
            if "model" in event:
                model = event.get("model")
        elif event_type == "error":
            raise ValueError(_error_text(event))
        elif event_type == "user_message":
            message = event.get("message")
            if isinstance(message, dict):
                user_message = message
        # "status" and any unknown event types are intentionally ignored.

    return AggregatedChat(
        content="".join(parts),
        message=final_message,
        user_message=user_message,
        chat_mode=chat_mode,
        model=model,
    )
