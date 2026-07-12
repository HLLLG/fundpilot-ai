import type { Metadata } from "next";

import { BRAND } from "@/lib/brand";

export const metadata: Metadata = {
  title: `免费注册 | ${BRAND.name}`,
  description: `注册${BRAND.name}账号，导入基金持仓并获取个性化投研分析与风险提示。`,
  alternates: {
    canonical: "/register",
  },
  robots: {
    index: false,
    follow: false,
  },
};

export default function RegisterLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
