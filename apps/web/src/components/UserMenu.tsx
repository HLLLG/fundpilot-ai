"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronDown, History, LogOut, Settings } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";

export type UserMenuTarget = "history";

type UserMenuProps = {
  onNavigate: (target: UserMenuTarget) => void;
};

export function UserMenu({ onNavigate }: UserMenuProps) {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const displayName = user?.username || user?.userAccount || "用户";
  const initial = displayName.slice(0, 1).toUpperCase();

  return (
    <div ref={rootRef} className="relative z-50">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-2 rounded-full border border-slate-200 bg-white py-1 pl-1 pr-2.5 shadow-sm transition hover:border-blue-200 hover:bg-blue-50/40"
        aria-expanded={open}
        aria-haspopup="menu"
      >
        {user?.avatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={user.avatarUrl}
            alt=""
            className="h-9 w-9 rounded-full object-cover"
          />
        ) : (
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-[var(--brand)] to-[var(--brand-strong)] text-sm font-black text-white shadow-[0_6px_16px_rgba(35,86,224,0.30)] ring-2 ring-white">
            {initial}
          </span>
        )}
        <ChevronDown
          size={16}
          className={`text-slate-400 transition ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-[60] mt-2 w-56 overflow-hidden rounded-2xl border border-slate-200 bg-white py-1.5 shadow-[0_16px_40px_rgba(15,23,42,0.12)]"
        >
          <div className="border-b border-slate-100 px-3 py-2.5">
            <p className="truncate text-sm font-bold text-slate-800">{displayName}</p>
            <p className="truncate text-xs text-slate-500">{user?.userAccount}</p>
          </div>
          <button
            type="button"
            role="menuitem"
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm font-bold text-slate-700 transition hover:bg-slate-50"
            onClick={() => {
              setOpen(false);
              router.push("/settings");
            }}
          >
            <Settings size={16} className="text-blue-600" />
            账号设置
          </button>
          <button
            type="button"
            role="menuitem"
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm font-bold text-slate-700 transition hover:bg-slate-50"
            onClick={() => {
              setOpen(false);
              onNavigate("history");
            }}
          >
            <History size={16} className="text-blue-600" />
            历史日报
          </button>
          <button
            type="button"
            role="menuitem"
            className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm font-bold text-slate-700 transition hover:bg-slate-50"
            onClick={() => {
              setOpen(false);
              logout();
            }}
          >
            <LogOut size={16} className="text-slate-500" />
            退出登录
          </button>
        </div>
      ) : null}
    </div>
  );
}
