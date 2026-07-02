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


class TokenResponse(BaseModel):
    accessToken: str
    expiresIn: int
    user: UserPublic
