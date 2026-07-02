"use client";

import Link from "next/link";
import { ArrowLeft, Mail, UserRound } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";

export default function SettingsPage() {
  const { user } = useAuth();

  return (
    <div className="landing-hero-bg min-h-screen px-4 py-8">
      <div className="mx-auto max-w-lg">
        <Link
          href="/"
          className="mb-6 inline-flex items-center gap-2 text-sm font-semibold text-slate-600 transition hover:text-[var(--brand)]"
        >
          <ArrowLeft size={16} />
          返回首页
        </Link>

        <h1 className="font-display text-2xl font-extrabold tracking-tight text-slate-900">账号设置</h1>
        <p className="mt-1 text-sm text-slate-500">查看当前登录账号</p>

        <section className="section-card mt-8 p-6">
          <h2 className="section-eyebrow">当前账号</h2>
          <div className="mt-4 space-y-3">
            <div className="flex items-center gap-3 rounded-2xl bg-slate-50 px-4 py-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                <UserRound size={18} />
              </span>
              <div>
                <p className="text-xs font-semibold text-slate-400">用户名</p>
                <p className="text-sm font-bold text-slate-900">{user?.username || "未命名用户"}</p>
              </div>
            </div>
            <div className="flex items-center gap-3 rounded-2xl bg-slate-50 px-4 py-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
                <Mail size={18} />
              </span>
              <div>
                <p className="text-xs font-semibold text-slate-400">登录邮箱</p>
                <p className="text-sm font-bold text-slate-900">{user?.userAccount || "未登录"}</p>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
