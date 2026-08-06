"use client";

import { useEffect, useState } from "react";
import { API_BASE, RELEASE_TAG } from "@/lib/api/base";

/**
 * 根级错误边界：root layout 自身渲染失败时的最后一道防线。
 *
 * 这一层已经没有 layout 可用，必须自带 <html>/<body>，也不能依赖任何 Provider
 * 或全局样式（globals.css 可能正是加载失败的那个文件），所以样式全部内联。
 * ClientErrorReporter 此时也没能挂载，因此这里必须自己完成上报。
 *
 * 上报刻意不复用 clientErrorReporter：Next 会把 global-error 打成独立入口，
 * 静态引入那个模块会把它整份复制进本 chunk（实测 +3 KiB gzip × 全部路由）。
 * 这里只需要一次性的 POST，自己写反而更贴合「零依赖兜底」的定位。
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [reference, setReference] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const stack =
      [error.digest ? `Next.js digest: ${error.digest}` : null, error.stack]
        .filter(Boolean)
        .join("\n") || null;
    void fetch(`${API_BASE}/api/telemetry/client-errors`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: (error.message || "应用根布局渲染失败").slice(0, 2000),
        errorType: (error.name || "Error").slice(0, 180),
        stack: stack ? stack.slice(0, 20_000) : null,
        level: "fatal",
        kind: "react_render",
        path: window.location.pathname || "/",
        release: RELEASE_TAG,
      }),
      keepalive: true,
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((result: { fingerprint?: string } | null) => {
        if (active && result?.fingerprint) {
          setReference(result.fingerprint);
        }
      })
      // 兜底页面自身绝不能再抛错。
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [error]);

  return (
    <html lang="zh-CN">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "24px",
          background: "#f8fafc",
          color: "#0f172a",
          fontFamily:
            'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif',
        }}
      >
        <div
          style={{
            width: "100%",
            maxWidth: "480px",
            background: "#ffffff",
            border: "1px solid #e2e8f0",
            borderRadius: "16px",
            padding: "24px",
          }}
        >
          <h1 style={{ margin: 0, fontSize: "18px", fontWeight: 700 }}>
            应用加载失败
          </h1>
          <p style={{ marginTop: "8px", fontSize: "14px", color: "#475569" }}>
            错误详情已自动上报。请刷新页面重试；如果反复出现，把下面的报障编号发给我们即可。
          </p>
          {reference ? (
            <p
              style={{
                marginTop: "16px",
                padding: "12px",
                background: "#f1f5f9",
                borderRadius: "12px",
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                fontSize: "13px",
                wordBreak: "break-all",
              }}
            >
              {reference}
            </p>
          ) : null}
          <button
            type="button"
            onClick={reset}
            style={{
              marginTop: "24px",
              minHeight: "44px",
              padding: "10px 20px",
              border: "none",
              borderRadius: "12px",
              background: "#2356e0",
              color: "#ffffff",
              fontSize: "14px",
              fontWeight: 700,
              cursor: "pointer",
            }}
          >
            重新加载
          </button>
        </div>
      </body>
    </html>
  );
}
