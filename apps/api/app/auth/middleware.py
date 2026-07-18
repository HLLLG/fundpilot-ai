from __future__ import annotations

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
    "/api/auth/register",
    "/api/auth/login",
    "/api/auth/password-reset/complete",
    "/api/internal/factor-ic-snapshots",
    "/api/internal/factor-ic-universe-snapshots",
    "/api/internal/factor-ic-nav-observations",
    "/api/internal/factor-ic-nav-observations/query",
    "/api/internal/decision-quality/evaluations/latest",
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
            token_auth_version = int(payload.get("ver", 1))
        except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
            return JSONResponse(status_code=401, content={"detail": "登录已失效"})

        principal = get_auth_principal(user_id)
        if (
            principal is None
            or int(principal.get("isDeleted") or 0) == 1
            or int(principal.get("authVersion") or 1) != token_auth_version
        ):
            return JSONResponse(status_code=401, content={"detail": "登录已失效"})

        request.state.auth_principal = principal
        ctx_token = set_request_user_id(user_id)
        try:
            return await call_next(request)
        finally:
            reset_request_user_id(ctx_token)
