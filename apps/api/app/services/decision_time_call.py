from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.services.news_freshness import normalize_news_now


def call_with_optional_time(
    function: Callable[..., Any],
    *args: Any,
    keyword: str,
    decision_at: datetime,
    **kwargs: Any,
) -> Any:
    """Call a time-aware API while tolerating legacy adapters without the kwarg.

    The compatibility retry is intentionally limited to the precise Python error
    emitted for an unsupported keyword.  TypeErrors raised inside a real provider
    implementation are never swallowed.
    """

    try:
        return function(*args, **kwargs, **{keyword: decision_at})
    except TypeError as exc:
        message = str(exc)
        if "unexpected keyword argument" not in message or keyword not in message:
            raise
        return function(*args, **kwargs)


def empty_announcement_result(decision_at: datetime) -> dict[str, Any]:
    return {
        "items": [],
        "requested": 0,
        "ok": 0,
        "empty": 0,
        "error": 0,
        "timeout": 0,
        "coverage": 0.0,
        "evidence_coverage": 0.0,
        "fetched_at": normalize_news_now(decision_at).isoformat(),
        "requested_codes": [],
        "funds": [],
    }


def prefetch_fund_announcements_compat(
    service: object,
    fund_codes: list[str],
    *,
    decision_at: datetime,
) -> dict[str, Any]:
    method = getattr(service, "prefetch_fund_announcements", None)
    if not callable(method):
        return empty_announcement_result(decision_at)
    result = call_with_optional_time(
        method,
        fund_codes,
        keyword="now",
        decision_at=decision_at,
    )
    return result if isinstance(result, dict) else empty_announcement_result(decision_at)
