from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    userAccount: EmailStr
    password: str = Field(min_length=8, max_length=128)
    username: str = Field(default="", max_length=64)


class LoginRequest(BaseModel):
    userAccount: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserPublic(BaseModel):
    id: int
    userRole: str
    username: str
    userAccount: str
    bio: str = ""
    avatarUrl: str = ""
    wechatBound: bool = False


class TokenResponse(BaseModel):
    accessToken: str
    expiresIn: int
    user: UserPublic


class WechatLoginRequest(BaseModel):
    cloudbaseUid: str | None = None
    cloudbaseAccessToken: str | None = None
    cloudbaseTicket: str | None = None
    username: str = Field(default="", max_length=64)


class BindWechatRequest(BaseModel):
    cloudbaseUid: str | None = None
    cloudbaseAccessToken: str | None = None
    cloudbaseTicket: str | None = None


class LinkEmailRequest(BaseModel):
    """小程序微信账号关联已有邮箱账号（合并到邮箱账号并迁移微信标识）。"""

    userAccount: EmailStr
    password: str = Field(min_length=1, max_length=128)
