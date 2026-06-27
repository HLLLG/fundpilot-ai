from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from app.request_context import reset_request_user_id, set_request_user_id

_T = TypeVar("_T")


def run_with_request_user(user_id: int, fn: Callable[[], _T]) -> _T:
    token = set_request_user_id(user_id)
    try:
        return fn()
    finally:
        reset_request_user_id(token)
