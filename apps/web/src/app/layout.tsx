import type { Metadata } from "next";
import { Sora } from "next/font/google";
import { AuthProvider } from "@/components/AuthProvider";
import "./globals.css";

// 拉丁字与大数字：Sora（几何感、克制高级，等宽数字适合金融场景）。
// 中文走系统高质量字体栈（PingFang / HarmonyOS / 雅黑 / Noto 兜底），零下载、不拖慢首屏。
const sora = Sora({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
  weight: ["400", "500", "600", "700", "800"],
});

export const metadata: Metadata = {
  title: "好基灵 | 截个图就懂你的基金",
  description:
    "好基灵：自动识别持仓明细 · 实时追踪板块冷暖 · 每天一份听得懂的投研日报。",
  icons: {
    icon: [
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/icon.png", type: "image/png" },
    ],
    apple: [{ url: "/icon.png", type: "image/png" }],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className={`${sora.variable} antialiased`}>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
