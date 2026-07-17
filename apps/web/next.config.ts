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

export default nextConfig;
