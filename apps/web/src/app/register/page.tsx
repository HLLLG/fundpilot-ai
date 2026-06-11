"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { registerUser } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";

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
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-slate-50 via-blue-50/40 to-indigo-50 px-4">
      <div className="w-full max-w-md rounded-3xl border border-slate-200 bg-white p-8 shadow-[0_24px_60px_rgba(15,23,42,0.08)]">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-black text-slate-900">注册 FundPilot</h1>
          <p className="mt-2 text-sm text-slate-500">创建账号，开始管理你的基金持仓</p>
        </div>
        <form className="space-y-4" onSubmit={onSubmit}>
          <label className="block text-sm font-semibold text-slate-700">
            昵称（可选）
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none ring-blue-200 focus:ring-2"
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
              className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none ring-blue-200 focus:ring-2"
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
              className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none ring-blue-200 focus:ring-2"
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
              className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none ring-blue-200 focus:ring-2"
            />
          </label>
          {error ? (
            <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
          ) : null}
          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-xl bg-gradient-to-r from-blue-600 to-indigo-500 py-3 text-sm font-bold text-white shadow-lg shadow-blue-500/25 disabled:opacity-60"
          >
            {submitting ? "注册中…" : "注册"}
          </button>
        </form>
        <p className="mt-6 text-center text-sm text-slate-500">
          已有账号？{" "}
          <Link href="/login" className="font-semibold text-blue-600 hover:underline">
            登录
          </Link>
        </p>
      </div>
    </div>
  );
}
