import type { Metadata } from "next";

import { BRAND } from "@/lib/brand";

export const metadata: Metadata = {
  title: `账号设置 | ${BRAND.name}`,
  description: `查看当前${BRAND.name}账号信息。`,
  alternates: {
    canonical: "/settings",
  },
  robots: {
    index: false,
    follow: false,
  },
};

export default function SettingsLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
