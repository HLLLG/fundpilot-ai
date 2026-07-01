"""Lightweight hypothesis-driven debug logging helper.

Used by ad-hoc "#region agent log" / "#endregion" instrumentation blocks added
while investigating a specific bug (see systematic-debugging workflow). Safe to
leave in place: at info/debug log level it has negligible overhead and can be
removed once the underlying investigation is closed out.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("app.debug_probe")


def debug_log(
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    hypothesis_id: str | None = None,
) -> None:
    payload = data or {}
    if hypothesis_id:
        logger.info("[debug_probe][%s][%s] %s | %r", hypothesis_id, location, message, payload)
    else:
        logger.info("[debug_probe][%s] %s | %r", location, message, payload)
