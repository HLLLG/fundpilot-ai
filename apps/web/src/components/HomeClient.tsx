"use client";

import dynamic from "next/dynamic";
import type { ReactNode } from "react";

import { useAuth } from "@/components/AuthProvider";
import { WorkspaceSkeleton } from "@/components/WorkspaceSkeleton";

const Dashboard = dynamic(
  () => import("@/components/Dashboard").then((module) => module.Dashboard),
  {
    // 骨架屏与真实工作台壳层同尺寸，chunk 到达时不再产生布局跳动。
    loading: () => <WorkspaceSkeleton message="正在加载工作台…" />,
  },
);

export function HomeClient({ landing }: { landing: ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return <WorkspaceSkeleton message="正在恢复工作台…" />;
  }
  return user ? <Dashboard key={user.id} /> : landing;
}
