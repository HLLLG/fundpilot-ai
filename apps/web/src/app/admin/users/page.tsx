"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Ban,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clipboard,
  FileClock,
  KeyRound,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  UserCog,
  Users,
  X,
} from "lucide-react";
import { BrandMark } from "@/components/BrandMark";
import { useAuth } from "@/components/AuthProvider";
import {
  createAdminPasswordResetLink,
  fetchAdminAuditEvents,
  fetchAdminUser,
  fetchAdminUsers,
  fetchAdminUserSummary,
  revokeAdminUserSessions,
  setAdminUserEnabled,
  updateAdminUser,
  type AdminAuditEvent,
  type AdminPage,
  type AdminUserDetail,
  type AdminUserListItem,
  type AdminUserRole,
  type AdminUserStatus,
  type AdminUserSummary,
} from "@/lib/api";

const EMPTY_PAGE: AdminPage<AdminUserListItem> = {
  items: [],
  page: 1,
  pageSize: 20,
  total: 0,
  totalPages: 1,
};

const ACTION_LABELS: Record<string, string> = {
  bootstrap_admin_promoted: "设置初始管理员",
  user_profile_updated: "修改用户资料",
  user_role_updated: "修改用户角色",
  user_disabled: "停用账户",
  user_restored: "恢复账户",
  user_sessions_revoked: "撤销全部会话",
  password_reset_link_created: "生成密码重置链接",
  password_reset_completed: "完成密码重置",
};

function formatDate(value: string | null | undefined): string {
  if (!value) return "暂无";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "操作失败，请稍后重试";
}

export default function AdminUsersPage() {
  const { user } = useAuth();
  const [summary, setSummary] = useState<AdminUserSummary | null>(null);
  const [usersPage, setUsersPage] = useState<AdminPage<AdminUserListItem>>(EMPTY_PAGE);
  const [auditEvents, setAuditEvents] = useState<AdminAuditEvent[]>([]);
  const [queryDraft, setQueryDraft] = useState("");
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<"all" | AdminUserRole>("all");
  const [status, setStatus] = useState<"all" | AdminUserStatus>("all");
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AdminUserDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const loadUsers = useCallback(async () => {
    const result = await fetchAdminUsers({ query, role, status, page, pageSize: 20 });
    setUsersPage(result);
  }, [page, query, role, status]);

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextSummary, nextUsers, audit] = await Promise.all([
        fetchAdminUserSummary(),
        fetchAdminUsers({ query, role, status, page, pageSize: 20 }),
        fetchAdminAuditEvents(1, 12),
      ]);
      setSummary(nextSummary);
      setUsersPage(nextUsers);
      setAuditEvents(audit.items);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setLoading(false);
    }
  }, [page, query, role, status]);

  useEffect(() => {
    if (user?.userRole === "admin") void loadOverview();
  }, [loadOverview, user?.userRole]);

  const openUser = useCallback(async (userId: number) => {
    setDetailLoading(true);
    setError(null);
    try {
      setSelected(await fetchAdminUser(userId));
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  async function refreshAfterMutation(nextDetail?: AdminUserDetail) {
    if (nextDetail) setSelected(nextDetail);
    await Promise.all([
      loadUsers(),
      fetchAdminUserSummary().then(setSummary),
      fetchAdminAuditEvents(1, 12).then((result) => setAuditEvents(result.items)),
    ]);
  }

  function onSearch(event: FormEvent) {
    event.preventDefault();
    setPage(1);
    setQuery(queryDraft.trim());
  }

  if (user?.userRole !== "admin") {
    return (
      <main className="premium-bg flex min-h-screen items-center justify-center px-4">
        <section className="section-card max-w-md p-7 text-center">
          <ShieldCheck className="mx-auto text-[var(--muted-soft)]" size={36} />
          <h1 className="mt-4 text-xl font-black text-[var(--brand-deep)]">无权访问用户管理中心</h1>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">此页面仅向管理员开放。</p>
          <Link href="/" className="btn-primary mt-6">返回研究台</Link>
        </section>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[var(--background)] text-[var(--foreground)]">
      <header className="sticky top-0 z-30 border-b border-[var(--line)] bg-[var(--panel-strong)]/95 backdrop-blur">
        <div className="mx-auto flex min-h-16 max-w-[1440px] items-center justify-between gap-2 px-4 sm:gap-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3 sm:gap-4">
            <span className="sm:hidden"><BrandMark size="sm" showName={false} /></span>
            <span className="hidden sm:inline-flex"><BrandMark size="sm" showEnglish /></span>
            <span className="hidden h-6 w-px bg-[var(--line)] sm:block" aria-hidden />
            <div className="min-w-0">
              <p className="research-kicker truncate text-[10px] sm:text-xs">ADMIN CONTROL</p>
              <h1 className="truncate text-sm font-black text-[var(--brand-deep)] sm:text-base">用户管理中心</h1>
            </div>
          </div>
          <Link href="/" className="btn-ghost min-h-11 shrink-0 whitespace-nowrap px-2 sm:px-3">
            <ArrowLeft size={16} /><span className="sm:hidden">研究台</span><span className="hidden sm:inline">返回研究台</span>
          </Link>
        </div>
      </header>

      <div className="mx-auto max-w-[1440px] space-y-6 px-4 py-6 sm:px-6 lg:py-8">
        <section className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="research-kicker">ACCOUNT GOVERNANCE</p>
            <h2 className="font-display mt-2 text-2xl font-black text-[var(--brand-deep)] sm:text-3xl">账户状态与访问控制</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">
              用户列表默认隐藏完整邮箱；完整身份信息仅在管理员详情中显示。所有高风险操作均写入不可修改的审计记录。
            </p>
          </div>
          <button type="button" onClick={() => void loadOverview()} className="btn-secondary min-h-11" disabled={loading}>
            <RefreshCw size={16} className={loading ? "animate-spin" : ""} />刷新数据
          </button>
        </section>

        {error ? (
          <div className="inline-notice inline-notice-error" role="alert">
            <CircleAlert className="inline-notice-icon" aria-hidden size={17} />
            <span className="inline-notice-message">{error}</span>
          </div>
        ) : null}

        <SummaryCards summary={summary} loading={loading} />

        <section className="overflow-hidden rounded-3xl border border-[var(--line)] bg-[var(--panel)]">
          <div className="border-b border-[var(--line)] p-4 sm:p-5">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <h2 className="text-lg font-black text-[var(--brand-deep)]">用户目录</h2>
                <p className="mt-1 text-xs text-[var(--muted)]">共 {usersPage.total} 个账户</p>
              </div>
              <form onSubmit={onSearch} className="flex min-w-0 flex-1 gap-2 lg:max-w-md">
                <label className="relative min-w-0 flex-1">
                  <span className="sr-only">搜索用户名或邮箱</span>
                  <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted-soft)]" />
                  <input
                    value={queryDraft}
                    onChange={(event) => setQueryDraft(event.target.value)}
                    className="input-field min-h-11 pl-9"
                    placeholder="搜索用户名或邮箱"
                    maxLength={128}
                  />
                </label>
                <button type="submit" className="btn-primary min-h-11 px-4">搜索</button>
              </form>
              <div className="grid grid-cols-2 gap-2">
                <select
                  aria-label="按角色筛选"
                  value={role}
                  onChange={(event) => { setPage(1); setRole(event.target.value as typeof role); }}
                  className="input-field min-h-11 py-2"
                >
                  <option value="all">全部角色</option>
                  <option value="user">普通用户</option>
                  <option value="admin">管理员</option>
                </select>
                <select
                  aria-label="按状态筛选"
                  value={status}
                  onChange={(event) => { setPage(1); setStatus(event.target.value as typeof status); }}
                  className="input-field min-h-11 py-2"
                >
                  <option value="all">全部状态</option>
                  <option value="active">启用中</option>
                  <option value="disabled">已停用</option>
                </select>
              </div>
            </div>
          </div>

          <UserDirectory
            items={usersPage.items}
            loading={loading || detailLoading}
            onSelect={(id) => void openUser(id)}
          />

          <div className="admin-pagination flex items-center justify-between border-t border-[var(--line)] px-4 py-3 text-sm sm:px-5">
            <span className="text-[var(--muted)]">第 {usersPage.page} / {usersPage.totalPages} 页</span>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-secondary min-h-10 px-3"
                disabled={page <= 1}
                onClick={() => setPage((value) => Math.max(1, value - 1))}
              ><ChevronLeft size={16} />上一页</button>
              <button
                type="button"
                className="btn-secondary min-h-10 px-3"
                disabled={page >= usersPage.totalPages}
                onClick={() => setPage((value) => value + 1)}
              >下一页<ChevronRight size={16} /></button>
            </div>
          </div>
        </section>

        <AuditPanel events={auditEvents} />
      </div>

      {selected ? (
        <UserDetailDialog
          detail={selected}
          currentUserId={user.id}
          onClose={() => setSelected(null)}
          onSaved={(nextDetail) => void refreshAfterMutation(nextDetail)}
          onSessionsRevoked={async () => {
            const refreshed = await fetchAdminUser(selected.id);
            await refreshAfterMutation(refreshed);
          }}
        />
      ) : null}
    </main>
  );
}

function SummaryCards({ summary, loading }: { summary: AdminUserSummary | null; loading: boolean }) {
  const cards = [
    { label: "全部账户", value: summary?.totalUsers, icon: Users, tone: "admin-tone-brand" },
    { label: "启用中", value: summary?.activeUsers, icon: CheckCircle2, tone: "admin-tone-success" },
    { label: "已停用", value: summary?.disabledUsers, icon: Ban, tone: "admin-tone-danger" },
    { label: "启用管理员", value: summary?.activeAdmins, icon: ShieldCheck, tone: "admin-tone-brand" },
    { label: "近 7 日注册", value: summary?.recentRegistrations, icon: UserCog, tone: "admin-tone-info" },
    { label: "近 7 日登录", value: summary?.recentLogins, icon: Activity, tone: "admin-tone-accent" },
  ];
  return (
    <section className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6" aria-label="用户概览">
      {cards.map(({ label, value, icon: Icon, tone }) => (
        <article key={label} className="rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4">
          <span className={`admin-summary-icon ${tone}`}><Icon size={18} /></span>
          <p className="mt-4 text-2xl font-black tabular-nums text-[var(--brand-deep)]">{loading && value === undefined ? "—" : value ?? 0}</p>
          <p className="mt-1 text-xs font-bold text-[var(--muted)]">{label}</p>
        </article>
      ))}
    </section>
  );
}

function UserDirectory({
  items,
  loading,
  onSelect,
}: {
  items: AdminUserListItem[];
  loading: boolean;
  onSelect: (id: number) => void;
}) {
  if (!items.length) {
    return <div className="px-5 py-14 text-center text-sm text-[var(--muted)]">{loading ? "正在读取用户…" : "没有符合条件的用户"}</div>;
  }
  return (
    <>
      <div className="hidden overflow-x-auto md:block">
        <table className="w-full min-w-[860px] text-left text-sm">
          <thead className="bg-[var(--surface-muted)] text-xs font-black uppercase tracking-wider text-[var(--muted)]">
            <tr><th className="px-5 py-3">用户</th><th className="px-4 py-3">角色</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">最近活动</th><th className="px-4 py-3">注册时间</th><th className="px-5 py-3 text-right">操作</th></tr>
          </thead>
          <tbody className="divide-y divide-[var(--line)]">
            {items.map((item) => (
              <tr key={item.id} className="transition hover:bg-[var(--surface-muted)]">
                <td className="px-5 py-4"><p className="font-black text-[var(--brand-deep)]">{item.username}</p><p className="mt-1 text-xs text-[var(--muted)]">#{item.id} · {item.maskedAccount}</p></td>
                <td className="px-4 py-4"><RoleBadge role={item.userRole} /></td>
                <td className="px-4 py-4"><StatusBadge status={item.status} /></td>
                <td className="px-4 py-4 text-xs text-[var(--muted)]">{formatDate(item.lastActiveAt || item.lastLoginAt)}</td>
                <td className="px-4 py-4 text-xs text-[var(--muted)]">{formatDate(item.createdAt)}</td>
                <td className="px-5 py-4 text-right"><button type="button" onClick={() => onSelect(item.id)} className="btn-secondary min-h-10 px-3">查看与管理</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="divide-y divide-[var(--line)] md:hidden">
        {items.map((item) => (
          <article key={item.id} className="p-4">
            <div className="flex items-start justify-between gap-3"><div><h3 className="font-black text-[var(--brand-deep)]">{item.username}</h3><p className="mt-1 text-xs text-[var(--muted)]">#{item.id} · {item.maskedAccount}</p></div><StatusBadge status={item.status} /></div>
            <div className="mt-3 flex items-center justify-between"><RoleBadge role={item.userRole} /><span className="text-xs text-[var(--muted)]">活动：{formatDate(item.lastActiveAt || item.lastLoginAt)}</span></div>
            <button type="button" onClick={() => onSelect(item.id)} className="btn-secondary mt-4 w-full">查看与管理</button>
          </article>
        ))}
      </div>
    </>
  );
}

function RoleBadge({ role }: { role: AdminUserRole }) {
  return <span className={`badge ${role === "admin" ? "badge-accent" : ""}`}>{role === "admin" ? "管理员" : "普通用户"}</span>;
}

function StatusBadge({ status }: { status: AdminUserStatus }) {
  const active = status === "active";
  return (
    <span className={`admin-status-pill ${active ? "admin-status-active" : "admin-status-disabled"}`}>
      <span className="admin-status-dot" aria-hidden />
      {active ? "启用中" : "已停用"}
    </span>
  );
}

function AuditPanel({ events }: { events: AdminAuditEvent[] }) {
  return (
    <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
      <div className="flex items-center gap-3">
        <span className="admin-summary-icon admin-tone-brand"><FileClock size={19} /></span>
        <div>
          <h2 className="font-black text-[var(--brand-deep)]">最近管理员操作</h2>
          <p className="text-xs text-[var(--muted)]">审计记录只追加，不支持修改或删除</p>
        </div>
      </div>
      <div className="mt-5 divide-y divide-[var(--line)]">
        {events.length ? events.map((event) => (
          <article key={event.eventId} className="grid gap-2 py-3 text-sm sm:grid-cols-[minmax(0,1fr)_auto]">
            <div><p className="font-bold text-[var(--foreground)]"><span className="text-[var(--brand-strong)]">{event.actorUsername}</span> · {ACTION_LABELS[event.action] ?? event.action} · <span className="text-[var(--brand-deep)]">{event.targetUsername}</span> <span className="text-xs text-[var(--muted-soft)]">#{event.targetUserId}</span></p><p className="mt-1 text-xs leading-5 text-[var(--muted)]">原因：{event.reason}</p></div>
            <time className="text-xs text-[var(--muted-soft)]">{formatDate(event.createdAt)}</time>
          </article>
        )) : <p className="py-8 text-center text-sm text-[var(--muted)]">暂无管理员操作记录</p>}
      </div>
    </section>
  );
}

function UserDetailDialog({
  detail,
  currentUserId,
  onClose,
  onSaved,
  onSessionsRevoked,
}: {
  detail: AdminUserDetail;
  currentUserId: number;
  onClose: () => void;
  onSaved: (detail: AdminUserDetail) => void;
  onSessionsRevoked: () => Promise<void>;
}) {
  const [username, setUsername] = useState(detail.username);
  const [userRole, setUserRole] = useState<AdminUserRole>(detail.userRole);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetLink, setResetLink] = useState<string | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onClose(); };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  const usage = useMemo(() => [
    ["日报", detail.usage.reportCount],
    ["基金发现报告", detail.usage.discoveryReportCount],
    ["交易记录", detail.usage.transactionCount],
    ["基金档案", detail.usage.fundProfileCount],
  ] as const, [detail.usage]);

  function requireReason(): string | null {
    const normalized = reason.trim();
    if (normalized.length < 3) {
      setError("请填写至少 3 个字的操作原因，便于审计追溯。");
      return null;
    }
    return normalized;
  }

  async function run(action: () => Promise<void>, success: string) {
    setBusy(true); setError(null); setMessage(null); setResetLink(null);
    try { await action(); setMessage(success); setReason(""); }
    catch (nextError) { setError(errorMessage(nextError)); }
    finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-[80] flex items-end justify-center bg-[var(--brand-ink)]/45 p-0 backdrop-blur-sm sm:items-center sm:p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}>
      <section role="dialog" aria-modal="true" aria-labelledby="user-detail-title" className="max-h-[94vh] w-full max-w-3xl overflow-y-auto rounded-t-3xl bg-[var(--panel-strong)] shadow-2xl sm:rounded-3xl">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-[var(--line)] bg-[var(--panel-strong)]/95 p-5 backdrop-blur sm:p-6">
          <div><div className="flex flex-wrap items-center gap-2"><h2 id="user-detail-title" className="text-xl font-black text-[var(--brand-deep)]">{detail.username}</h2><RoleBadge role={detail.userRole} /><StatusBadge status={detail.status} /></div><p className="mt-2 break-all text-sm text-[var(--muted)]">#{detail.id} · {detail.userAccount}</p></div>
          <button type="button" onClick={onClose} disabled={busy} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--surface-muted)]" aria-label="关闭"><X size={20} /></button>
        </header>

        <div className="space-y-6 p-5 sm:p-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{usage.map(([label, value]) => <article key={label} className="rounded-2xl bg-[var(--surface-muted)] p-3"><p className="text-xl font-black tabular-nums text-[var(--brand-deep)]">{value}</p><p className="mt-1 text-xs text-[var(--muted)]">{label}</p></article>)}</div>

          <dl className="grid gap-x-5 gap-y-3 rounded-2xl border border-[var(--line)] p-4 text-sm sm:grid-cols-2">
            <div><dt className="text-xs text-[var(--muted)]">注册时间</dt><dd className="mt-1 font-semibold">{formatDate(detail.createdAt)}</dd></div>
            <div><dt className="text-xs text-[var(--muted)]">最近登录</dt><dd className="mt-1 font-semibold">{formatDate(detail.lastLoginAt)}</dd></div>
            <div><dt className="text-xs text-[var(--muted)]">最近活动</dt><dd className="mt-1 font-semibold">{formatDate(detail.lastActiveAt)}</dd></div>
            <div><dt className="text-xs text-[var(--muted)]">密码更新时间</dt><dd className="mt-1 font-semibold">{formatDate(detail.passwordUpdatedAt)}</dd></div>
          </dl>

          <section className="rounded-2xl border border-[var(--line)] p-4 sm:p-5">
            <h3 className="font-black text-[var(--brand-deep)]">基本资料与角色</h3>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label className="text-sm font-bold text-[var(--foreground)]">用户名<input className="input-field mt-1.5" value={username} maxLength={64} onChange={(event) => setUsername(event.target.value)} /></label>
              <label className="text-sm font-bold text-[var(--foreground)]">角色<select className="input-field mt-1.5" value={userRole} onChange={(event) => setUserRole(event.target.value as AdminUserRole)} disabled={detail.id === currentUserId}><option value="user">普通用户</option><option value="admin">管理员</option></select></label>
            </div>
          </section>

          <label className="block text-sm font-bold text-[var(--foreground)]">操作原因（必填）<textarea className="input-field mt-1.5 min-h-24 resize-y" value={reason} maxLength={500} onChange={(event) => setReason(event.target.value)} placeholder="例如：用户本人完成身份核验后申请重置" /></label>

          {error ? <div className="inline-notice inline-notice-error" role="alert"><CircleAlert className="inline-notice-icon" size={17} aria-hidden /><span className="inline-notice-message">{error}</span></div> : null}
          {message ? <div className="inline-notice inline-notice-success" role="status"><CheckCircle2 className="inline-notice-icon" size={17} aria-hidden /><span className="inline-notice-message">{message}</span></div> : null}
          {resetLink ? <div className="rounded-2xl border border-[var(--warn-border)] bg-[var(--warn-bg)] p-4"><p className="text-sm font-black text-[var(--warn-fg)]">一次性重置链接（30 分钟内有效）</p><p className="mt-2 break-all rounded-lg bg-[var(--panel-strong)] p-3 text-xs leading-5 text-[var(--foreground)]">{resetLink}</p><button type="button" className="btn-secondary mt-3" onClick={() => void navigator.clipboard.writeText(resetLink).then(() => setMessage("链接已复制。请通过可信渠道发送给用户。"))}><Clipboard size={16} />复制链接</button></div> : null}

          <div className="grid gap-3 sm:grid-cols-2">
            <button type="button" className="btn-primary" disabled={busy} onClick={() => { const auditReason = requireReason(); if (!auditReason) return; void run(async () => { const next = await updateAdminUser(detail.id, { expectedUpdatedAt: detail.updatedAt, username: username.trim(), userRole, reason: auditReason }); onSaved(next); }, "用户资料已保存，相关旧会话按权限变化自动失效。"); }}><UserCog size={16} />保存资料与角色</button>
            <button type="button" className="btn-secondary" disabled={busy || detail.status === "disabled"} onClick={() => { const auditReason = requireReason(); if (!auditReason) return; void run(async () => { const result = await createAdminPasswordResetLink(detail.id, auditReason); const link = `${window.location.origin}/reset-password#token=${encodeURIComponent(result.resetToken)}`; setResetLink(link); }, "一次性重置链接已生成。"); }}><KeyRound size={16} />生成密码重置链接</button>
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => { const auditReason = requireReason(); if (!auditReason || !window.confirm("确定撤销该用户在所有设备上的登录吗？")) return; void run(async () => { await revokeAdminUserSessions(detail.id, auditReason); await onSessionsRevoked(); }, "全部旧会话已撤销。"); }}><RotateCcw size={16} />撤销全部会话</button>
            <button type="button" className={detail.status === "active" ? "admin-btn-danger" : "btn-secondary"} disabled={busy || detail.id === currentUserId} onClick={() => { const auditReason = requireReason(); const enabling = detail.status === "disabled"; if (!auditReason || !window.confirm(enabling ? "确定恢复该账户吗？恢复后用户仍需重新登录。" : "确定停用该账户吗？该用户会立即退出所有设备。")) return; void run(async () => { const next = await setAdminUserEnabled(detail.id, enabling, { expectedUpdatedAt: detail.updatedAt, reason: auditReason }); onSaved(next); }, enabling ? "账户已恢复，用户需要重新登录。" : "账户已停用，全部旧会话已失效。"); }}>{detail.status === "active" ? <><Ban size={16} />停用账户</> : <><CheckCircle2 size={16} />恢复账户</>}</button>
          </div>
          <p className="text-xs leading-5 text-[var(--muted)]">出于隐私与安全考虑，本中心不提供邮箱修改、明文密码设置、资金数据查看或用户硬删除。</p>
        </div>
      </section>
    </div>
  );
}
