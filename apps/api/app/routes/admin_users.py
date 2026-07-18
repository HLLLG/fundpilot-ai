from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.request_context import get_request_user_id
from app.services.admin_user_management import (
    AdminConflict,
    AdminForbidden,
    AdminManagementError,
    AdminNotFound,
    complete_password_reset,
    create_password_reset_token,
    get_user_detail,
    get_user_summary,
    list_audit_events,
    list_users,
    revoke_user_sessions,
    set_user_enabled,
    update_user,
)


router = APIRouter(tags=["admin-users"])


class UserUpdateRequest(BaseModel):
    expectedUpdatedAt: str = Field(min_length=1, max_length=64)
    username: str | None = Field(default=None, min_length=1, max_length=64)
    userRole: Literal["user", "admin"] | None = None
    reason: str = Field(min_length=3, max_length=500)


class UserStateRequest(BaseModel):
    expectedUpdatedAt: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=3, max_length=500)


class AdminReasonRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class PasswordResetCompleteRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    newPassword: str = Field(min_length=8, max_length=128)


class UserSearchRequest(BaseModel):
    query: str = Field(default="", max_length=128)
    role: Literal["all", "user", "admin"] = "all"
    status: Literal["all", "active", "disabled"] = "all"
    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=20, ge=1, le=100)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"


def _require_admin(request: Request) -> int:
    principal = getattr(request.state, "auth_principal", None)
    if not isinstance(principal, dict) or str(principal.get("userRole")) != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可以访问用户管理中心")
    return get_request_user_id()


def _raise_admin_error(exc: AdminManagementError) -> None:
    if isinstance(exc, AdminNotFound):
        status_code = 404
    elif isinstance(exc, AdminForbidden):
        status_code = 403
    elif isinstance(exc, AdminConflict):
        status_code = 409
    else:
        status_code = 400
    raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/api/admin/users/summary")
def admin_user_summary(
    response: Response,
    _actor_id: int = Depends(_require_admin),
) -> dict:
    _no_store(response)
    return get_user_summary()


@router.get("/api/admin/users")
def admin_list_users(
    response: Response,
    role: Literal["all", "user", "admin"] = "all",
    status: Literal["all", "active", "disabled"] = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _actor_id: int = Depends(_require_admin),
) -> dict:
    _no_store(response)
    return list_users(
        query="",
        role=role,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.post("/api/admin/users/search")
def admin_search_users(
    body: UserSearchRequest,
    response: Response,
    _actor_id: int = Depends(_require_admin),
) -> dict:
    """Search from a request body so email terms never enter access-log URLs."""

    _no_store(response)
    return list_users(
        query=body.query,
        role=body.role,
        status=body.status,
        page=body.page,
        page_size=body.pageSize,
    )


@router.get("/api/admin/users/{user_id}")
def admin_get_user(
    user_id: int,
    response: Response,
    _actor_id: int = Depends(_require_admin),
) -> dict:
    _no_store(response)
    try:
        return get_user_detail(user_id)
    except AdminManagementError as exc:
        _raise_admin_error(exc)


@router.patch("/api/admin/users/{user_id}")
def admin_update_user(
    user_id: int,
    body: UserUpdateRequest,
    response: Response,
    actor_id: int = Depends(_require_admin),
) -> dict:
    _no_store(response)
    try:
        return update_user(
            actor_user_id=actor_id,
            target_user_id=user_id,
            expected_updated_at=body.expectedUpdatedAt,
            username=body.username,
            user_role=body.userRole,
            reason=body.reason,
        )
    except AdminManagementError as exc:
        _raise_admin_error(exc)


@router.post("/api/admin/users/{user_id}/disable")
def admin_disable_user(
    user_id: int,
    body: UserStateRequest,
    response: Response,
    actor_id: int = Depends(_require_admin),
) -> dict:
    _no_store(response)
    try:
        return set_user_enabled(
            actor_user_id=actor_id,
            target_user_id=user_id,
            enabled=False,
            expected_updated_at=body.expectedUpdatedAt,
            reason=body.reason,
        )
    except AdminManagementError as exc:
        _raise_admin_error(exc)


@router.post("/api/admin/users/{user_id}/restore")
def admin_restore_user(
    user_id: int,
    body: UserStateRequest,
    response: Response,
    actor_id: int = Depends(_require_admin),
) -> dict:
    _no_store(response)
    try:
        return set_user_enabled(
            actor_user_id=actor_id,
            target_user_id=user_id,
            enabled=True,
            expected_updated_at=body.expectedUpdatedAt,
            reason=body.reason,
        )
    except AdminManagementError as exc:
        _raise_admin_error(exc)


@router.post("/api/admin/users/{user_id}/revoke-sessions")
def admin_revoke_sessions(
    user_id: int,
    body: AdminReasonRequest,
    response: Response,
    actor_id: int = Depends(_require_admin),
) -> dict:
    _no_store(response)
    try:
        return revoke_user_sessions(
            actor_user_id=actor_id,
            target_user_id=user_id,
            reason=body.reason,
        )
    except AdminManagementError as exc:
        _raise_admin_error(exc)


@router.post("/api/admin/users/{user_id}/password-reset-link")
def admin_password_reset_link(
    user_id: int,
    body: AdminReasonRequest,
    response: Response,
    actor_id: int = Depends(_require_admin),
) -> dict:
    _no_store(response)
    try:
        return create_password_reset_token(
            actor_user_id=actor_id,
            target_user_id=user_id,
            reason=body.reason,
        )
    except AdminManagementError as exc:
        _raise_admin_error(exc)


@router.get("/api/admin/audit-events")
def admin_audit_events(
    response: Response,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _actor_id: int = Depends(_require_admin),
) -> dict:
    _no_store(response)
    return list_audit_events(page=page, page_size=page_size)


@router.post("/api/auth/password-reset/complete")
def auth_password_reset_complete(
    body: PasswordResetCompleteRequest,
    response: Response,
) -> dict[str, bool]:
    _no_store(response)
    try:
        complete_password_reset(raw_token=body.token, new_password=body.newPassword)
    except AdminManagementError as exc:
        raise HTTPException(status_code=400, detail="重置链接无效或已过期") from exc
    return {"ok": True}
