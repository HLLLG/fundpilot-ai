from __future__ import annotations

import asyncio
import re

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.auth.jwt import decode_access_token
from app.database import get_auth_principal
from app.request_context import reset_request_user_id, set_request_user_id

_PUBLIC_EXACT = {
    "/health",
    "/ready",
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/password-reset/complete",
    "/api/internal/factor-ic-snapshots",
    "/api/internal/factor-ic-universe-snapshots",
    "/api/internal/factor-ic-nav-observations",
    "/api/internal/factor-ic-nav-observations/query",
    "/docs",
    "/redoc",
    "/openapi.json",
}

_PUBLIC_PREFIXES = (
    "/api/trading-session",
)

#: 可选鉴权：无 token 或 token 失效也放行，但带了有效 token 就照常绑定身份。
#:
#: 浏览器错误上报必须免登录——登录/注册页自身崩溃时还没有 token，而那恰恰是用户
#: 最描述不清的故障。但把它当成完全公开会连已登录用户的身份一起丢掉，运维就看不出
#: "谁遇到了这个错"。端点自身有体积上限、按 IP 与全局限流、字段长度限制。
_OPTIONAL_AUTH_EXACT = {
    "/api/telemetry/client-errors",
}


def _is_public_path(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


def _extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    match = re.match(r"^Bearer\s+(.+)$", auth.strip(), re.IGNORECASE)
    return match.group(1).strip() if match else None


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path
        optional_auth = path in _OPTIONAL_AUTH_EXACT
        if request.method == "OPTIONS" or (
            not optional_auth and _is_public_path(path)
        ):
            return await call_next(request)

        def unauthenticated(detail: str) -> Response | None:
            """401 for protected routes; pass through for optional-auth ones."""

            if optional_auth:
                return None
            return JSONResponse(status_code=401, content={"detail": detail})

        token = _extract_bearer_token(request)
        if not token:
            rejection = unauthenticated("未登录")
            return rejection if rejection is not None else await call_next(request)

        try:
            payload = decode_access_token(token)
            user_id = int(payload["sub"])
            token_auth_version = int(payload.get("ver", 1))
        except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
            rejection = unauthenticated("登录已失效")
            return rejection if rejection is not None else await call_next(request)

        # PyMySQL/SQLite are synchronous. Keep the authoritative per-request
        # read (security changes must remain immediate across Uvicorn workers),
        # but do not block the event loop while the database is responding.
        principal = await asyncio.to_thread(get_auth_principal, user_id)
        if (
            principal is None
            or int(principal.get("isDeleted") or 0) == 1
            or int(principal.get("authVersion") or 1) != token_auth_version
        ):
            rejection = unauthenticated("登录已失效")
            return rejection if rejection is not None else await call_next(request)

        request.state.auth_principal = principal
        ctx_token = set_request_user_id(user_id)
        try:
            return await call_next(request)
        finally:
            reset_request_user_id(ctx_token)
