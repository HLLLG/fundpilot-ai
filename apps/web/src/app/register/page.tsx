"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { Check, ScanLine, Sparkles, TrendingUp } from "lucide-react";
import { registerUser } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { BrandMark } from "@/components/BrandMark";

const REGISTER_BENEFITS = [
  { icon: ScanLine, text: "截图识别持仓，零手动录入" },
  { icon: TrendingUp, text: "盘中板块涨跌实时关联" },
  { icon: Sparkles, text: "每日 AI 简报，说人话的建议" },
];

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

      <div className="grid w-full max-w-4xl gap-6 lg:grid-cols-[1fr_1.1fr] lg:items-start">
        <div className="section-card hidden p-6 lg:block">
          <p className="text-sm font-semibold text-[var(--muted)]">注册即享 · 永久免费</p>
          <h2 className="font-display mt-2 text-2xl font-extrabold tracking-tight text-slate-950">
            1 分钟注册，30 秒看懂基金
          </h2>
          <ul className="mt-6 flex flex-col gap-4">
            {REGISTER_BENEFITS.map(({ icon: Icon, text }) => (
              <li key={text} className="flex items-start gap-3 text-sm text-slate-700">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-[var(--brand-soft)] text-[var(--brand-strong)]">
                  <Icon size={18} strokeWidth={2.2} />
                </span>
                <span className="pt-1.5 leading-6">{text}</span>
              </li>
            ))}
          </ul>
          <div className="mt-8 flex flex-wrap gap-2">
            {["无需绑卡", "本地识别", "隐私隔离"].map((tag) => (
              <span key={tag} className="badge">
                <Check size={11} strokeWidth={3} />
                {tag}
              </span>
            ))}
          </div>
        </div>

        <div className="w-full rounded-[var(--radius-card)] border border-slate-200/80 bg-white p-8 shadow-[var(--shadow-lg)]">
          <div className="mb-8 text-center lg:text-left">
            <h1 className="text-2xl font-black text-slate-900">创建账号</h1>
            <p className="mt-2 text-sm text-slate-500">注册后上传持仓截图，立即生成你的第一份 AI 简报</p>
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
                placeholder="至少 8 位"
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
              {submitting ? "注册中…" : "免费注册，开始使用"}
            </button>
          </form>
          <p className="mt-6 text-center text-sm text-slate-500 lg:text-left">
            已有账号？{" "}
            <Link href="/login" className="font-semibold text-[var(--brand)] hover:underline">
              登录
            </Link>
          </p>
          <p className="mt-5 text-center text-[11px] leading-5 text-slate-400 lg:text-left">
            投资有风险，入市需谨慎。本工具内容仅供参考，不构成投资建议。
          </p>
        </div>
      </div>

      <Link href="/" className="mt-6 text-xs text-slate-400 hover:text-slate-600">
        ← 返回首页
      </Link>
    </div>
  );
}
