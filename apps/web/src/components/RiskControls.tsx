"use client";

import { ShieldCheck, SlidersHorizontal } from "lucide-react";
import type { InvestorProfile } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

type RiskControlsProps = {
  profile: InvestorProfile;
  onChange: (profile: InvestorProfile) => void;
  onAnalyze: () => void;
  isBusy: boolean;
};

export function RiskControls({ profile, onChange, onAnalyze, isBusy }: RiskControlsProps) {
  return (
    <section className="glass-panel min-w-0 rounded-[28px] p-6">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-emerald-500 text-white">
            <ShieldCheck size={22} />
          </div>
          <h2 className="text-xl font-black text-slate-950">个人风控画像</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">模型输出之前，先用硬规则把“能不能动”框住。</p>
        </div>
        <StatusPill tone="green">稳健模式</StatusPill>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block rounded-3xl bg-white p-4 shadow-sm">
          <span className="text-xs font-bold text-slate-400">投资风格</span>
          <input
            value={profile.style}
            onChange={(event) => onChange({ ...profile, style: event.target.value })}
            className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-2 text-sm font-semibold outline-none focus:border-blue-400"
          />
        </label>
        <label className="block rounded-3xl bg-white p-4 shadow-sm">
          <span className="text-xs font-bold text-slate-400">持有周期</span>
          <input
            value={profile.horizon}
            onChange={(event) => onChange({ ...profile, horizon: event.target.value })}
            className="mt-2 w-full rounded-2xl border border-slate-200 px-3 py-2 text-sm font-semibold outline-none focus:border-blue-400"
          />
        </label>
        <label className="block rounded-3xl bg-white p-4 shadow-sm">
          <span className="text-xs font-bold text-slate-400">最大浮亏线</span>
          <div className="mt-3 flex items-center gap-3">
            <input
              type="range"
              min={3}
              max={20}
              value={profile.max_drawdown_percent}
              onChange={(event) =>
                onChange({ ...profile, max_drawdown_percent: Number(event.target.value) })
              }
              className="w-full accent-blue-600"
            />
            <span className="w-12 text-right text-sm font-black text-slate-950">
              {profile.max_drawdown_percent}%
            </span>
          </div>
        </label>
        <label className="block rounded-3xl bg-white p-4 shadow-sm">
          <span className="text-xs font-bold text-slate-400">单只集中度上限</span>
          <div className="mt-3 flex items-center gap-3">
            <input
              type="range"
              min={20}
              max={60}
              value={profile.concentration_limit_percent}
              onChange={(event) =>
                onChange({ ...profile, concentration_limit_percent: Number(event.target.value) })
              }
              className="w-full accent-emerald-500"
            />
            <span className="w-12 text-right text-sm font-black text-slate-950">
              {profile.concentration_limit_percent}%
            </span>
          </div>
        </label>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="flex items-center justify-between rounded-2xl bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm">
          偏定投
          <input
            type="checkbox"
            checked={profile.prefer_dca}
            onChange={(event) => onChange({ ...profile, prefer_dca: event.target.checked })}
            className="h-5 w-5 accent-blue-600"
          />
        </label>
        <label className="flex items-center justify-between rounded-2xl bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm">
          拒绝追高
          <input
            type="checkbox"
            checked={profile.avoid_chasing}
            onChange={(event) => onChange({ ...profile, avoid_chasing: event.target.checked })}
            className="h-5 w-5 accent-rose-500"
          />
        </label>
      </div>

      <button
        type="button"
        onClick={onAnalyze}
        disabled={isBusy}
        data-testid="analyze"
        className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-full bg-slate-950 px-5 py-3 text-sm font-black text-white shadow-[0_16px_36px_rgba(15,23,42,0.22)] transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
      >
        <SlidersHorizontal size={18} />
        {isBusy ? "正在生成投研日报..." : "生成今日基金操作日报"}
      </button>
    </section>
  );
}
