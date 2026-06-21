from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import httpx
import jwt

from app.config import get_settings


def _load_custom_login_private_key() -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.cloudbase_custom_login_key_path:
        return None
    path = Path(settings.cloudbase_custom_login_key_path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def verify_custom_ticket(ticket: str) -> str:
    """验证 CloudBase 自定义登录 ticket，返回 customUserId（此处为 cloudbaseUid）。"""
    key_data = _load_custom_login_private_key()
    if key_data is None:
        raise ValueError("未配置 CloudBase 自定义登录私钥")
    private_key = key_data.get("private_key") or key_data.get("privateKey")
    if not private_key:
        raise ValueError("CloudBase 私钥文件格式无效")
    payload = jwt.decode(ticket, private_key, algorithms=["RS256"], options={"verify_aud": False})
    custom_user_id = payload.get("customUserId") or payload.get("uid")
    if not custom_user_id:
        raise ValueError("ticket 中缺少用户标识")
    return str(custom_user_id)


def verify_cloudbase_access_token(access_token: str) -> str:
    """调用 CloudBase 用户信息接口校验 access token，返回 uid。"""
    settings = get_settings()
    env_id = settings.cloudbase_env_id
    if not env_id:
        raise ValueError("未配置 FUND_AI_CLOUDBASE_ENV_ID")
    base = settings.cloudbase_api_base_url.rstrip("/")
    url = f"{base}/auth/v1/user/me"
    headers = {"Authorization": f"Bearer {access_token}"}
    with httpx.Client(timeout=8.0) as client:
        response = client.get(url, headers=headers)
    if response.status_code != 200:
        raise ValueError("CloudBase 登录态无效")
    body = response.json()
    uid = body.get("uid") or body.get("sub") or (body.get("data") or {}).get("uid")
    if not uid:
        raise ValueError("无法解析 CloudBase 用户")
    return str(uid)


def resolve_trusted_wechat_openid(headers: Mapping[str, str]) -> str | None:
    """callContainer 经微信网关注入 openid；公网直连不可伪造此链路。"""
    settings = get_settings()
    if not settings.cloudbase_env_id:
        return None
    normalized = {str(key).lower(): value for key, value in headers.items()}
    openid = normalized.get("x-wx-openid")
    env_id = normalized.get("x-wx-env-id")
    if not openid:
        return None
    if env_id and env_id != settings.cloudbase_env_id:
        return None
    return str(openid).strip()


def resolve_cloudbase_uid(
    *,
    cloudbase_uid: str | None = None,
    cloudbase_access_token: str | None = None,
    cloudbase_ticket: str | None = None,
) -> str:
    settings = get_settings()
    if cloudbase_ticket:
        return verify_custom_ticket(cloudbase_ticket)
    if cloudbase_access_token:
        try:
            return verify_cloudbase_access_token(cloudbase_access_token)
        except (ValueError, httpx.HTTPError, jwt.InvalidTokenError):
            if not settings.cloudbase_auth_dev_mode:
                raise
    if cloudbase_uid and settings.cloudbase_auth_dev_mode:
        return cloudbase_uid.strip()
    raise ValueError("微信登录验证失败，请检查 CloudBase 配置")
