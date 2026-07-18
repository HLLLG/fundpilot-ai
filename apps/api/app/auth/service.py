from __future__ import annotations

from app.auth.jwt import create_access_token
from app.auth.models import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)
from app.auth.passwords import hash_password, verify_password
from app.auth.test_account_guard import assert_register_allowed
from app.database import (
    create_user,
    get_user_by_account,
    get_user_by_id,
    record_successful_login,
)

def _to_public(user: dict) -> UserPublic:
    return UserPublic(
        id=int(user["id"]),
        userRole=str(user["userRole"]),
        username=str(user["username"]),
        userAccount=str(user["userAccount"]),
        bio=str(user.get("bio") or ""),
        avatarUrl=str(user.get("avatarUrl") or ""),
    )


def register_user(body: RegisterRequest) -> TokenResponse:
    account = body.userAccount.strip().lower()
    assert_register_allowed(account)
    if get_user_by_account(account) is not None:
        raise ValueError("该邮箱已注册")
    username = body.username.strip() or account.split("@")[0]
    user = create_user(
        user_account=account,
        password_hash=hash_password(body.password),
        username=username,
    )
    access_token, expires_in = create_access_token(
        int(user["id"]), int(user.get("authVersion") or 1)
    )
    return TokenResponse(
        accessToken=access_token,
        expiresIn=expires_in,
        user=_to_public(user),
    )


def login_user(body: LoginRequest) -> TokenResponse:
    account = body.userAccount.strip().lower()
    user = get_user_by_account(account)
    if user is None or not verify_password(body.password, str(user["passwordHash"])):
        raise ValueError("邮箱或密码错误")
    if int(user.get("isDeleted") or 0) == 1:
        raise ValueError("账号已停用")
    user = record_successful_login(int(user["id"]))
    if user is None:
        raise ValueError("账号不可用")
    access_token, expires_in = create_access_token(
        int(user["id"]), int(user.get("authVersion") or 1)
    )
    return TokenResponse(
        accessToken=access_token,
        expiresIn=expires_in,
        user=_to_public(user),
    )


def get_current_user_public(user_id: int) -> UserPublic:
    user = get_user_by_id(user_id)
    if user is None or int(user.get("isDeleted") or 0) == 1:
        raise ValueError("用户不存在")
    return _to_public(user)
