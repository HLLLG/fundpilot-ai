import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FundPilot AI | 私人基金投研助手",
  description: "本地优先的基金截图识别、风控和 DeepSeek 投研日报。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
