"use client";

import { useAuth } from "@/components/AuthProvider";
import { UserAvatar } from "@/components/UserAvatar";

type UserMenuProps = {
  onOpenMe: () => void;
};

export function UserMenu({ onOpenMe }: UserMenuProps) {
  const { user } = useAuth();
  const displayName = user?.username || user?.userAccount || "用户";

  return (
    <button
      type="button"
      onClick={onOpenMe}
      className="flex items-center gap-2 rounded-full border border-slate-200 bg-white py-1 pl-1 pr-1 shadow-sm transition hover:border-[var(--info-border)] hover:bg-[var(--info-bg)]/80 lg:pr-2.5"
      aria-label="打开我的"
    >
      <UserAvatar name={displayName} avatarUrl={user?.avatarUrl} size="sm" />
      <span className="hidden max-w-[7.5rem] truncate text-sm font-bold text-slate-800 lg:inline">
        {displayName}
      </span>
    </button>
  );
}
