import { API_BASE, ApiError, apiFetch } from "@/lib/api/core";

export type AdminUserStatus = "active" | "disabled";
export type AdminUserRole = "user" | "admin";

export type AdminUserSummary = {
  totalUsers: number;
  activeUsers: number;
  disabledUsers: number;
  activeAdmins: number;
  recentRegistrations: number;
  recentLogins: number;
};

export type AdminUserListItem = {
  id: number;
  username: string;
  maskedAccount: string;
  userRole: AdminUserRole;
  status: AdminUserStatus;
  createdAt: string;
  updatedAt: string;
  lastLoginAt: string | null;
  lastActiveAt: string | null;
};

export type AdminUserDetail = {
  id: number;
  username: string;
  userAccount: string;
  userRole: AdminUserRole;
  status: AdminUserStatus;
  bio: string;
  avatarUrl: string;
  createdAt: string;
  updatedAt: string;
  deletedAt: string | null;
  lastLoginAt: string | null;
  lastActiveAt: string | null;
  passwordUpdatedAt: string | null;
  usage: {
    reportCount: number;
    discoveryReportCount: number;
    transactionCount: number;
    fundProfileCount: number;
  };
};

export type AdminAuditEvent = {
  eventId: string;
  actorUserId: number | null;
  actorUsername: string;
  targetUserId: number;
  targetUsername: string;
  action: string;
  reason: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  createdAt: string;
};

export type AdminPage<T> = {
  items: T[];
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
};

async function responseJson<T>(response: Response, fallback: string): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : fallback;
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export async function fetchAdminUserSummary(): Promise<AdminUserSummary> {
  const response = await apiFetch(`${API_BASE}/api/admin/users/summary`, {
    cache: "no-store",
  });
  return responseJson(response, "无法读取用户概览");
}

export async function fetchAdminUsers(options: {
  query?: string;
  role?: "all" | AdminUserRole;
  status?: "all" | AdminUserStatus;
  page?: number;
  pageSize?: number;
} = {}): Promise<AdminPage<AdminUserListItem>> {
  const response = await apiFetch(`${API_BASE}/api/admin/users/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: options.query ?? "",
      role: options.role ?? "all",
      status: options.status ?? "all",
      page: options.page ?? 1,
      pageSize: options.pageSize ?? 20,
    }),
    cache: "no-store",
  });
  return responseJson(response, "无法读取用户列表");
}

export async function fetchAdminUser(userId: number): Promise<AdminUserDetail> {
  const response = await apiFetch(`${API_BASE}/api/admin/users/${userId}`, {
    cache: "no-store",
  });
  return responseJson(response, "无法读取用户详情");
}

export async function updateAdminUser(
  userId: number,
  payload: {
    expectedUpdatedAt: string;
    username?: string;
    userRole?: AdminUserRole;
    reason: string;
  },
): Promise<AdminUserDetail> {
  const response = await apiFetch(`${API_BASE}/api/admin/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return responseJson(response, "用户信息保存失败");
}

export async function setAdminUserEnabled(
  userId: number,
  enabled: boolean,
  payload: { expectedUpdatedAt: string; reason: string },
): Promise<AdminUserDetail> {
  const action = enabled ? "restore" : "disable";
  const response = await apiFetch(`${API_BASE}/api/admin/users/${userId}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return responseJson(response, enabled ? "恢复用户失败" : "停用用户失败");
}

export async function revokeAdminUserSessions(
  userId: number,
  reason: string,
): Promise<{ ok: boolean; updatedAt: string }> {
  const response = await apiFetch(
    `${API_BASE}/api/admin/users/${userId}/revoke-sessions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  );
  return responseJson(response, "撤销会话失败");
}

export async function createAdminPasswordResetLink(
  userId: number,
  reason: string,
): Promise<{ resetToken: string; expiresAt: string }> {
  const response = await apiFetch(
    `${API_BASE}/api/admin/users/${userId}/password-reset-link`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  );
  return responseJson(response, "生成密码重置链接失败");
}

export async function fetchAdminAuditEvents(
  page = 1,
  pageSize = 20,
): Promise<AdminPage<AdminAuditEvent>> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  const response = await apiFetch(`${API_BASE}/api/admin/audit-events?${params}`, {
    cache: "no-store",
  });
  return responseJson(response, "无法读取管理员操作记录");
}

export async function completePasswordReset(payload: {
  token: string;
  newPassword: string;
}): Promise<{ ok: boolean }> {
  const response = await apiFetch(`${API_BASE}/api/auth/password-reset/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return responseJson(response, "重置链接无效或已过期");
}
