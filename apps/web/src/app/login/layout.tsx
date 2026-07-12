import type { Metadata } from "next";
import { Suspense } from "react";

import { BrandMark } from "@/components/BrandMark";
import { BRAND } from "@/lib/brand";

export const metadata: Metadata = {
  title: `登录 | ${BRAND.name}`,
  description: `登录${BRAND.name}，查看你的基金持仓、盈亏分析与投研日报。`,
  alternates: {
    canonical: "/login",
  },
  robots: {
    index: false,
    follow: false,
  },
};

export default function LoginLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <Suspense fallback={<LoginPageFallback />}>
      {children}
    </Suspense>
  );
}

function LoginPageFallback() {
  return (
    <div className="landing-hero-bg flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <BrandMark size="lg" showEnglish />
      <p
        className="mt-6 rounded-full border border-[var(--line)] bg-white px-4 py-2.5 text-sm font-semibold text-[var(--muted)] shadow-[var(--shadow-sm)]"
        role="status"
        aria-live="polite"
      >
        正在准备登录…
      </p>
    </div>
  );
}
