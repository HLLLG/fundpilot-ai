"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";
import { loginUser } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { AuthShell } from "@/components/AuthShell";
import { safeLoginRedirect } from "@/lib/authRedirect";

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
      router.replace(safeLoginRedirect(redirect));
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell mode="login">
        <div className="mb-8">
          <p className="research-kicker">ACCOUNT ACCESS</p>
          <h1 className="font-display mt-2 text-3xl font-bold text-[var(--brand-deep)]">欢迎回来</h1>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">继续查看今日组合状态、风险变化与下一步行动。</p>
        </div>
        <form className="space-y-5" onSubmit={onSubmit}>
          <label className="block text-sm font-semibold text-slate-700">
            邮箱
            <input
              type="email"
              required
              autoComplete="email"
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "login-error" : undefined}
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
              aria-invalid={Boolean(error)}
              aria-describedby={error ? "login-error" : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input-field mt-1.5"
              placeholder="至少 8 位"
            />
          </label>
          {error ? <p id="login-error" role="alert" className="auth-error">{error}</p> : null}
          {error ? <p className="auth-recovery">请核对账号与密码后重试，已输入内容会保留。</p> : null}
          <button type="submit" disabled={submitting} aria-busy={submitting} className="btn-primary w-full">
            {submitting ? "登录中…" : "登录"}
          </button>
        </form>
        <p className="mt-7 text-sm text-[var(--muted)]">
          还没有账号？{" "}
          <Link
            href="/register"
            prefetch={false}
            className="auth-inline-link font-semibold text-[var(--brand-strong)] hover:underline"
          >
            免费注册
          </Link>
        </p>
    </AuthShell>
  );
}
