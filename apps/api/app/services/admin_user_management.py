from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.auth.passwords import hash_password
from app.database import _connect


_SYSTEM_MIGRATION_ACCOUNT = "migration@local"


class AdminManagementError(ValueError):
    pass


class AdminNotFound(AdminManagementError):
    pass


class AdminConflict(AdminManagementError):
    pass


class AdminForbidden(AdminManagementError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_dict(row: object) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def _begin_write(connection: Any) -> None:
    if str(getattr(connection, "dialect", "sqlite")) == "sqlite":
        connection.execute("BEGIN IMMEDIATE")


def _for_update(connection: Any) -> str:
    return " FOR UPDATE" if getattr(connection, "dialect", "sqlite") == "mysql" else ""


def _load_target(connection: Any, target_user_id: int) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, userRole, username, userAccount, bio, avatarUrl,
               createdAt, updatedAt, isDeleted, deletedAt, authVersion,
               lastLoginAt, lastActiveAt, passwordUpdatedAt
        FROM users
        WHERE id = ?
        """ + _for_update(connection),
        (target_user_id,),
    ).fetchone()
    target = _row_dict(row)
    if not target:
        raise AdminNotFound("用户不存在")
    if str(target.get("userAccount") or "").lower() == _SYSTEM_MIGRATION_ACCOUNT:
        raise AdminNotFound("用户不存在")
    return target


def _lock_actor_and_target(
    connection: Any,
    *,
    actor_user_id: int,
    target_user_id: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Lock account rows in ID order to avoid cross-admin deadlocks."""

    user_ids = sorted({actor_user_id, target_user_id})
    placeholders = ", ".join("?" for _ in user_ids)
    rows = connection.execute(
        f"""
        SELECT id, userRole, username, userAccount, bio, avatarUrl,
               createdAt, updatedAt, isDeleted, deletedAt, authVersion,
               lastLoginAt, lastActiveAt, passwordUpdatedAt
        FROM users
        WHERE id IN ({placeholders})
        ORDER BY id
        """ + _for_update(connection),
        tuple(user_ids),
    ).fetchall()
    users = {int(user["id"]): user for user in map(_row_dict, rows)}
    actor = users.get(actor_user_id, {})
    if (
        not actor
        or int(actor.get("isDeleted") or 0) == 1
        or str(actor.get("userRole") or "") != "admin"
    ):
        raise AdminForbidden("管理员权限已失效")
    target = users.get(target_user_id, {})
    if not target or str(target.get("userAccount") or "").lower() == _SYSTEM_MIGRATION_ACCOUNT:
        raise AdminNotFound("用户不存在")
    return actor, target


def _active_admin_ids(connection: Any) -> list[int]:
    rows = connection.execute(
        """
        SELECT id FROM users
        WHERE userRole = 'admin' AND isDeleted = 0
        ORDER BY id
        """ + _for_update(connection)
    ).fetchall()
    return [int(_row_dict(row)["id"]) for row in rows]


def _audit_snapshot(user: dict[str, Any]) -> dict[str, Any]:
    if not user:
        return {}
    return {
        "username": str(user.get("username") or ""),
        "userRole": str(user.get("userRole") or ""),
        "isDeleted": int(user.get("isDeleted") or 0),
        "authVersion": int(user.get("authVersion") or 1),
        "updatedAt": user.get("updatedAt"),
        "passwordUpdatedAt": user.get("passwordUpdatedAt"),
    }


def _insert_audit(
    connection: Any,
    *,
    actor_user_id: int | None,
    target_user_id: int,
    action: str,
    reason: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT INTO admin_audit_events (
            eventId, actorUserId, targetUserId, action, reason,
            beforeJson, afterJson, createdAt
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uuid4().hex,
            actor_user_id,
            target_user_id,
            action,
            reason.strip(),
            json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            json.dumps(after, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            _now(),
        ),
    )


def _mask_account(account: str) -> str:
    local, separator, domain = str(account or "").partition("@")
    if not separator:
        return "***"
    if len(local) <= 2:
        visible = local[:1] + "***"
    else:
        visible = local[:1] + "***" + local[-1:]
    return f"{visible}@{domain}"


def _public_list_user(row: object) -> dict[str, Any]:
    user = _row_dict(row)
    return {
        "id": int(user["id"]),
        "username": str(user.get("username") or ""),
        "maskedAccount": _mask_account(str(user.get("userAccount") or "")),
        "userRole": str(user.get("userRole") or "user"),
        "status": "disabled" if int(user.get("isDeleted") or 0) else "active",
        "createdAt": user.get("createdAt"),
        "updatedAt": user.get("updatedAt"),
        "lastLoginAt": user.get("lastLoginAt"),
        "lastActiveAt": user.get("lastActiveAt"),
    }


def get_user_summary() -> dict[str, Any]:
    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    with _connect() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS totalUsers,
                SUM(CASE WHEN isDeleted = 0 THEN 1 ELSE 0 END) AS activeUsers,
                SUM(CASE WHEN isDeleted = 1 THEN 1 ELSE 0 END) AS disabledUsers,
                SUM(CASE WHEN userRole = 'admin' AND isDeleted = 0 THEN 1 ELSE 0 END)
                    AS activeAdmins,
                SUM(CASE WHEN createdAt >= ? THEN 1 ELSE 0 END) AS recentRegistrations,
                SUM(CASE WHEN lastLoginAt >= ? THEN 1 ELSE 0 END) AS recentLogins
            FROM users
            WHERE userAccount <> ?
            """,
            (seven_days_ago, seven_days_ago, _SYSTEM_MIGRATION_ACCOUNT),
        ).fetchone()
    result = _row_dict(row)
    return {key: int(result.get(key) or 0) for key in (
        "totalUsers",
        "activeUsers",
        "disabledUsers",
        "activeAdmins",
        "recentRegistrations",
        "recentLogins",
    )}


def list_users(
    *,
    query: str = "",
    role: str = "all",
    status: str = "all",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    clauses: list[str] = ["userAccount <> ?"]
    params: list[Any] = [_SYSTEM_MIGRATION_ACCOUNT]
    normalized_query = query.strip().lower()
    if normalized_query:
        clauses.append("(LOWER(username) LIKE ? OR LOWER(userAccount) LIKE ?)")
        pattern = f"%{normalized_query}%"
        params.extend((pattern, pattern))
    if role in {"user", "admin"}:
        clauses.append("userRole = ?")
        params.append(role)
    if status == "active":
        clauses.append("isDeleted = 0")
    elif status == "disabled":
        clauses.append("isDeleted = 1")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    offset = (page - 1) * page_size
    with _connect() as connection:
        total_row = connection.execute(
            f"SELECT COUNT(*) AS total FROM users{where}", tuple(params)
        ).fetchone()
        rows = connection.execute(
            f"""
            SELECT id, username, userAccount, userRole, isDeleted,
                   createdAt, updatedAt, lastLoginAt, lastActiveAt
            FROM users{where}
            ORDER BY createdAt DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            tuple([*params, page_size, offset]),
        ).fetchall()
    total = int(_row_dict(total_row).get("total") or 0)
    return {
        "items": [_public_list_user(row) for row in rows],
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": max(1, (total + page_size - 1) // page_size),
    }


def get_user_detail(target_user_id: int) -> dict[str, Any]:
    with _connect() as connection:
        user_row = connection.execute(
            """
            SELECT id, userRole, username, userAccount, bio, avatarUrl,
                   createdAt, updatedAt, isDeleted, deletedAt, authVersion,
                   lastLoginAt, lastActiveAt, passwordUpdatedAt
            FROM users WHERE id = ?
            """,
            (target_user_id,),
        ).fetchone()
        if user_row is None:
            raise AdminNotFound("用户不存在")
        if (
            str(_row_dict(user_row).get("userAccount") or "").lower()
            == _SYSTEM_MIGRATION_ACCOUNT
        ):
            raise AdminNotFound("用户不存在")
        usage_row = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM reports WHERE userId = ?) AS reportCount,
                (SELECT COUNT(*) FROM fund_discovery_reports WHERE userId = ?)
                    AS discoveryReportCount,
                (SELECT COUNT(*) FROM fund_transactions WHERE userId = ?)
                    AS transactionCount,
                (SELECT COUNT(*) FROM fund_profiles WHERE userId = ?)
                    AS fundProfileCount
            """,
            (target_user_id, target_user_id, target_user_id, target_user_id),
        ).fetchone()
    user = _row_dict(user_row)
    return {
        "id": int(user["id"]),
        "username": str(user.get("username") or ""),
        "userAccount": str(user.get("userAccount") or ""),
        "userRole": str(user.get("userRole") or "user"),
        "status": "disabled" if int(user.get("isDeleted") or 0) else "active",
        "bio": str(user.get("bio") or ""),
        "avatarUrl": str(user.get("avatarUrl") or ""),
        "createdAt": user.get("createdAt"),
        "updatedAt": user.get("updatedAt"),
        "deletedAt": user.get("deletedAt"),
        "lastLoginAt": user.get("lastLoginAt"),
        "lastActiveAt": user.get("lastActiveAt"),
        "passwordUpdatedAt": user.get("passwordUpdatedAt"),
        "usage": {
            key: int(_row_dict(usage_row).get(key) or 0)
            for key in (
                "reportCount",
                "discoveryReportCount",
                "transactionCount",
                "fundProfileCount",
            )
        },
    }


def update_user(
    *,
    actor_user_id: int,
    target_user_id: int,
    expected_updated_at: str,
    username: str | None,
    user_role: str | None,
    reason: str,
) -> dict[str, Any]:
    with _connect() as connection:
        _begin_write(connection)
        _actor, target = _lock_actor_and_target(
            connection,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
        )
        if str(target.get("updatedAt")) != expected_updated_at:
            raise AdminConflict("用户信息已变化，请刷新后重试")

        next_username = str(target.get("username") or "")
        if username is not None:
            next_username = username.strip()
            if not next_username:
                raise AdminManagementError("用户名不能为空")
        next_role = str(target.get("userRole") or "user")
        if user_role is not None:
            if user_role not in {"user", "admin"}:
                raise AdminManagementError("无效的用户角色")
            next_role = user_role

        role_changed = next_role != str(target.get("userRole"))
        name_changed = next_username != str(target.get("username"))
        if not role_changed and not name_changed:
            raise AdminManagementError("没有需要保存的变化")
        if role_changed and target_user_id == actor_user_id and next_role != "admin":
            raise AdminForbidden("不能取消自己的管理员权限")
        if (
            role_changed
            and str(target.get("userRole")) == "admin"
            and int(target.get("isDeleted") or 0) == 0
            and next_role != "admin"
            and len(_active_admin_ids(connection)) <= 1
        ):
            raise AdminForbidden("系统必须保留至少一名启用中的管理员")

        before = _audit_snapshot(target)
        updated_at = _now()
        auth_increment = 1 if role_changed else 0
        connection.execute(
            """
            UPDATE users
            SET username = ?, userRole = ?, updatedAt = ?,
                authVersion = authVersion + ?
            WHERE id = ?
            """,
            (next_username, next_role, updated_at, auth_increment, target_user_id),
        )
        target.update(
            username=next_username,
            userRole=next_role,
            updatedAt=updated_at,
            authVersion=int(target.get("authVersion") or 1) + auth_increment,
        )
        _insert_audit(
            connection,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action="user_profile_updated" if not role_changed else "user_role_updated",
            reason=reason,
            before=before,
            after=_audit_snapshot(target),
        )
    return get_user_detail(target_user_id)


def set_user_enabled(
    *,
    actor_user_id: int,
    target_user_id: int,
    enabled: bool,
    expected_updated_at: str,
    reason: str,
) -> dict[str, Any]:
    with _connect() as connection:
        _begin_write(connection)
        _actor, target = _lock_actor_and_target(
            connection,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
        )
        if str(target.get("updatedAt")) != expected_updated_at:
            raise AdminConflict("用户信息已变化，请刷新后重试")
        is_enabled = int(target.get("isDeleted") or 0) == 0
        if is_enabled == enabled:
            raise AdminManagementError("账户已经处于目标状态")
        if not enabled and target_user_id == actor_user_id:
            raise AdminForbidden("不能停用自己的账户")
        if (
            not enabled
            and str(target.get("userRole")) == "admin"
            and len(_active_admin_ids(connection)) <= 1
        ):
            raise AdminForbidden("系统必须保留至少一名启用中的管理员")

        before = _audit_snapshot(target)
        updated_at = _now()
        deleted_at = None if enabled else updated_at
        connection.execute(
            """
            UPDATE users
            SET isDeleted = ?, deletedAt = ?, updatedAt = ?,
                authVersion = authVersion + 1
            WHERE id = ?
            """,
            (0 if enabled else 1, deleted_at, updated_at, target_user_id),
        )
        target.update(
            isDeleted=0 if enabled else 1,
            deletedAt=deleted_at,
            updatedAt=updated_at,
            authVersion=int(target.get("authVersion") or 1) + 1,
        )
        _insert_audit(
            connection,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action="user_restored" if enabled else "user_disabled",
            reason=reason,
            before=before,
            after=_audit_snapshot(target),
        )
    return get_user_detail(target_user_id)


def revoke_user_sessions(
    *, actor_user_id: int, target_user_id: int, reason: str
) -> dict[str, Any]:
    with _connect() as connection:
        _begin_write(connection)
        _actor, target = _lock_actor_and_target(
            connection,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
        )
        before = _audit_snapshot(target)
        updated_at = _now()
        connection.execute(
            """
            UPDATE users
            SET authVersion = authVersion + 1, updatedAt = ?
            WHERE id = ?
            """,
            (updated_at, target_user_id),
        )
        target.update(
            authVersion=int(target.get("authVersion") or 1) + 1,
            updatedAt=updated_at,
        )
        _insert_audit(
            connection,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action="user_sessions_revoked",
            reason=reason,
            before=before,
            after=_audit_snapshot(target),
        )
    return {"ok": True, "updatedAt": updated_at}


def create_password_reset_token(
    *,
    actor_user_id: int,
    target_user_id: int,
    reason: str,
    ttl_minutes: int = 30,
) -> dict[str, Any]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    created_at = _now()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    ).isoformat()
    with _connect() as connection:
        _begin_write(connection)
        _actor, target = _lock_actor_and_target(
            connection,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
        )
        if int(target.get("isDeleted") or 0) == 1:
            raise AdminManagementError("停用账户不能重置密码")
        connection.execute(
            """
            UPDATE password_reset_tokens
            SET revokedAt = ?
            WHERE userId = ? AND usedAt IS NULL AND revokedAt IS NULL
            """,
            (created_at, target_user_id),
        )
        connection.execute(
            """
            INSERT INTO password_reset_tokens (
                id, userId, tokenHash, expiresAt, createdAt,
                usedAt, revokedAt, createdByAdminId
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                uuid4().hex,
                target_user_id,
                token_hash,
                expires_at,
                created_at,
                actor_user_id,
            ),
        )
        _insert_audit(
            connection,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action="password_reset_link_created",
            reason=reason,
            before={"resetLinkIssued": False},
            after={"resetLinkIssued": True, "expiresAt": expires_at},
        )
    return {"resetToken": raw_token, "expiresAt": expires_at}


def complete_password_reset(*, raw_token: str, new_password: str) -> None:
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    password_hash = hash_password(new_password)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    with _connect() as connection:
        _begin_write(connection)
        preliminary_row = connection.execute(
            """
            SELECT id, userId, expiresAt, usedAt, revokedAt, createdByAdminId
            FROM password_reset_tokens
            WHERE tokenHash = ?
            """,
            (token_hash,),
        ).fetchone()
        preliminary = _row_dict(preliminary_row)
        if not preliminary:
            raise AdminManagementError("重置链接无效或已过期")
        target = _load_target(connection, int(preliminary["userId"]))
        token_row = connection.execute(
            """
            SELECT id, userId, expiresAt, usedAt, revokedAt, createdByAdminId
            FROM password_reset_tokens
            WHERE tokenHash = ?
            """ + _for_update(connection),
            (token_hash,),
        ).fetchone()
        token = _row_dict(token_row)
        if not token or token.get("usedAt") or token.get("revokedAt"):
            raise AdminManagementError("重置链接无效或已过期")
        try:
            expires_at = datetime.fromisoformat(str(token["expiresAt"]))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError) as exc:
            raise AdminManagementError("重置链接无效或已过期") from exc
        if expires_at <= now_dt:
            raise AdminManagementError("重置链接无效或已过期")
        if int(token["userId"]) != int(target["id"]):
            raise AdminManagementError("重置链接无效或已过期")
        if int(target.get("isDeleted") or 0) == 1:
            raise AdminManagementError("重置链接无效或已过期")

        before = _audit_snapshot(target)
        connection.execute(
            """
            UPDATE users
            SET passwordHash = ?, passwordUpdatedAt = ?, updatedAt = ?,
                authVersion = authVersion + 1
            WHERE id = ? AND isDeleted = 0
            """,
            (password_hash, now, now, int(target["id"])),
        )
        connection.execute(
            "UPDATE password_reset_tokens SET usedAt = ? WHERE id = ?",
            (now, token["id"]),
        )
        connection.execute(
            """
            UPDATE password_reset_tokens
            SET revokedAt = ?
            WHERE userId = ? AND id <> ?
              AND usedAt IS NULL AND revokedAt IS NULL
            """,
            (now, int(target["id"]), token["id"]),
        )
        target.update(
            passwordUpdatedAt=now,
            updatedAt=now,
            authVersion=int(target.get("authVersion") or 1) + 1,
        )
        _insert_audit(
            connection,
            actor_user_id=int(token["createdByAdminId"]),
            target_user_id=int(target["id"]),
            action="password_reset_completed",
            reason="用户通过一次性链接完成密码重置",
            before=before,
            after=_audit_snapshot(target),
        )


def list_audit_events(*, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    offset = (page - 1) * page_size
    with _connect() as connection:
        count_row = connection.execute(
            "SELECT COUNT(*) AS total FROM admin_audit_events"
        ).fetchone()
        rows = connection.execute(
            """
            SELECT e.eventId, e.actorUserId, e.targetUserId, e.action,
                   e.reason, e.beforeJson, e.afterJson, e.createdAt,
                   actor.username AS actorUsername,
                   target.username AS targetUsername
            FROM admin_audit_events e
            LEFT JOIN users actor ON actor.id = e.actorUserId
            LEFT JOIN users target ON target.id = e.targetUserId
            ORDER BY e.createdAt DESC, e.eventId DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
    total = int(_row_dict(count_row).get("total") or 0)
    items: list[dict[str, Any]] = []
    for row in rows:
        event = _row_dict(row)
        items.append(
            {
                "eventId": str(event["eventId"]),
                "actorUserId": event.get("actorUserId"),
                "actorUsername": event.get("actorUsername") or "系统引导",
                "targetUserId": int(event["targetUserId"]),
                "targetUsername": event.get("targetUsername") or "未知用户",
                "action": str(event["action"]),
                "reason": str(event["reason"]),
                "before": json.loads(str(event.get("beforeJson") or "{}")),
                "after": json.loads(str(event.get("afterJson") or "{}")),
                "createdAt": event.get("createdAt"),
            }
        )
    return {
        "items": items,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": max(1, (total + page_size - 1) // page_size),
    }


def promote_bootstrap_admin(account: str) -> bool:
    """Promote one explicit existing active account; never creates or auto-matches users."""

    normalized = account.strip().lower()
    if not normalized:
        raise AdminManagementError("管理员账户不能为空")
    with _connect() as connection:
        _begin_write(connection)
        row = connection.execute(
            """
            SELECT id, userRole, username, userAccount, createdAt, updatedAt,
                   isDeleted, authVersion, passwordUpdatedAt
            FROM users WHERE LOWER(userAccount) = ?
            """ + _for_update(connection),
            (normalized,),
        ).fetchone()
        target = _row_dict(row)
        if not target:
            raise AdminNotFound("指定账户尚未注册")
        if str(target.get("userAccount") or "").lower() == _SYSTEM_MIGRATION_ACCOUNT:
            raise AdminNotFound("指定账户尚未注册")
        if int(target.get("isDeleted") or 0) == 1:
            raise AdminManagementError("指定账户已停用")
        if str(target.get("userRole")) == "admin":
            return False
        before = _audit_snapshot(target)
        updated_at = _now()
        connection.execute(
            """
            UPDATE users
            SET userRole = 'admin', authVersion = authVersion + 1, updatedAt = ?
            WHERE id = ?
            """,
            (updated_at, int(target["id"])),
        )
        target.update(
            userRole="admin",
            authVersion=int(target.get("authVersion") or 1) + 1,
            updatedAt=updated_at,
        )
        _insert_audit(
            connection,
            actor_user_id=None,
            target_user_id=int(target["id"]),
            action="bootstrap_admin_promoted",
            reason="部署引导：设置初始管理员",
            before=before,
            after=_audit_snapshot(target),
        )
    return True
