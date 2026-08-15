import type { Viewport } from "next";
import { Sora } from "next/font/google";
import { AuthProvider } from "@/components/AuthProvider";
import { ClientErrorReporter } from "@/components/ClientErrorReporter";
import { WebVitalsReporter } from "@/components/WebVitalsReporter";
import { SITE_METADATA } from "@/lib/siteMetadata";
import "./globals.css";

// 拉丁字与大数字：Sora（几何感、克制高级，等宽数字适合金融场景）。
// 中文走系统高质量字体栈（PingFang / HarmonyOS / 雅黑 / Noto 兜底），零下载、不拖慢首屏。
const sora = Sora({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
  weight: ["400", "500", "600", "700", "800"],
});

export const metadata = SITE_METADATA;

// Next 默认只发 `width=device-width, initial-scale=1`，缺 `viewport-fit=cover`。
// 而 `.dashboard-bottom-nav` / `.dashboard-shell` 都依赖
// `env(safe-area-inset-bottom)` 给全屏刘海屏留出底部安全区；没有 cover 时 iOS 上
// 这些 env() 一律解析为 0，底栏会压在 Home 指示条上。
// 刻意不设 maximum-scale / user-scalable：禁止缩放会破坏可访问性。
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={`${sora.variable} antialiased`}>
        {/* 放在 AuthProvider 之外：即使鉴权初始化本身出错，全局监听也已就位；
            它只读 localStorage 里的 token，不依赖 Provider。 */}
        <ClientErrorReporter />
        <AuthProvider>
          <WebVitalsReporter />
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
