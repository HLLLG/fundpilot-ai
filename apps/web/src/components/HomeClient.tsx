"use client";

import dynamic from "next/dynamic";
import type { ReactNode } from "react";

import { useAuth } from "@/components/AuthProvider";

const Dashboard = dynamic(
  () => import("@/components/Dashboard").then((module) => module.Dashboard),
  {
    loading: () => (
      <main className="premium-bg flex min-h-screen items-center justify-center px-4">
        <div className="section-card flex items-center gap-3 px-5 py-4 text-sm text-slate-600" role="status">
          <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-[var(--brand)]" aria-hidden />
          正在加载工作台…
        </div>
      </main>
    ),
  },
);

export function HomeClient({ landing }: { landing: ReactNode }) {
  const { user } = useAuth();
  return user ? <Dashboard key={user.id} /> : landing;
}
