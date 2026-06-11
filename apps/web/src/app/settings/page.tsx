"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { ArrowLeft, CheckCircle2, Link2, Smartphone } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";
import { bindWechatAccount } from "@/lib/api";

export default function SettingsPage() {
  const { user, refreshUser } = useAuth();
  const [cloudbaseUid, setCloudbaseUid] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const wechatBound = Boolean(user?.wechatBound);
  const isWechatOnly =
    user?.userAccount?.endsWith("@wechat.fundpilot") ?? false;

  async function onBind(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSuccess(null);
    const uid = cloudbaseUid.trim();
    if (!uid) {
      setError("请填写 CloudBase 用户 ID");
      return;
    }
    setSubmitting(true);
    try {
      await bindWechatAccount({ cloudbaseUid: uid });
      await refreshUser();
      setSuccess("微信账号已绑定，小程序登录后将看到相同持仓。");
      setCloudbaseUid("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "绑定失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50/30 to-indigo-50/40 px-4 py-8">
      <div className="mx-auto max-w-lg">
        <Link
          href="/"
          className="mb-6 inline-flex items-center gap-2 text-sm font-semibold text-slate-600 transition hover:text-blue-600"
        >
          <ArrowLeft size={16} />
          返回首页
        </Link>

        <h1 className="text-2xl font-black text-slate-900">账号设置</h1>
        <p className="mt-1 text-sm text-slate-500">管理登录方式与小程序同步</p>

        <section className="mt-8 rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-sm font-bold uppercase tracking-wide text-slate-400">
            当前账号
          </h2>
          <p className="mt-3 text-lg font-bold text-slate-900">{user?.username}</p>
          <p className="text-sm text-slate-500">{user?.userAccount}</p>
        </section>

        <section className="mt-6 rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
          <div className="flex items-start gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-green-50 text-green-600">
              <Smartphone size={20} />
            </span>
            <div className="flex-1">
              <h2 className="text-base font-bold text-slate-900">微信小程序</h2>
              <p className="mt-1 text-sm leading-relaxed text-slate-500">
                绑定后，使用同一 CloudBase 微信账号在小程序登录，即可查看与 Web 端相同的基金持仓。
              </p>
            </div>
          </div>

          {wechatBound ? (
            <div className="mt-5 flex items-center gap-2 rounded-2xl bg-emerald-50 px-4 py-3 text-sm font-semibold text-emerald-700">
              <CheckCircle2 size={18} />
              已绑定微信
            </div>
          ) : (
            <>
              <div className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50/80 px-4 py-3 text-sm text-slate-600">
                <p className="font-semibold text-slate-700">绑定步骤</p>
                <ol className="mt-2 list-decimal space-y-1 pl-4">
                  <li>在小程序完成微信 / CloudBase 登录</li>
                  <li>将小程序侧的 CloudBase UID 填入下方（开发联调）</li>
                  <li>或在上云后由 CloudBase Access Token 自动校验绑定</li>
                </ol>
              </div>

              {!isWechatOnly ? (
                <form className="mt-5 space-y-4" onSubmit={onBind}>
                  <label className="block text-sm font-semibold text-slate-700">
                    CloudBase 用户 ID
                    <input
                      type="text"
                      value={cloudbaseUid}
                      onChange={(e) => setCloudbaseUid(e.target.value)}
                      placeholder="例如 cloudbase-test-uid-001"
                      className="mt-1.5 w-full rounded-xl border border-slate-200 px-3 py-2.5 font-normal outline-none ring-blue-200 focus:ring-2"
                    />
                  </label>
                  <p className="text-xs text-slate-400">
                    开发环境可在后端开启{" "}
                    <code className="rounded bg-slate-100 px-1">FUND_AI_CLOUDBASE_AUTH_DEV_MODE=true</code>
                    ，小程序登录后使用相同 UID 绑定。
                  </p>
                  {error ? (
                    <p className="rounded-xl bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
                  ) : null}
                  {success ? (
                    <p className="rounded-xl bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
                      {success}
                    </p>
                  ) : null}
                  <button
                    type="submit"
                    disabled={submitting}
                    className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-blue-600 to-indigo-500 py-3 text-sm font-bold text-white shadow-lg shadow-blue-500/20 disabled:opacity-60"
                  >
                    <Link2 size={16} />
                    {submitting ? "绑定中…" : "绑定微信"}
                  </button>
                </form>
              ) : (
                <p className="mt-5 text-sm text-slate-500">
                  当前为微信小程序注册账号，已关联 CloudBase，无需重复绑定。
                </p>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
