"use client";

import { useRouter } from "next/navigation";
import { Activity, ChevronRight, LogOut, PieChart, ShieldCheck } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";
import { UserAvatar } from "@/components/UserAvatar";

type MePageProps = {
  onOpenAnalysis: () => void;
};

export function MePage({ onOpenAnalysis }: MePageProps) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const displayName = user?.username || user?.userAccount || "用户";
  const isAdmin = user?.userRole === "admin";

  return (
    <div className="mx-auto w-full max-w-lg pb-4">
      <button
        type="button"
        onClick={() => router.push("/settings")}
        className="me-identity flex min-h-11 w-full items-center gap-3 rounded-[var(--radius-card)] px-4 py-4 text-left transition hover:bg-[var(--panel-strong)]"
        aria-label="账号设置"
      >
        <UserAvatar name={displayName} avatarUrl={user?.avatarUrl} size="lg" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-lg font-bold text-[var(--brand-deep)]">{displayName}</p>
          {user?.userAccount ? (
            <p className="mt-0.5 truncate text-sm text-[var(--muted)]">{user.userAccount}</p>
          ) : null}
        </div>
        <ChevronRight size={18} className="shrink-0 text-slate-400" />
      </button>

      <div className="me-list mt-3 overflow-hidden rounded-[var(--radius-card)]">
        <MeRow icon={PieChart} label="盈亏分析" onClick={onOpenAnalysis} />
        {isAdmin ? (
          <>
            <MeRow
              icon={ShieldCheck}
              label="用户管理中心"
              onClick={() => router.push("/admin/users")}
            />
            <MeRow
              icon={Activity}
              label="运维监控"
              onClick={() => router.push("/admin/ops")}
            />
          </>
        ) : null}
      </div>

      <div className="me-list mt-3 overflow-hidden rounded-[var(--radius-card)]">
        <MeRow
          icon={LogOut}
          label="退出登录"
          showChevron={false}
          onClick={() => {
            if (window.confirm("确定退出当前登录吗？")) logout();
          }}
        />
      </div>
    </div>
  );
}

function MeRow({
  icon: Icon,
  label,
  onClick,
  showChevron = true,
}: {
  icon: typeof PieChart;
  label: string;
  onClick: () => void;
  showChevron?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="me-row flex min-h-11 w-full items-center gap-3 px-4 py-3.5 text-left"
    >
      <span className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent-strong)]">
        <Icon size={18} strokeWidth={2.25} />
      </span>
      <span className="min-w-0 flex-1 text-[15px] font-medium text-[var(--brand-deep)]">{label}</span>
      {showChevron ? <ChevronRight size={18} className="shrink-0 text-slate-400" /> : null}
    </button>
  );
}
