"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { CheckCircle2, KeyRound } from "lucide-react";
import { AuthShell } from "@/components/AuthShell";
import { completePasswordReset } from "@/lib/api";
import { clearAccessToken } from "@/lib/auth";

export default function ResetPasswordPage() {
  const [token, setToken] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [completed, setCompleted] = useState(false);

  useEffect(() => {
    const fragment = window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : window.location.hash;
    setToken(new URLSearchParams(fragment).get("token") ?? "");
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (!token) {
      setError("重置链接不完整，请联系管理员重新生成。");
      return;
    }
    if (password !== confirmPassword) {
      setError("两次输入的密码不一致。");
      return;
    }
    setSubmitting(true);
    try {
      await completePasswordReset({ token, newPassword: password });
      clearAccessToken();
      setCompleted(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "密码重置失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthShell mode="login">
      <div className="mb-8">
        <p className="research-kicker">SECURE ACCOUNT RECOVERY</p>
        <h1 className="font-display mt-2 text-3xl font-bold text-[var(--brand-deep)]">
          设置新密码
        </h1>
        <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
          此链接只能使用一次。密码更新后，账户在其他设备上的旧登录会立即失效。
        </p>
      </div>

      {completed ? (
        <div className="rounded-2xl border p-5" role="status" style={{ borderColor: "var(--success-border)", background: "var(--success-bg)", color: "var(--success-fg)" }}>
          <CheckCircle2 size={28} aria-hidden />
          <h2 className="mt-3 text-lg font-black">密码已更新</h2>
          <p className="mt-2 text-sm leading-6">请使用新密码重新登录。</p>
          <Link href="/login" className="btn-primary mt-5 w-full" prefetch={false}>
            前往登录
          </Link>
        </div>
      ) : (
        <form className="space-y-5" onSubmit={onSubmit}>
          <input
            type="text"
            name="username"
            autoComplete="username"
            value=""
            readOnly
            hidden
          />
          <label className="block text-sm font-semibold text-slate-700">
            新密码
            <span className="relative mt-1.5 block">
              <KeyRound
                size={17}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                aria-hidden
              />
              <input
                type="password"
                minLength={8}
                maxLength={128}
                required
                autoComplete="new-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="input-field pl-10"
                placeholder="至少 8 位"
              />
            </span>
          </label>
          <label className="block text-sm font-semibold text-slate-700">
            再次输入新密码
            <input
              type="password"
              minLength={8}
              maxLength={128}
              required
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className="input-field mt-1.5"
            />
          </label>
          {token === "" ? (
            <div className="inline-notice inline-notice-error" role="alert">
              <span className="inline-notice-message">
                重置链接缺少安全令牌，请联系管理员重新生成。
              </span>
            </div>
          ) : null}
          {error ? (
            <div className="inline-notice inline-notice-error" role="alert">
              <span className="inline-notice-message">{error}</span>
            </div>
          ) : null}
          <button
            type="submit"
            disabled={submitting || token === null || token === ""}
            aria-busy={submitting}
            className="btn-primary w-full"
          >
            {submitting ? "正在更新…" : "更新密码并退出旧会话"}
          </button>
        </form>
      )}
    </AuthShell>
  );
}
