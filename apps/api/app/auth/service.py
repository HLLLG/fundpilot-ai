from __future__ import annotations

from app.auth.cloudbase_auth import resolve_cloudbase_uid, resolve_trusted_wechat_openid
from app.auth.jwt import create_access_token
from app.auth.models import (
    BindWechatRequest,
    LinkEmailRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
    WechatLoginRequest,
)
from app.auth.passwords import hash_password, verify_password
from app.database import (
    bind_user_cloudbase_uid,
    create_user,
    create_wechat_user,
    get_user_by_account,
    get_user_by_cloudbase_uid,
    get_user_by_id,
    merge_wechat_account_into_email_user,
)

_WECHAT_ACCOUNT_SUFFIX = "@wechat.fundpilot"


def _to_public(user: dict) -> UserPublic:
    return UserPublic(
        id=int(user["id"]),
        userRole=str(user["userRole"]),
        username=str(user["username"]),
        userAccount=str(user["userAccount"]),
        bio=str(user.get("bio") or ""),
        avatarUrl=str(user.get("avatarUrl") or ""),
        wechatBound=bool(user.get("cloudbaseUid")),
    )


def register_user(body: RegisterRequest) -> TokenResponse:
    account = body.userAccount.strip().lower()
    if get_user_by_account(account) is not None:
        raise ValueError("该邮箱已注册")
    username = body.username.strip() or account.split("@")[0]
    user = create_user(
        user_account=account,
        password_hash=hash_password(body.password),
        username=username,
    )
    access_token, expires_in = create_access_token(int(user["id"]))
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
    access_token, expires_in = create_access_token(int(user["id"]))
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


def _issue_token_for_user(user: dict) -> TokenResponse:
    access_token, expires_in = create_access_token(int(user["id"]))
    return TokenResponse(
        accessToken=access_token,
        expiresIn=expires_in,
        user=_to_public(user),
    )


def wechat_login_user(
    body: WechatLoginRequest,
    *,
    trusted_wechat_openid: str | None = None,
) -> TokenResponse:
    if trusted_wechat_openid:
        cloudbase_uid = trusted_wechat_openid
    else:
        cloudbase_uid = resolve_cloudbase_uid(
            cloudbase_uid=body.cloudbaseUid,
            cloudbase_access_token=body.cloudbaseAccessToken,
            cloudbase_ticket=body.cloudbaseTicket,
        )
    user = get_user_by_cloudbase_uid(cloudbase_uid)
    if user is None:
        user = create_wechat_user(cloudbase_uid=cloudbase_uid, username=body.username)
    if int(user.get("isDeleted") or 0) == 1:
        raise ValueError("账号已停用")
    return _issue_token_for_user(user)


def bind_wechat_user(user_id: int, body: BindWechatRequest) -> UserPublic:
    cloudbase_uid = resolve_cloudbase_uid(
        cloudbase_uid=body.cloudbaseUid,
        cloudbase_access_token=body.cloudbaseAccessToken,
        cloudbase_ticket=body.cloudbaseTicket,
    )
    user = bind_user_cloudbase_uid(user_id, cloudbase_uid)
    return _to_public(user)


def link_email_account(current_user_id: int, body: LinkEmailRequest) -> TokenResponse:
    """小程序微信账号关联已有邮箱账号。

    `current_user_id` 来自微信登录签发的 JWT（占位微信账号）。校验邮箱密码后，
    把占位账号上的 cloudbaseUid 迁移到邮箱账号并软删占位账号，再为邮箱账号重新
    签发 JWT。之后每次微信登录都会命中邮箱账号。
    """
    wechat_user = get_user_by_id(current_user_id)
    if wechat_user is None:
        raise ValueError("当前登录态无效")

    account = str(wechat_user.get("userAccount") or "")
    if not account.endswith(_WECHAT_ACCOUNT_SUFFIX) or not wechat_user.get("cloudbaseUid"):
        raise ValueError("仅微信登录账号可关联邮箱")

    email_account = body.userAccount.strip().lower()
    email_user = get_user_by_account(email_account)
    if email_user is None or not verify_password(
        body.password, str(email_user["passwordHash"])
    ):
        raise ValueError("邮箱或密码错误")
    if int(email_user.get("isDeleted") or 0) == 1:
        raise ValueError("账号已停用")
    if int(email_user["id"]) == current_user_id:
        raise ValueError("无法关联到自身账号")

    merged = merge_wechat_account_into_email_user(
        current_user_id, int(email_user["id"])
    )
    return _issue_token_for_user(merged)
