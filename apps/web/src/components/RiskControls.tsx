"use client";

import { useEffect, useState } from "react";
import { ChevronDown, Loader2, RotateCcw, ShieldCheck, SlidersHorizontal, Sparkles } from "lucide-react";
import type { InvestorProfile } from "@/lib/api";
import { AnalysisScanProgress } from "@/components/AnalysisScanProgress";
import type { AnalysisScanProgress as AnalysisScanProgressState } from "@/lib/analysisScanProgress";
import { RolePromptEditor } from "@/components/RolePromptEditor";

const EXPECTED_INVESTMENT_MIN = 10_000;
const EXPECTED_INVESTMENT_MAX = 100_000;
const EXPECTED_INVESTMENT_STEP = 5_000;
const EXPECTED_INVESTMENT_DEFAULT = 30_000;
// formatter 提到模块作用域：滑杆拖动时这一行会随每一帧重渲染。无选项的
// `Intl.NumberFormat(locale)` 与 `n.toLocaleString(locale)` 输出一致。
const YUAN_FORMATTER = new Intl.NumberFormat("zh-CN");

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
  return `浮亏 ${profile.max_drawdown_percent}% · 集中度 ${profile.concentration_limit_percent}% · 计划投入 ${investLabel}`;
}

type RiskControlsProps = {
  profile: InvestorProfile;
  rolePrompt: string;
  isRolePromptCustom: boolean;
  onChange: (profile: InvestorProfile) => void;
  onRolePromptChange: (value: string) => void;
  onRolePromptReset: () => void;
  onAnalyze: () => void;
  isBusy: boolean;
  hasBlockingErrors?: boolean;
  blockingMessage?: string | null;
  /**
   * 上一次点击「生成」失败的原因。挨着触发它的按钮展示 —— 生成日报是这一屏的
   * 主操作，失败时按钮只是恢复可用态，没有文字用户无法区分"失败了"和"没反应"。
   */
  errorMessage?: string | null;
  readingModeKey?: string | null;
  /** 兼容旧调用：没有航线数据时，按钮上仍可显示当前阶段。 */
  busyLabel?: string | null;
  scanProgress?: AnalysisScanProgressState | null;
  onCancel?: () => void;
  /** 发现基金正在跑时禁止再开日报，避免两台设备/两个 Tab 叠两条长任务。 */
  peerBusyMessage?: string | null;
};

export function RiskControls({
  profile,
  rolePrompt,
  isRolePromptCustom,
  onChange,
  onRolePromptChange,
  onRolePromptReset,
  onAnalyze,
  isBusy,
  hasBlockingErrors = false,
  blockingMessage = null,
  errorMessage = null,
  readingModeKey = null,
  busyLabel = null,
  scanProgress = null,
  onCancel,
  peerBusyMessage = null,
}: RiskControlsProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [rolePromptOpen, setRolePromptOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(readingModeKey == null);

  useEffect(() => {
    setSettingsOpen(readingModeKey == null);
  }, [readingModeKey]);

  const scanFailed = scanProgress?.status === "failed";
  const showScanTrack = Boolean(scanProgress) && scanProgress?.status !== "completed";
  const busyButtonLabel = scanFailed
    ? "重试生成"
    : isBusy
      ? busyLabel?.trim() || scanProgress?.stageLabel || "正在生成..."
      : null;
  const blockedByPeer = Boolean(peerBusyMessage) && !isBusy && !scanFailed;
  const generateDisabled = (!scanFailed && isBusy) || hasBlockingErrors || blockedByPeer;

  if (readingModeKey && !settingsOpen && !showScanTrack) {
    return (
      <section className="report-control-card section-card min-w-0 overflow-hidden">
        <div className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="text-sm font-black text-[var(--brand-deep)]">本次生成设置</div>
            <p className="mt-1 text-xs text-[var(--muted)]">
              深度分析 · {profileSummary(profile)}
            </p>
            {busyButtonLabel ? (
              <p className="mt-1 text-xs font-semibold text-[var(--brand-strong)]" data-testid="report-generate-stage">
                {busyButtonLabel}
              </p>
            ) : null}
            {hasBlockingErrors && blockingMessage ? (
              <p className="mt-1 text-xs font-semibold text-[var(--danger-fg)]" role="alert">
                {blockingMessage}
              </p>
            ) : errorMessage ? (
              <p className="mt-1 text-xs font-semibold text-[var(--danger-fg)]" role="alert">
                {errorMessage}
              </p>
            ) : blockedByPeer ? (
              <p className="mt-1 text-xs font-semibold text-[var(--brand-strong)]" role="status">
                {peerBusyMessage}
              </p>
            ) : null}
          </div>
          <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto">
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
              disabled={generateDisabled}
              className="btn-primary min-h-11"
            >
              {busyButtonLabel ?? (hasBlockingErrors ? "请先处理严重项" : "重新生成")}
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
            <p className="ink-label">Daily Desk</p>
            <h2 className="font-display text-lg font-extrabold text-[var(--brand-deep)]">生成投研日报</h2>
            <p className="mt-0.5 text-xs text-[var(--muted)]">AI 结合你的持仓与风险偏好，给出说人话的操作建议</p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
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

      {showScanTrack && scanProgress ? (
        <AnalysisScanProgress progress={scanProgress} />
      ) : null}

      <div className="p-4 sm:p-5">
      <div className="overflow-hidden rounded-xl border border-slate-100">
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
          // 折叠态不再解释这个可选高级项的边界 —— 触发器上已经标了「（高级）」
          // 和「未添加 / 已添加」，展开后编辑器里也有完整说明。
          // id 必须保留：触发器的 aria-controls 指向它。
          <span id="report-role-prompt-settings" hidden />
        )}
      </div>

      {hasBlockingErrors && blockingMessage ? (
        <p
          className="mt-3 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-xs font-semibold leading-5 text-[var(--danger-fg)]"
          role="alert"
        >
          {blockingMessage}
        </p>
      ) : errorMessage ? (
        <p
          className="mt-3 rounded-xl border border-[var(--danger-border)] bg-[var(--danger-bg)] px-3 py-2 text-xs font-semibold leading-5 text-[var(--danger-fg)]"
          role="alert"
        >
          {errorMessage}
        </p>
      ) : blockedByPeer ? (
        <p
          className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--brand-soft)] px-3 py-2 text-xs font-semibold leading-5 text-[var(--brand-strong)]"
          role="status"
        >
          {peerBusyMessage}
        </p>
      ) : null}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onAnalyze}
          disabled={generateDisabled}
          data-testid="analyze"
          className="btn-primary min-h-11 w-full !rounded-xl sm:w-auto"
        >
          {scanFailed ? (
            <RotateCcw size={17} />
          ) : isBusy ? (
            <Loader2 size={17} className="animate-spin" />
          ) : (
            <SlidersHorizontal size={17} />
          )}
          {busyButtonLabel ??
            (hasBlockingErrors ? "请先处理严重项" : "生成今日操作建议")}
        </button>
        {isBusy || scanFailed ? (
          <button
            type="button"
            data-testid="analysis-stop-button"
            onClick={onCancel}
            className="btn-ghost min-h-11 w-full !rounded-xl border border-[var(--line)] sm:w-auto"
          >
            {scanFailed ? "关闭" : "停止生成"}
          </button>
        ) : null}
      </div>

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
                  {YUAN_FORMATTER.format(resolveExpectedInvestmentAmount(profile))} 元
                </span>
              </div>
            </label>
            <label className="block rounded-xl border border-slate-100 bg-slate-50/50 p-3 sm:col-span-2">
              <span className="text-[11px] font-bold text-slate-500">
                预计最短持有天数（用于赎回费档位测算）
              </span>
              <div className="mt-2 flex items-center gap-2">
                <input
                  type="range"
                  min={1}
                  max={180}
                  value={profile.hold_days_target ?? 7}
                  onChange={(event) =>
                    onChange({ ...profile, hold_days_target: Number(event.target.value) })
                  }
                  className="w-full accent-[var(--brand)]"
                />
                <span className="w-12 text-right text-xs font-black tabular-nums">
                  {profile.hold_days_target ?? 7} 天
                </span>
              </div>
            </label>
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
