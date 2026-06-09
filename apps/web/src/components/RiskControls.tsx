"use client";

import { ShieldCheck, SlidersHorizontal } from "lucide-react";
import type { AnalysisMode, InvestorProfile } from "@/lib/api";
import { AnalysisModeToggle } from "@/components/AnalysisModeToggle";
import { StatusPill } from "@/components/StatusPill";

const EXPECTED_INVESTMENT_MIN = 10_000;
const EXPECTED_INVESTMENT_MAX = 100_000;
const EXPECTED_INVESTMENT_STEP = 5_000;
const EXPECTED_INVESTMENT_DEFAULT = 30_000;

function resolveExpectedInvestmentAmount(profile: InvestorProfile): number {
  const value = profile.expected_investment_amount ?? EXPECTED_INVESTMENT_DEFAULT;
  return Math.min(
    EXPECTED_INVESTMENT_MAX,
    Math.max(EXPECTED_INVESTMENT_MIN, value),
  );
}

type RiskControlsProps = {
  profile: InvestorProfile;
  analysisMode: AnalysisMode;
  onAnalysisModeChange: (mode: AnalysisMode) => void;
  onChange: (profile: InvestorProfile) => void;
  onAnalyze: () => void;
  isBusy: boolean;
  ocrWarningCount?: number;
  hasBlockingErrors?: boolean;
};

export function RiskControls({
  profile,
  analysisMode,
  onAnalysisModeChange,
  onChange,
  onAnalyze,
  isBusy,
  ocrWarningCount = 0,
  hasBlockingErrors = false,
}: RiskControlsProps) {
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
        <label className="block rounded-3xl bg-white p-4 shadow-sm sm:col-span-2">
          <span className="text-xs font-bold text-slate-400">期望投入总额</span>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">
            用于计算持仓占比。减仓后仍按计划投入规模判断集中度，避免按当前总市值误判偏高。
          </p>
          <div className="mt-3 flex items-center gap-3">
            <input
              type="range"
              min={EXPECTED_INVESTMENT_MIN}
              max={EXPECTED_INVESTMENT_MAX}
              step={EXPECTED_INVESTMENT_STEP}
              value={resolveExpectedInvestmentAmount(profile)}
              onChange={(event) =>
                onChange({
                  ...profile,
                  expected_investment_amount: Number(event.target.value),
                })
              }
              className="w-full accent-violet-500"
            />
            <span className="w-24 shrink-0 text-right text-sm font-black tabular-nums text-slate-950">
              {resolveExpectedInvestmentAmount(profile).toLocaleString("zh-CN")} 元
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

      <div className="mt-4">
        <AnalysisModeToggle mode={analysisMode} onChange={onAnalysisModeChange} />
      </div>

      {ocrWarningCount > 0 ? (
        <p className="mt-4 rounded-2xl border border-amber-100 bg-amber-50 px-4 py-3 text-xs font-semibold leading-5 text-amber-900">
          数据识别有 {ocrWarningCount} 处待留意。
          {hasBlockingErrors
            ? "存在严重项，建议先在账户汇总核对后再生成。"
            : "可在账户汇总确认后继续生成。"}
        </p>
      ) : null}

      <button
        type="button"
        onClick={onAnalyze}
        disabled={isBusy || hasBlockingErrors}
        data-testid="analyze"
        className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-full bg-slate-950 px-5 py-3 text-sm font-black text-white shadow-[0_16px_36px_rgba(15,23,42,0.22)] transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
      >
        <SlidersHorizontal size={18} />
        {isBusy
          ? "正在生成投研日报..."
          : hasBlockingErrors
            ? "请先处理检查清单中的严重项"
            : "生成今日基金操作日报"}
      </button>
    </section>
  );
}
