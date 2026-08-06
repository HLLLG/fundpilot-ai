"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Copy, Home, RotateCcw } from "lucide-react";
import { reportClientError } from "@/lib/clientErrorReporter";

/**
 * 路由级错误边界。
 *
 * 除了不把白屏丢给用户，它还做一件对排查更重要的事：把渲染错误连同堆栈上报，
 * 并把服务端返回的分组指纹作为「报障编号」显示出来。用户只要报这串编号，
 * 就能在 /admin/ops 里直接定位到对应堆栈，不必再靠口头描述复现。
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
    void reportClientError({
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
    }).then((result) => {
      if (active && result?.fingerprint) {
        setReference(result.fingerprint);
      }
    });
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
    <main className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-12">
      <div className="w-full max-w-lg rounded-2xl border border-slate-200 bg-white p-6 shadow-[0_16px_40px_rgba(15,23,42,0.08)]">
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-amber-50 text-amber-600"
          >
            <AlertTriangle size={20} />
          </span>
          <div className="min-w-0">
            <h1 className="text-lg font-bold text-slate-900">这个页面出错了</h1>
            <p className="mt-1 text-sm text-slate-600">
              错误详情已自动上报，无需截图。可以先重试，或返回首页继续操作。
            </p>
          </div>
        </div>

        {reference ? (
          <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50 p-3">
            <p className="text-xs font-bold text-slate-500">报障编号</p>
            <div className="mt-1 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate font-mono text-sm text-slate-800">
                {reference}
              </code>
              <button
                type="button"
                onClick={copyReference}
                className="flex min-h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-700 transition hover:bg-slate-50"
              >
                <Copy size={14} aria-hidden="true" />
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
            className="flex min-h-11 items-center gap-2 rounded-xl bg-[var(--brand)] px-4 py-2.5 text-sm font-bold text-white transition hover:bg-[var(--brand-strong)]"
          >
            <RotateCcw size={16} aria-hidden="true" />
            重试
          </button>
          <Link
            href="/"
            className="flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-bold text-slate-700 transition hover:bg-slate-50"
          >
            <Home size={16} aria-hidden="true" />
            返回首页
          </Link>
        </div>
      </div>
    </main>
  );
}
