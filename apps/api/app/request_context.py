from __future__ import annotations

from contextvars import ContextVar, Token

_current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)


def get_request_user_id() -> int:
    user_id = _current_user_id.get()
    if user_id is None:
        raise RuntimeError("未设置当前用户上下文")
    return user_id


def try_get_request_user_id() -> int | None:
    return _current_user_id.get()


def set_request_user_id(user_id: int) -> Token:
    return _current_user_id.set(user_id)


def reset_request_user_id(token: Token) -> None:
    _current_user_id.reset(token)
