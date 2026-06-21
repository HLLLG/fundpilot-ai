"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";
import { loginUser } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { BrandMark } from "@/components/BrandMark";

export default function LoginPage() {
  const { setSession } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [userAccount, setUserAccount] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const session = await loginUser({ userAccount, password });
      setSession(session.accessToken, session.user);
      const redirect = searchParams.get("redirect");
      router.replace(redirect ? decodeURIComponent(redirect) : "/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="landing-hero-bg flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <Link href="/" className="mb-7">
        <BrandMark size="lg" showEnglish />
      </Link>
      <div className="w-full max-w-md rounded-[var(--radius-card)] border border-slate-200/80 bg-white p-8 shadow-[var(--shadow-lg)]">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-black text-slate-900">欢迎回来</h1>
          <p className="mt-2 text-sm text-slate-500">登录查看今日 AI 简报与持仓分析</p>
        </div>
        <form className="space-y-4" onSubmit={onSubmit}>
          <label className="block text-sm font-semibold text-slate-700">
            邮箱
            <input
              type="email"
              required
              autoComplete="email"
              value={userAccount}
              onChange={(e) => setUserAccount(e.target.value)}
              className="input-field mt-1.5"
              placeholder="you@example.com"
            />
          </label>
          <label className="block text-sm font-semibold text-slate-700">
            密码
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input-field mt-1.5"
              placeholder="至少 8 位"
            />
          </label>
          {error ? (
            <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
          ) : null}
          <button type="submit" disabled={submitting} className="btn-primary w-full">
            {submitting ? "登录中…" : "登录"}
          </button>
        </form>
        <p className="mt-6 text-center text-sm text-slate-500">
          还没有账号？{" "}
          <Link href="/register" className="font-semibold text-[var(--brand)] hover:underline">
            免费注册
          </Link>
        </p>
      </div>
      <Link href="/" className="mt-6 text-xs text-slate-400 hover:text-slate-600">
        ← 返回首页
      </Link>
    </div>
  );
}
