"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

/**
 * 路由级错误边界。
 *
 * 除了不把白屏丢给用户，它还做一件对排查更重要的事：把渲染错误连同堆栈上报，
 * 并把服务端返回的分组指纹作为「报障编号」显示出来。用户只要报这串编号，
 * 就能在 /admin/ops 里直接定位到对应堆栈，不必再靠口头描述复现。
 *
 * 体积敏感：Next 会把 error.tsx 打进**每个路由的首屏**包，因为边界必须随时待命。
 * 所以这里刻意不引入 lucide 图标（4 个图标实测让本 chunk 从 2.5 KiB 涨到 7.2 KiB，
 * 而且要乘以全部路由）。next/link 保留 —— 它本就在共享框架 chunk 里，额外成本约为零，
 * 而导航语义上必须是链接（可访问性）。上报模块用动态 import，只在真的崩溃时才加载。
 */
export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [reference, setReference] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let active = true;
    void (async () => {
      const { reportClientError } = await import("@/lib/clientErrorReporter");
      const result = await reportClientError({
        kind: "react_render",
        level: "fatal",
        errorType: error.name || "Error",
        message: error.message || "页面渲染失败",
        // 生产构建下 Server Component 的真实报错只以 digest 形式回传，
        // 存进堆栈首行才能在面板里跟服务端日志对上。
        stack:
          [error.digest ? `Next.js digest: ${error.digest}` : null, error.stack]
            .filter(Boolean)
            .join("\n") || null,
      });
      if (active && result?.fingerprint) {
        setReference(result.fingerprint);
      }
    })();
    return () => {
      active = false;
    };
  }, [error]);

  const copyReference = async () => {
    if (!reference) {
      return;
    }
    try {
      await navigator.clipboard.writeText(reference);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-[var(--background)] px-4 py-12">
      <div className="section-card w-full max-w-lg p-6">
        <p className="ink-label">Desk Fault</p>
        <h1 className="mt-2 text-lg font-bold text-[var(--brand-deep)]">这个页面出错了</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          错误详情已自动上报，无需截图。可以先重试，或返回首页继续操作。
        </p>

        {reference ? (
          <div className="mt-5 rounded-xl border border-[var(--line)] bg-[var(--surface-muted)] p-3">
            <p className="text-xs font-bold text-[var(--muted)]">报障编号</p>
            <div className="mt-1 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate font-mono text-sm text-slate-800">
                {reference}
              </code>
              <button
                type="button"
                onClick={copyReference}
                className="min-h-9 shrink-0 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-700 transition hover:bg-slate-50"
              >
                {copied ? "已复制" : "复制"}
              </button>
            </div>
            <p className="mt-2 text-xs text-slate-500">
              反馈问题时附上这个编号，可以直接定位到具体错误。
            </p>
          </div>
        ) : null}

        <div className="mt-6 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={reset}
            className="min-h-11 rounded-xl bg-[var(--brand)] px-4 py-2.5 text-sm font-bold text-white transition hover:bg-[var(--brand-strong)]"
          >
            重试
          </button>
          <Link
            href="/"
            className="min-h-11 rounded-xl border border-[var(--line)] bg-[var(--panel)] px-4 py-2.5 text-sm font-bold text-[var(--brand-deep)] transition hover:bg-[var(--surface-muted)]"
          >
            返回首页
          </Link>
        </div>
      </div>
    </main>
  );
}
