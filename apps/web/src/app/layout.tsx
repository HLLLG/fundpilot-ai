import { Sora } from "next/font/google";
import { AuthProvider } from "@/components/AuthProvider";
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

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={`${sora.variable} antialiased`}>
        <AuthProvider>
          <WebVitalsReporter />
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
