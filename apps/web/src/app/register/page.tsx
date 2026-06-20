"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { registerUser } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { BrandMark } from "@/components/BrandMark";

export default function RegisterPage() {
  const { setSession } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [userAccount, setUserAccount] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (password !== confirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }
    setSubmitting(true);
    try {
      const session = await registerUser({
        userAccount,
        password,
        username: username || undefined,
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
    <div className="landing-hero-bg flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <Link href="/" className="mb-7">
        <BrandMark size="lg" showEnglish />
      </Link>
      <div className="w-full max-w-md rounded-[var(--radius-card)] border border-slate-200/80 bg-white p-8 shadow-[var(--shadow-lg)]">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-black text-slate-900">创建账号</h1>
          <p className="mt-2 text-sm text-slate-500">注册好基灵，截个图就看懂你的基金</p>
        </div>
        <form className="space-y-4" onSubmit={onSubmit}>
          <label className="block text-sm font-semibold text-slate-700">
            昵称（可选）
            <input
              type="text"
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
              minLength={8}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="input-field mt-1.5"
            />
          </label>
          <label className="block text-sm font-semibold text-slate-700">
            确认密码
            <input
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="input-field mt-1.5"
            />
          </label>
          {error ? (
            <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
          ) : null}
          <button type="submit" disabled={submitting} className="btn-primary w-full">
            {submitting ? "注册中…" : "免费注册"}
          </button>
        </form>
        <p className="mt-6 text-center text-sm text-slate-500">
          已有账号？{" "}
          <Link href="/login" className="font-semibold text-[var(--brand)] hover:underline">
            登录
          </Link>
        </p>
        <p className="mt-5 text-center text-[11px] leading-5 text-slate-400">
          投资有风险，入市需谨慎。本工具内容仅供参考，不构成投资建议。
        </p>
      </div>
      <Link href="/" className="mt-6 text-xs text-slate-400 hover:text-slate-600">
        ← 返回首页
      </Link>
    </div>
  );
}
