"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { registerUser } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { AuthShell } from "@/components/AuthShell";

export default function RegisterPage() {
  const { setSession } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [userAccount, setUserAccount] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const formData = new FormData(event.currentTarget);
    const submittedUsername = String(formData.get("username") ?? "");
    const submittedUserAccount = String(formData.get("userAccount") ?? "");
    const submittedPassword = String(formData.get("password") ?? "");
    const submittedConfirmPassword = String(formData.get("confirmPassword") ?? "");

    if (submittedPassword !== submittedConfirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }
    setSubmitting(true);
    try {
      const session = await registerUser({
        userAccount: submittedUserAccount,
        password: submittedPassword,
        username: submittedUsername || undefined,
      });
      setSession(session.accessToken, session.user);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell mode="register">
          <div className="mb-7">
            <p className="research-kicker">CREATE DESK</p>
            <h1 className="font-display mt-2 text-3xl font-bold text-[var(--brand-deep)]">创建账号</h1>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
              注册后即可上传持仓截图，校对后建立你的第一份研究摘要。
            </p>
          </div>
          <form className="space-y-4" onSubmit={onSubmit}>
            <label className="block text-sm font-semibold text-slate-700">
              昵称（可选）
              <input
                type="text"
                name="username"
                autoComplete="nickname"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="input-field mt-1.5"
                placeholder="投研用户"
              />
            </label>
            <label className="block text-sm font-semibold text-slate-700">
              邮箱
              <input
                type="email"
                name="userAccount"
                required
                autoComplete="email"
                aria-invalid={Boolean(error)}
                aria-describedby={error ? "register-error" : undefined}
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
                name="password"
                required
                minLength={8}
                autoComplete="new-password"
                aria-invalid={Boolean(error)}
                aria-describedby={error ? "register-error" : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input-field mt-1.5"
                placeholder="至少 8 位"
              />
            </label>
            <label className="block text-sm font-semibold text-slate-700">
              确认密码
              <input
                type="password"
                name="confirmPassword"
                required
                minLength={8}
                autoComplete="new-password"
                aria-invalid={Boolean(error)}
                aria-describedby={error ? "register-error" : undefined}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="input-field mt-1.5"
              />
            </label>
            <p className="-mt-2 text-xs leading-5 text-[var(--muted)]">密码至少 8 位；建议同时包含字母与数字。</p>
            {error ? <p id="register-error" role="alert" className="auth-error">{error}</p> : null}
            {error ? <p className="auth-recovery">请修正后再次提交，其他输入会保留。</p> : null}
            <button type="submit" disabled={submitting} aria-busy={submitting} className="btn-primary w-full">
              {submitting ? "注册中…" : "免费注册，开始使用"}
            </button>
          </form>
          <p className="mt-6 text-sm text-[var(--muted)]">
            已有账号？{" "}
            <Link
              href="/login"
              className="auth-inline-link inline-flex min-h-11 min-w-11 items-center justify-center font-semibold text-[var(--brand-strong)] hover:underline"
            >
              登录
            </Link>
          </p>
          <p className="mt-4 text-xs leading-5 text-[var(--muted)]">
            投资有风险，入市需谨慎。本工具内容仅供参考，不构成投资建议。
          </p>
    </AuthShell>
  );
}
