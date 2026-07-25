import bundleAnalyzer from "@next/bundle-analyzer";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "export",
  // Keep the development module graph separate from production builds. Running
  // `next build` while `next dev` is serving the app must not invalidate chunks
  // that are already loaded in the browser.
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  images: {
    unoptimized: true,
  },
};

// Bundle 分析只在显式 `ANALYZE=true` 时启用，正常 build 完全不受影响，
// 也不会有任何东西进入运行时产物。
const withBundleAnalyzer = bundleAnalyzer({
  enabled: process.env.ANALYZE === "true",
  openAnalyzer: false,
});

export default withBundleAnalyzer(nextConfig);
