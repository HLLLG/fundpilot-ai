"use client";

import { useEffect, useState } from "react";
import { ChevronDown, RotateCcw, ShieldCheck, SlidersHorizontal, Sparkles } from "lucide-react";
import type { AnalysisMode, DecisionStyle, InvestorProfile, SwingMonitorScope } from "@/lib/api";
import { takeProfitThresholdPercent } from "@/lib/investmentPresets";
import { AnalysisModeToggle } from "@/components/AnalysisModeToggle";
import { InvestmentPresetSelector } from "@/components/InvestmentPresetSelector";
import { RolePromptEditor } from "@/components/RolePromptEditor";
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

function profileSummary(profile: InvestorProfile): string {
  const invest = resolveExpectedInvestmentAmount(profile);
  const investLabel =
    invest >= 10_000 ? `${Math.round(invest / 10_000)}万` : `${invest}`;
  const style =
    profile.decision_style === "tactical"
      ? "战术"
      : profile.decision_style === "aggressive"
        ? "激进"
        : "稳健";
  return `${style} · 浮亏 ${profile.max_drawdown_percent}% · 集中度 ${profile.concentration_limit_percent}% · 计划投入 ${investLabel}`;
}

type RiskControlsProps = {
  profile: InvestorProfile;
  analysisMode: AnalysisMode;
  rolePrompt: string;
  isRolePromptCustom: boolean;
  onAnalysisModeChange: (mode: AnalysisMode) => void;
  onChange: (profile: InvestorProfile) => void;
  onRolePromptChange: (value: string) => void;
  onRolePromptReset: () => void;
  onAnalyze: () => void;
  isBusy: boolean;
  hasBlockingErrors?: boolean;
  blockingMessage?: string | null;
  readingModeKey?: string | null;
};

export function RiskControls({
  profile,
  analysisMode,
  rolePrompt,
  isRolePromptCustom,
  onAnalysisModeChange,
  onChange,
  onRolePromptChange,
  onRolePromptReset,
  onAnalyze,
  isBusy,
  hasBlockingErrors = false,
  blockingMessage = null,
  readingModeKey = null,
}: RiskControlsProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [rolePromptOpen, setRolePromptOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(readingModeKey == null);

  useEffect(() => {
    setSettingsOpen(readingModeKey == null);
  }, [readingModeKey]);

  if (readingModeKey && !settingsOpen) {
    return (
      <section className="report-control-card section-card min-w-0 overflow-hidden">
        <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="text-sm font-black text-slate-950">本次生成设置</div>
            <p className="mt-1 text-xs text-slate-500">
              {analysisMode === "deep" ? "深度模式" : "快速模式"} · {profileSummary(profile)}
            </p>
            {hasBlockingErrors && blockingMessage ? (
              <p className="mt-1 text-xs font-semibold text-rose-700" role="alert">
                {blockingMessage}
              </p>
            ) : null}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="btn-secondary min-h-11"
            >
              调整设置
            </button>
            <button
              type="button"
              onClick={onAnalyze}
              disabled={isBusy || hasBlockingErrors}
              className="btn-primary min-h-11"
            >
              {isBusy ? "正在生成..." : hasBlockingErrors ? "请先处理严重项" : "重新生成"}
            </button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="report-control-card section-card min-w-0 overflow-hidden">
      <div className="report-control-hero border-b border-[var(--line)] px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-10 w-10 items-center justify-center rounded-2xl bg-[var(--brand-soft)] text-[var(--brand-strong)]">
            <ShieldCheck size={20} strokeWidth={2.3} />
          </span>
          <div>
            <h2 className="font-display text-lg font-extrabold text-slate-950">生成投研日报</h2>
            <p className="mt-0.5 text-xs text-slate-500">AI 结合你的持仓与风险偏好，给出说人话的操作建议</p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <StatusPill
            tone={
              profile.decision_style === "aggressive"
                ? "red"
                : profile.decision_style === "tactical"
                  ? "amber"
                  : "green"
            }
          >
            {profile.decision_style === "aggressive"
              ? "激进"
              : profile.decision_style === "tactical"
                ? "战术"
                : "稳健"}
          </StatusPill>
          {readingModeKey ? (
            <button
              type="button"
              onClick={() => setSettingsOpen(false)}
              className="btn-secondary min-h-11 !px-3 !py-2 !text-xs"
            >
              收起设置
            </button>
          ) : null}
        </div>
      </div>
      </div>

      <div className="p-4 sm:p-5">
      <AnalysisModeToggle mode={analysisMode} onChange={onAnalysisModeChange} compact />

      <div className="mt-4">
        <p className="mb-2 text-[11px] font-bold text-slate-500">投资风格预设</p>
        <InvestmentPresetSelector profile={profile} onChange={onChange} compact />
      </div>

      <div className="mt-4 overflow-hidden rounded-xl border border-slate-100">
        <div className="flex items-center gap-2 px-2">
          <button
            type="button"
            onClick={() => setRolePromptOpen((current) => !current)}
            className="flex min-h-11 min-w-0 flex-1 items-center justify-between gap-2 rounded-lg px-1 text-left hover:bg-slate-50"
            aria-expanded={rolePromptOpen}
            aria-controls="report-role-prompt-settings"
          >
            <span className="flex min-w-0 items-center gap-2">
              <Sparkles size={15} className="shrink-0 text-[var(--brand)]" />
              <span className="text-xs font-bold text-slate-700">AI 分析偏好附录（高级）</span>
              <span className="truncate text-[11px] font-semibold text-slate-500">
                {isRolePromptCustom ? "已添加" : "未添加"}
              </span>
            </span>
            <ChevronDown
              size={15}
              className={`shrink-0 text-slate-500 transition ${rolePromptOpen ? "rotate-180" : ""}`}
              aria-hidden
            />
          </button>
          {rolePromptOpen && isRolePromptCustom ? (
            <button
              type="button"
              onClick={onRolePromptReset}
              className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 text-[11px] font-bold text-slate-600 transition hover:bg-slate-50"
            >
              <RotateCcw size={12} />
              清空附录
            </button>
          ) : null}
        </div>
        {rolePromptOpen ? (
          <div id="report-role-prompt-settings" className="border-t border-slate-100">
            <RolePromptEditor value={rolePrompt} onChange={onRolePromptChange} />
          </div>
        ) : (
          <p id="report-role-prompt-settings" className="border-t border-slate-100 px-3 py-2 text-[11px] leading-5 text-slate-500">
            普通日报无需填写；附录只能补充表达风格和关注角度，不能修改系统决策约束。
          </p>
        )}
      </div>

      {hasBlockingErrors && blockingMessage ? (
        <p
          className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold leading-5 text-rose-800"
          role="alert"
        >
          {blockingMessage}
        </p>
      ) : null}

      <button
        type="button"
        onClick={onAnalyze}
        disabled={isBusy || hasBlockingErrors}
        data-testid="analyze"
        className="btn-primary mt-4 w-full !rounded-xl"
      >
        <SlidersHorizontal size={17} />
        {isBusy
          ? "正在生成..."
          : hasBlockingErrors
            ? "请先处理严重项"
            : "生成今日操作建议"}
      </button>

      <div className="mt-3 overflow-hidden rounded-xl border border-slate-100">
        <button
          type="button"
          onClick={() => setAdvancedOpen((value) => !value)}
          className="flex min-h-11 w-full items-center justify-between gap-2 px-3 text-left text-xs font-bold text-slate-600 hover:bg-slate-50"
          aria-expanded={advancedOpen}
          aria-controls="report-advanced-settings"
        >
          <span>高级设置</span>
          <ChevronDown size={14} className={`shrink-0 transition ${advancedOpen ? "rotate-180" : ""}`} />
        </button>
        {!advancedOpen ? (
          <p id="report-advanced-settings" className="border-t border-slate-100 px-3 py-2 text-[11px] leading-5 text-slate-500">
            {profileSummary(profile)}
          </p>
        ) : (
          <div id="report-advanced-settings" className="grid gap-3 border-t border-slate-100 p-3 sm:grid-cols-2">
            <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3">
              <span className="text-[11px] font-bold text-slate-500">投资风格</span>
              <input
                value={profile.style}
                onChange={(event) => onChange({ ...profile, style: event.target.value })}
                className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-sm font-semibold outline-none focus:border-[var(--brand)]"
              />
            </label>
            <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3">
              <span className="text-[11px] font-bold text-slate-500">持有周期</span>
              <input
                value={profile.horizon}
                onChange={(event) => onChange({ ...profile, horizon: event.target.value })}
                className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-sm font-semibold outline-none focus:border-[var(--brand)]"
              />
            </label>
            <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3">
              <span className="text-[11px] font-bold text-slate-500">最大浮亏线</span>
              <div className="mt-2 flex items-center gap-2">
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
                <span className="w-10 text-right text-xs font-black tabular-nums">
                  {profile.max_drawdown_percent}%
                </span>
              </div>
            </label>
            <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3">
              <span className="text-[11px] font-bold text-slate-500">单只集中度上限</span>
              <div className="mt-2 flex items-center gap-2">
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
                <span className="w-10 text-right text-xs font-black tabular-nums">
                  {profile.concentration_limit_percent}%
                </span>
              </div>
            </label>
            <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3 sm:col-span-2">
              <span className="text-[11px] font-bold text-slate-500">期望投入总额</span>
              <div className="mt-2 flex items-center gap-2">
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
                  className="w-full accent-[var(--brand)]"
                />
                <span className="w-20 shrink-0 text-right text-xs font-black tabular-nums">
                  {resolveExpectedInvestmentAmount(profile).toLocaleString("zh-CN")} 元
                </span>
              </div>
            </label>
            <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3 sm:col-span-2">
              <span className="text-[11px] font-bold text-slate-500">决策风格</span>
              <div className="mt-2 grid grid-cols-3 gap-2" role="group" aria-label="决策风格">
                {(
                  [
                    ["conservative", "稳健"],
                    ["tactical", "战术短线"],
                    ["aggressive", "激进波段"],
                  ] as const satisfies Array<[DecisionStyle, string]>
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={(profile.decision_style ?? "conservative") === value}
                    onClick={() => onChange({ ...profile, decision_style: value })}
                    className={`min-h-11 rounded-lg border px-2 py-2 text-xs font-bold transition ${
                      (profile.decision_style ?? "conservative") === value
                        ? value === "aggressive"
                          ? "border-rose-300 bg-rose-50 text-rose-900"
                          : value === "tactical"
                            ? "border-amber-300 bg-amber-50 text-amber-900"
                            : "border-emerald-300 bg-emerald-50 text-emerald-900"
                        : "border-slate-200 bg-white text-slate-600"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </label>
            {(profile.decision_style === "aggressive" || profile.swing_alerts_enabled) ? (
              <>
                <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3 sm:col-span-2">
                  <span className="text-[11px] font-bold text-slate-500">
                    买卖合计手续费（%）· 扣费止盈线约 {takeProfitThresholdPercent(profile)}%
                  </span>
                  <div className="mt-2 flex items-center gap-2">
                    <input
                      type="range"
                      min={0.5}
                      max={3}
                      step={0.1}
                      value={profile.round_trip_fee_percent ?? 1.5}
                      onChange={(event) =>
                        onChange({
                          ...profile,
                          round_trip_fee_percent: Number(event.target.value),
                        })
                      }
                      className="w-full accent-rose-500"
                    />
                    <span className="w-12 text-right text-xs font-black tabular-nums">
                      {(profile.round_trip_fee_percent ?? 1.5).toFixed(1)}%
                    </span>
                  </div>
                </label>
                <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3 sm:col-span-2">
                  <span className="text-[11px] font-bold text-slate-500">期望净赚（%）</span>
                  <div className="mt-2 flex items-center gap-2">
                    <input
                      type="range"
                      min={0.5}
                      max={3}
                      step={0.5}
                      value={profile.min_net_profit_percent ?? 1.0}
                      onChange={(event) =>
                        onChange({
                          ...profile,
                          min_net_profit_percent: Number(event.target.value),
                        })
                      }
                      className="w-full accent-rose-500"
                    />
                    <span className="w-12 text-right text-xs font-black tabular-nums">
                      {(profile.min_net_profit_percent ?? 1.0).toFixed(1)}%
                    </span>
                  </div>
                </label>
                <label className="flex min-h-11 items-center justify-between rounded-xl border border-slate-100 bg-slate-50/50 px-3 py-2.5 text-sm font-semibold text-slate-700 sm:col-span-2">
                  盘中波段盯盘提醒
                  <input
                    type="checkbox"
                    checked={profile.swing_alerts_enabled ?? profile.decision_style === "aggressive"}
                    onChange={(event) =>
                      onChange({ ...profile, swing_alerts_enabled: event.target.checked })
                    }
                    className="h-4 w-4 accent-rose-500"
                  />
                </label>
                <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3 sm:col-span-2">
                  <span className="text-[11px] font-bold text-slate-500">盯盘范围</span>
                  <div className="mt-2 grid grid-cols-3 gap-2" role="group" aria-label="盯盘范围">
                    {(
                      [
                        ["holdings", "仅持仓"],
                        ["full_market", "全市场"],
                        ["both", "两者"],
                      ] as const satisfies Array<[SwingMonitorScope, string]>
                    ).map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={(profile.swing_monitor_scope ?? "both") === value}
                        onClick={() => onChange({ ...profile, swing_monitor_scope: value })}
                        className={`min-h-11 rounded-lg border px-2 py-2 text-xs font-bold transition ${
                          (profile.swing_monitor_scope ?? "both") === value
                            ? "border-rose-300 bg-rose-50 text-rose-900"
                            : "border-slate-200 bg-white text-slate-600"
                        }`}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </label>
              </>
            ) : null}
            <label className="flex min-h-11 items-center justify-between rounded-xl border border-slate-100 bg-slate-50/50 px-3 py-2.5 text-sm font-semibold text-slate-700">
              偏好定投
              <input
                type="checkbox"
                checked={profile.prefer_dca}
                onChange={(event) => onChange({ ...profile, prefer_dca: event.target.checked })}
                className="h-4 w-4 accent-blue-600"
              />
            </label>
            <label className="flex min-h-11 items-center justify-between rounded-xl border border-slate-100 bg-slate-50/50 px-3 py-2.5 text-sm font-semibold text-slate-700">
              拒绝追高
              <input
                type="checkbox"
                checked={profile.avoid_chasing}
                onChange={(event) => onChange({ ...profile, avoid_chasing: event.target.checked })}
                className="h-4 w-4 accent-rose-500"
              />
            </label>
          </div>
        )}
      </div>
      </div>
    </section>
  );
}
