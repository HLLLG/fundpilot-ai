from __future__ import annotations

import re

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.auth.jwt import decode_access_token
from app.request_context import reset_request_user_id, set_request_user_id

_PUBLIC_EXACT = {
    "/health",
    "/api/auth/register",
    "/api/auth/login",
    "/docs",
    "/redoc",
    "/openapi.json",
}

_PUBLIC_PREFIXES = (
    "/api/trading-session",
)


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
        if request.method == "OPTIONS" or _is_public_path(request.url.path):
            return await call_next(request)

        token = _extract_bearer_token(request)
        if not token:
            return JSONResponse(status_code=401, content={"detail": "未登录"})

        try:
            payload = decode_access_token(token)
            user_id = int(payload["sub"])
        except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
            return JSONResponse(status_code=401, content={"detail": "登录已失效"})

        ctx_token = set_request_user_id(user_id)
        try:
            return await call_next(request)
        finally:
            reset_request_user_id(ctx_token)
