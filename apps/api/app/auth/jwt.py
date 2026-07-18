from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.config import get_settings


def create_access_token(user_id: int, auth_version: int = 1) -> tuple[str, int]:
    settings = get_settings()
    expires_minutes = settings.jwt_access_expire_minutes
    expires_delta = timedelta(minutes=expires_minutes)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + expires_delta,
        "type": "access",
        "ver": int(auth_version),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, int(expires_delta.total_seconds())


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("invalid token type")
    return payload
