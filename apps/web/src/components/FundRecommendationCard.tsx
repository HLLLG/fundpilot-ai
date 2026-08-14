"use client";

import { useState } from "react";
import { AlertTriangle, ChevronDown, TrendingDown, TrendingUp } from "lucide-react";
import type {
  AnalysisFactsHoldingRow,
  DecisionEscalation,
  DirectionExit,
  FactorIcEvidenceStatus,
  Report,
} from "@/lib/api";
import { actionBadgeClass, actionTone, isExtremeAction } from "@/lib/actionStyles";
import { translateEvidenceText } from "@/lib/decisionText";
import {
  confidenceDisplayLabel,
  keyReasonLines,
  meaningfulNewsLines,
  safeDiagnosticMetrics,
  selectNextTradingPlan,
  selectPrimaryReason,
} from "@/lib/reportPresentation";
import { DecisionEvidenceGrid } from "@/components/DecisionEvidenceGrid";
import { MethodologyNote } from "@/components/MethodologyNote";
import { QuantEvidenceSummary } from "@/components/QuantEvidenceSummary";
import { SectorOpportunityCard } from "@/components/SectorOpportunityCard";
import type { HoldingIdentity } from "@/lib/holdingMetrics";

// formatter 提到模块作用域：日报按推荐条数逐条渲染金额。无选项的
// `Intl.NumberFormat(locale)` 与 `n.toLocaleString(locale)` 输出一致。
const YUAN_FORMATTER = new Intl.NumberFormat("zh-CN");

type Snapshot = Report["snapshots"][number];

type FundRecommendationCardProps = {
  item: Report["fund_recommendations"][number];
  report: Report;
  recommendationIndex: number;
  defaultExpanded: boolean;
  onOpenHolding?: (holding: HoldingIdentity) => void;
};

const actionAccentClasses = {
  add: "border-l-emerald-400",
  reduce: "border-l-orange-400",
  deep_reduce: "border-l-rose-500",
  clear_all: "border-l-rose-700",
  pause: "border-l-amber-400",
  watch: "border-l-slate-300",
  neutral: "border-l-blue-400",
} as const;

function exactEvidenceKey(value?: string | null): string {
  return value ? translateEvidenceText(value.trim()).trim() : "";
}

function FundDiagnosticHint({ snapshot }: { snapshot: Snapshot }) {
  const hints: string[] = [];
  if (snapshot.fund_type) hints.push(`类型 ${snapshot.fund_type}`);
  if (snapshot.management_fee) hints.push(`管理费 ${snapshot.management_fee}`);
  hints.push(...safeDiagnosticMetrics(snapshot).hints);
  if (!hints.length) {
    return null;
  }
  return (
    <p className="mt-2 break-words text-xs leading-5 text-[var(--info-fg)] [overflow-wrap:anywhere]">
      {hints.join(" · ")}
    </p>
  );
}

function navHintForSnapshot(snapshot: Snapshot | undefined): string | null {
  if (!snapshot) {
    return null;
  }
  if (snapshot.latest_nav != null && snapshot.nav_date) {
    return `最新净值 ${snapshot.latest_nav} · 日期 ${snapshot.nav_date}`;
  }
  if (snapshot.latest_nav != null) {
    return `最新净值 ${snapshot.latest_nav}`;
  }
  if (snapshot.nav_date) {
    return `净值日期 ${snapshot.nav_date}`;
  }
  if (snapshot.note) {
    return snapshot.note;
  }
  return null;
}

function holdingFactsRow(
  recommendationIndex: number,
  item: Report["fund_recommendations"][number],
  report: Report,
): AnalysisFactsHoldingRow | null {
  const facts = report.analysis_facts as { holdings?: AnalysisFactsHoldingRow[] } | undefined;
  const rows = facts?.holdings;
  if (!rows?.length) {
    return null;
  }

  const aligned = rows[recommendationIndex];
  if (aligned) {
    return aligned;
  }

  const matches = rows.filter((holding) => holding.fund_code === item.fund_code);
  return matches.length === 1 ? matches[0] : null;
}

function snapshotForRecommendation(
  recommendationIndex: number,
  item: Report["fund_recommendations"][number],
  report: Report,
): Snapshot | undefined {
  const aligned = report.snapshots[recommendationIndex];
  if (aligned) {
    return aligned;
  }

  const exactMatches = report.snapshots.filter(
    (snapshot) =>
      snapshot.fund_code === item.fund_code && snapshot.fund_name === item.fund_name,
  );
  if (exactMatches.length === 1) {
    return exactMatches[0];
  }

  const codeMatches = report.snapshots.filter(
    (snapshot) => snapshot.fund_code === item.fund_code,
  );
  return codeMatches.length === 1 ? codeMatches[0] : undefined;
}

function holdingForRecommendation(
  recommendationIndex: number,
  item: Report["fund_recommendations"][number],
  report: Report,
) {
  const aligned = report.holdings[recommendationIndex];
  if (aligned?.fund_code === item.fund_code) {
    return aligned;
  }
  const exactMatches = report.holdings.filter(
    (holding) =>
      holding.fund_code === item.fund_code && holding.fund_name === item.fund_name,
  );
  if (exactMatches.length === 1) {
    return exactMatches[0];
  }
  const codeMatches = report.holdings.filter(
    (holding) => holding.fund_code === item.fund_code,
  );
  return codeMatches.length === 1 ? codeMatches[0] : undefined;
}

function reportIcStatus(report: Report): FactorIcEvidenceStatus | null {
  const facts = report.analysis_facts as {
    factor_scores?: { ic_status?: FactorIcEvidenceStatus };
  } | undefined;
  return facts?.factor_scores?.ic_status ?? null;
}

/** 双向守卫是否真正生效由本次运行的模式决定，逐报告冻结在 pipeline 里。 */
function reportEscalationMode(report: Report): string | null {
  const facts = report.analysis_facts as
    | { pipeline?: { decision_escalation_mode?: string } }
    | undefined;
  const mode = facts?.pipeline?.decision_escalation_mode;
  return typeof mode === "string" && mode ? mode : null;
}

function FactorIcNotice({ status }: { status: FactorIcEvidenceStatus | null }) {
  if (!status || status.state === "available") {
    return null;
  }
  if (status.state === "stale") {
    return (
      <div className="mt-3 rounded-xl border border-[var(--warn-border)] bg-[var(--warn-bg)] px-3 py-2 text-xs leading-5 text-[var(--warn-fg)]">
        IC 回测已过期{status.run_date ? `（${status.run_date}）` : ""}，本次已降级为不参与
      </div>
    );
  }
  return (
    <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-700">
      <h4 className="font-bold text-slate-900">量化回测未接入</h4>
      当前建议主要依据持仓风险、行情与新闻；IC 不参与本次结论。
    </div>
  );
}

/**
 * 双向守卫的风险升级判定（`decision_guard_shared.resolve_escalation_floor`）。
 *
 * 这个结果一直算着但前端从不展示。展示时必须区分是否真正生效：生产默认
 * `decision_escalation_mode=shadow`，此时最终动作**不会**被改写，只在校验备注里留
 * 灰度提示。如果不加说明地写出「系统建议减仓」，会和卡头显示的「观察」直接矛盾。
 */
function EscalationEvidence({
  escalation,
  escalationMode,
}: {
  escalation: DecisionEscalation;
  escalationMode: string | null;
}) {
  if (escalation.min_bucket == null || !escalation.min_action_label) {
    return null;
  }
  // 用逐报告落库的守卫模式判定，而不是比较动作文案。`min_bucket` 有值只说明算出了
  // 一个保守下限，不代表它比模型给的动作更保守；而动作文案比较有三种翻错：enforced
  // 下模型本身已更保守、REDUCE 档被 normalize 成「风控复核」而 label 是「减仓评估」、
  // 以及 shadow 下动作恰好相等。
  const enforced = escalationMode === "enforced";
  const reasons = (escalation.reasons ?? []).filter((line) => line.trim());
  return (
    <div
      data-testid="report-escalation-evidence"
      className="mt-3 rounded-xl border border-[var(--warn-border)] bg-[var(--warn-bg)]/60 px-3 py-2.5"
    >
      <div className="flex flex-wrap items-baseline gap-x-2 text-[11px] font-black text-[var(--warn-fg)]">
        风险升级判定
        <span className="font-bold">
          对应更保守动作：{escalation.min_action_label}
        </span>
      </div>
      {reasons.length ? (
        <ul className="mt-1.5 space-y-1 text-xs leading-5 text-[var(--warn-fg)]">
          {reasons.map((reason, index) => (
            <li key={`${reason}-${index}`} className="break-words [overflow-wrap:anywhere]">
              {translateEvidenceText(reason)}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="mt-1.5 text-[11px] leading-4 text-slate-600">
        {enforced
          ? "该判定已参与本次最终动作的收紧。"
          : "升级机制处于观察期，本次最终动作未按该判定收紧，仅作风险提示。"}
      </p>
    </div>
  );
}

const EXIT_STATE_PRESENTATION: Record<
  string,
  { label: string; tone: "danger" | "warn" | "info" | "good" }
> = {
  exit: { label: "方向已失效", tone: "danger" },
  deep_reduce: { label: "方向持续走坏", tone: "danger" },
  reduce: { label: "方向已跌破退出线", tone: "warn" },
  pause_add: { label: "方向转弱，仅维持持有", tone: "warn" },
  hold: { label: "方向仍然有效", tone: "good" },
  unavailable: { label: "方向信号不可得", tone: "info" },
};

const EXIT_TONE_CLASS: Record<string, string> = {
  danger: "border-[var(--danger-border)] bg-[var(--danger-bg)]/60 text-[var(--danger-fg)]",
  warn: "border-[var(--warn-border)] bg-[var(--warn-bg)]/60 text-[var(--warn-fg)]",
  info: "border-[var(--info-border)] bg-[var(--info-bg)]/60 text-[var(--info-fg)]",
  good: "border-[var(--success-border)] bg-[var(--success-bg)]/50 text-[var(--success-fg)]",
};

/** 已持仓方向的退出判定。
 *
 * 这块补的是整套方向成熟度长期缺失的一半：原来只有「什么时候进」，没有「什么时候走」，
 * 于是浮盈会因为犹豫不决被拿回去。`hold` 不渲染——方向正常时不需要占版面。
 *
 * 刻意**不**在这里写动作档位和减仓比例：只要退出判定产出了档位，它就已经合并进
 * `resolve_escalation_floor`，由上方「风险升级判定」统一给出动作与是否已生效。这块只回答
 * 「方向为什么走坏」，两块因此各说一件事，不会同一句理由印两遍、也不会出现两个动作口径。
 */
function DirectionExitEvidence({
  exit,
  escalationReasons,
}: {
  exit: DirectionExit;
  escalationReasons: string[];
}) {
  const state = String(exit.exit_state || "");
  if (state === "hold") {
    return null;
  }
  const presentation = EXIT_STATE_PRESENTATION[state] ?? {
    label: "方向状态待确认",
    tone: "info" as const,
  };
  // 风险升级判定合并了退出理由，同一句话不重复渲染。
  const shown = new Set(escalationReasons.map((line) => line.trim()).filter(Boolean));
  const reasons = (exit.reasons ?? [])
    .map((line) => line.trim())
    .filter((line) => line && !shown.has(line));
  const triggers = (exit.triggers ?? []).filter((line) => line.trim());
  const entry = exit.entry_reference;
  const relative = exit.basis === "relative_to_entry";

  return (
    <div
      data-testid="report-direction-exit"
      className={`mt-3 rounded-xl border px-3 py-2.5 ${EXIT_TONE_CLASS[presentation.tone]}`}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 text-[11px] font-black">
        方向退出判定 · {presentation.label}
      </div>

      {reasons.length ? (
        <ul className="mt-1.5 space-y-1 text-xs leading-5">
          {reasons.map((reason, index) => (
            <li key={`${reason}-${index}`} className="break-words [overflow-wrap:anywhere]">
              {reason}
            </li>
          ))}
        </ul>
      ) : null}

      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] leading-4">
        {exit.trend_strength != null && exit.exit_trend_threshold != null ? (
          <div className="flex items-baseline gap-1">
            <dt className="opacity-80">趋势/退出线</dt>
            <dd className="font-mono font-bold tabular-nums">
              {exit.trend_strength.toFixed(1)} / {exit.exit_trend_threshold}
            </dd>
          </div>
        ) : null}
        {exit.consecutive_days_below_exit_line ? (
          <div className="flex items-baseline gap-1">
            <dt className="opacity-80">连续跌破</dt>
            <dd className="font-mono font-bold tabular-nums">
              {exit.consecutive_days_below_exit_line} 个交易日
            </dd>
          </div>
        ) : null}
        {relative && entry?.entry_trend != null ? (
          <div className="flex items-baseline gap-1">
            <dt className="opacity-80">买入时趋势</dt>
            <dd className="font-mono font-bold tabular-nums">
              {entry.entry_trend.toFixed(1)}
              {entry.entry_date ? `（${entry.entry_date}）` : ""}
            </dd>
          </div>
        ) : null}
        {exit.allows_add === false ? (
          <div className="flex items-baseline gap-1">
            <dt className="opacity-80">加仓资格</dt>
            <dd className="font-bold">本轮不加仓</dd>
          </div>
        ) : null}
      </dl>

      {triggers.length ? (
        <p className="mt-1.5 text-[11px] leading-4 opacity-90">
          恢复条件：{triggers.join("；")}
        </p>
      ) : null}

      <p className="mt-1.5 text-[11px] leading-4 text-slate-600">
        {relative
          ? "该判定对齐了买入当时的方向条件。"
          : "该持仓没有对应的发现基金买入记录（多为截图导入），只能按绝对退出线判定。"}
        {exit.thresholds_validated === false
          ? "连续跌破天数与相对回落门槛尚未经过历史回测，可作动作依据但不代表历史胜率。"
          : null}
      </p>
    </div>
  );
}

function PositionChangeBadge({
  percent,
  estimatedAmountYuan,
}: {
  percent: number;
  estimatedAmountYuan?: number | null;
}) {
  const isAdd = percent > 0;
  const Icon = isAdd ? TrendingUp : TrendingDown;
  const toneClass = isAdd
    ? "border-emerald-200 bg-emerald-50 text-emerald-900"
    : "border-rose-200 bg-rose-50 text-rose-900";
  const displayPercent = formatAdjustmentPercent(percent);
  return (
    <div className={`mt-2 flex items-start gap-2 rounded-xl border px-3 py-2 ${toneClass}`}>
      <Icon size={18} className="mt-0.5 flex-shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <div className="text-sm font-black">
            建议{isAdd ? "加仓" : "减仓"}当前持仓的 {displayPercent}%
          </div>
          {estimatedAmountYuan != null && estimatedAmountYuan > 0 ? (
            <div className="whitespace-nowrap text-base font-black tabular-nums">
              约 ¥{YUAN_FORMATTER.format(Math.round(estimatedAmountYuan))}
            </div>
          ) : null}
        </div>
        {estimatedAmountYuan != null && estimatedAmountYuan > 0 ? (
          // 折算口径不该挤在金额下面 —— 用户先要的是"减多少钱"。
          <MethodologyNote label="金额口径" className="mt-1">
            按报告生成时持仓估值折算。
          </MethodologyNote>
        ) : null}
      </div>
    </div>
  );
}

function formatAdjustmentPercent(percent: number) {
  const value = Math.abs(percent);
  return Number.isInteger(value) ? value.toFixed(0) : value.toFixed(1);
}

function ExtremeActionGate({
  action,
  children,
}: {
  action: string;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  if (expanded) {
    return <>{children}</>;
  }
  return (
    <button
      type="button"
      onClick={() => setExpanded(true)}
      className="flex w-full items-center gap-2 rounded-xl border-2 border-dashed border-rose-300 bg-rose-50 px-3 py-3 text-left transition hover:bg-rose-100"
      data-testid="extreme-action-gate"
    >
      <AlertTriangle size={20} className="flex-shrink-0 text-rose-600" />
      <span className="min-w-0 break-words text-sm font-black text-rose-900 [overflow-wrap:anywhere]">
        系统建议「{action}」，点击查看完整依据
      </span>
    </button>
  );
}

function Disclosure({
  id,
  title,
  open,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-3 border-t border-slate-100 pt-3">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        aria-controls={id}
        className="flex min-h-11 w-full items-center justify-between gap-3 text-left text-sm font-black text-slate-800"
      >
        {title}
        <ChevronDown
          size={16}
          aria-hidden="true"
          className={`flex-shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open ? <div id={id} className="min-w-0 pt-3">{children}</div> : null}
    </div>
  );
}

function NewsBlock({
  title,
  tone,
  items,
}: {
  title: string;
  tone: "positive" | "negative";
  items: string[];
}) {
  const classes = tone === "positive"
    ? "bg-emerald-50 text-emerald-900"
    : "bg-rose-50 text-rose-900";
  return (
    <div className={`mt-3 rounded-xl px-3 py-2 ${classes}`}>
      <div className="text-xs font-black">{title}</div>
      <ul className="mt-1 space-y-1 text-xs leading-5">
        {items.map((item) => (
          <li key={item} className="break-words [overflow-wrap:anywhere]">{item}</li>
        ))}
      </ul>
    </div>
  );
}

export function FundRecommendationCard({
  item,
  report,
  recommendationIndex,
  defaultExpanded,
  onOpenHolding,
}: FundRecommendationCardProps) {
  const [summaryOpen, setSummaryOpen] = useState(defaultExpanded);
  const [whyOpen, setWhyOpen] = useState(false);
  const [professionalOpen, setProfessionalOpen] = useState(false);
  const stableIdentity = `${item.fund_code}-${recommendationIndex}`;
  const snapshot = snapshotForRecommendation(recommendationIndex, item, report);
  const reportHolding = holdingForRecommendation(recommendationIndex, item, report);
  const holdingFacts = holdingFactsRow(recommendationIndex, item, report);
  const evidence = holdingFacts?.evidence ?? null;
  const sectorOpportunity = holdingFacts?.sector_opportunity ?? null;
  const divergenceBacktest = holdingFacts?.flow_divergence_backtest ?? null;
  const escalation = holdingFacts?.escalation ?? null;
  const directionExit = holdingFacts?.direction_exit ?? null;
  //: 只留 `transaction_execution`：申购/赎回状态徽章已移除，但减仓侧仍要读
  //: `reduction_amount_status` 来解释"为什么没有减仓金额"。
  const transactionExecution =
    item.transaction_execution ?? holdingFacts?.transaction_execution;
  const isReductionReview = /减仓|清仓/.test(item.action);
  const icStatus = reportIcStatus(report);

  const primaryReason = selectPrimaryReason(item);
  const primaryReasonKey = exactEvidenceKey(primaryReason);
  const positionChangeBasis =
    exactEvidenceKey(item.suggested_position_change_basis) === primaryReasonKey
      ? undefined
      : item.suggested_position_change_basis;
  const derivedAdjustmentAmount =
    item.suggested_position_change_percent != null &&
    reportHolding != null &&
    Number.isFinite(reportHolding.holding_amount) &&
    reportHolding.holding_amount > 0
      ? Math.round(
          reportHolding.holding_amount *
            Math.abs(item.suggested_position_change_percent),
        ) / 100
      : null;
  const serverEstimatedAdjustmentAmount =
    item.estimated_position_change_amount_yuan != null &&
    Number.isFinite(item.estimated_position_change_amount_yuan) &&
    item.estimated_position_change_amount_yuan > 0
      ? item.estimated_position_change_amount_yuan
      : null;
  const estimatedAdjustmentAmount =
    serverEstimatedAdjustmentAmount ?? derivedAdjustmentAmount;
  const amountDetail = item.amount_note?.trim()
    ? item.amount_note
    : item.amount_yuan != null
      ? `参考金额：约 ${YUAN_FORMATTER.format(item.amount_yuan)} 元`
      : null;
  const visibleAmountDetail = exactEvidenceKey(amountDetail) === primaryReasonKey
    ? null
    : amountDetail;
  const nextPlanCandidate = selectNextTradingPlan(item.points);
  const nextPlan = exactEvidenceKey(nextPlanCandidate) === primaryReasonKey
    ? null
    : nextPlanCandidate;
  const bullish = meaningfulNewsLines(item.news_bullish);
  const bearish = meaningfulNewsLines(item.news_bearish);
  const newsKeys = new Set(
    [...bullish, ...bearish].map(exactEvidenceKey).filter(Boolean),
  );
  const reasons = keyReasonLines(item).filter(
    (reason) => {
      const key = exactEvidenceKey(reason);
      return key !== primaryReasonKey && !newsKeys.has(key);
    },
  );
  const diagnostic = safeDiagnosticMetrics(snapshot ?? {});
  const referenceLabel = confidenceDisplayLabel(item.confidence);
  const navHint = navHintForSnapshot(snapshot);
  const actionAccentClass = actionAccentClasses[actionTone(item.action)];
  const actionBadge = (
    <span className={`ml-auto max-w-full rounded-full border px-2 py-0.5 text-xs font-bold ${actionBadgeClass(item.action)}`}>
      {item.action}
      {item.suggested_position_change_percent != null ? (
        <span>
          {" · "}
          {item.suggested_position_change_percent > 0 ? "+" : "−"}
          {formatAdjustmentPercent(item.suggested_position_change_percent)}%
        </span>
      ) : null}
    </span>
  );
  const expandLabel = `${summaryOpen ? "收起" : "展开"} ${item.fund_name}`;

  const cardHeader = onOpenHolding ? (
    <div className="flex min-h-11 w-full min-w-0 flex-col gap-2 px-4 py-3 text-left">
      <span className="flex w-full min-w-0 flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() =>
            onOpenHolding({
              fund_code: item.fund_code,
              fund_name: item.fund_name,
            })
          }
          aria-label={`查看 ${item.fund_name} 详情`}
          className="min-w-0 rounded-lg text-left transition hover:text-[var(--brand-strong)]"
        >
          <strong className="min-w-0 break-words text-sm text-slate-950 [overflow-wrap:anywhere]">
            {item.fund_name}
          </strong>
          <span className="ml-2 text-xs text-slate-500">{item.fund_code}</span>
          <span className="mt-1 block text-[11px] font-medium text-[var(--brand)]">
            查看基金详情 →
          </span>
        </button>
        {referenceLabel ? <span className="text-xs text-slate-500">{referenceLabel}</span> : null}
        {actionBadge}
        <button
          type="button"
          onClick={() => setSummaryOpen((value) => !value)}
          aria-expanded={summaryOpen}
          aria-controls={`${stableIdentity}-summary`}
          aria-label={expandLabel}
          className="flex size-9 shrink-0 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-50"
        >
          <ChevronDown className={`size-4 transition ${summaryOpen ? "rotate-180" : ""}`} />
        </button>
      </span>
      <span className="w-full break-words text-xs leading-5 text-slate-600 [overflow-wrap:anywhere]">
        {primaryReason}
      </span>
    </div>
  ) : (
    <button
      type="button"
      onClick={() => setSummaryOpen((value) => !value)}
      aria-expanded={summaryOpen}
      aria-controls={`${stableIdentity}-summary`}
      aria-label={expandLabel}
      className="flex min-h-11 w-full min-w-0 flex-col gap-2 px-4 py-3 text-left"
    >
      <span className="flex w-full min-w-0 flex-wrap items-center gap-2">
        <strong className="min-w-0 break-words text-sm text-slate-950 [overflow-wrap:anywhere]">
          {item.fund_name}
        </strong>
        <span className="text-xs text-slate-500">{item.fund_code}</span>
        {referenceLabel ? <span className="text-xs text-slate-500">{referenceLabel}</span> : null}
        {actionBadge}
      </span>
      <span className="w-full break-words text-xs leading-5 text-slate-600 [overflow-wrap:anywhere]">
        {primaryReason}
      </span>
    </button>
  );

  const cardBody = (
    <div className={`min-w-0 overflow-hidden rounded-2xl border border-l-4 border-slate-200 bg-white ${actionAccentClass}`}>
      {cardHeader}
      {summaryOpen ? (
        <div id={`${stableIdentity}-summary`} className="min-w-0 border-t border-slate-100 px-4 pb-4">
          {item.suggested_position_change_percent != null ? (
            <PositionChangeBadge
              percent={item.suggested_position_change_percent}
              estimatedAmountYuan={estimatedAdjustmentAmount}
            />
          ) : visibleAmountDetail ? (
            <p className="mt-3 break-words rounded-xl bg-[var(--info-bg)] px-3 py-2 text-sm font-bold text-[var(--info-fg)] [overflow-wrap:anywhere]">
              {visibleAmountDetail}
            </p>
          ) : null}
          {nextPlan ? (
            <p className="mt-3 break-words text-sm leading-6 text-[var(--warn-fg)] [overflow-wrap:anywhere]">
              {nextPlan}
            </p>
          ) : null}
          {item.risks?.[0] ? (
            <p className="mt-3 break-words text-xs leading-5 text-[var(--danger-fg)] [overflow-wrap:anywhere]">
              主要风险：{translateEvidenceText(item.risks[0])}
            </p>
          ) : null}
          <Disclosure
            id={`${stableIdentity}-why`}
            title="为什么这样建议"
            open={whyOpen}
            onToggle={() => setWhyOpen((value) => !value)}
          >
            <ul className="space-y-2 text-sm leading-6 text-slate-700">
              {/* 仓位比例的依据原本紧贴在金额下面，和上方的核心理由并列成第二段
                  说明文字。它属于"为什么"，放到这里首位更合适。 */}
              {positionChangeBasis ? (
                <li className="break-words [overflow-wrap:anywhere]">
                  {translateEvidenceText(positionChangeBasis)}
                </li>
              ) : null}
              {reasons.map((point) => (
                <li key={point} className="break-words [overflow-wrap:anywhere]">{point}</li>
              ))}
            </ul>
            {bullish.length ? <NewsBlock title="有效利好" tone="positive" items={bullish} /> : null}
            {bearish.length ? <NewsBlock title="有效利空 / 风险" tone="negative" items={bearish} /> : null}
            {item.risks && item.risks.length > 1 ? (
              <ul className="mt-3 space-y-1 text-xs text-[var(--danger-fg)]">
                {item.risks.slice(1).map((risk) => (
                  <li key={risk} className="break-words [overflow-wrap:anywhere]">
                    {translateEvidenceText(risk)}
                  </li>
                ))}
              </ul>
            ) : null}
          </Disclosure>
          <Disclosure
            id={`${stableIdentity}-professional`}
            title="专业依据"
            open={professionalOpen}
            onToggle={() => setProfessionalOpen((value) => !value)}
          >
            {navHint ? (
              <p className="break-words text-xs leading-5 text-slate-500 [overflow-wrap:anywhere]">{navHint}</p>
            ) : null}
            {snapshot ? <FundDiagnosticHint snapshot={snapshot} /> : null}
            {diagnostic.invalid ? (
              <p className="mt-2 text-xs text-[var(--warn-fg)]">指标数据异常，已隐藏</p>
            ) : null}
            <FactorIcNotice status={icStatus} />
            {/* 申购/赎回状态徽章块（申购开放 · 赎回开放 · 首次起购 · 单日限额 · 来源…）
                已按产品要求移除：「能不能买」由用户自行在支付宝确认，日报不再复述一份可能
                过期的副本。

                这里刻意**保留**减仓侧的人工复核提示——它回答的不是"能不能买"，而是
                "这条减仓指令会不会踩到锁定期与赎回费"。没有逐笔申购时间时系统本就不自动
                生成减仓金额，这句话是那个行为的唯一出口，去掉会让用户看到一个减仓比例却
                不知道它为什么没有金额。 */}
            {isReductionReview &&
            transactionExecution?.reduction_amount_status === "manual_review" ? (
              <p className="mt-3 rounded-lg border border-[var(--warn-border)] bg-[var(--warn-bg)] px-2.5 py-2 text-[11px] leading-5 text-[var(--warn-fg)]">
                逐笔申购时间未核验：减仓前需人工确认锁定期与适用赎回费，系统不自动生成减仓金额。
              </p>
            ) : null}
            {sectorOpportunity ? (
              <SectorOpportunityCard item={sectorOpportunity} divergenceBacktest={divergenceBacktest} />
            ) : null}
            {directionExit ? (
              <DirectionExitEvidence
                exit={directionExit}
                escalationReasons={escalation?.reasons ?? []}
              />
            ) : null}
            {escalation ? (
              <EscalationEvidence
                escalation={escalation}
                escalationMode={reportEscalationMode(report)}
              />
            ) : null}
            {evidence ? (
              <div className="mt-3 rounded-xl border border-slate-200/80 bg-slate-50/70 p-3">
                <div className="mb-2 text-[11px] font-semibold tracking-[0.12em] text-slate-500">量化证据质量</div>
                <QuantEvidenceSummary evidence={evidence} />
              </div>
            ) : null}
            {item.decision_path ? (
              <p className="mt-3 break-words text-sm leading-6 text-[var(--info-fg)] [overflow-wrap:anywhere]">
                {translateEvidenceText(item.decision_path)}
              </p>
            ) : null}
            <DecisionEvidenceGrid
              className="mt-3"
              sectorEvidence={item.sector_evidence}
              fundEvidence={item.fund_evidence}
              validationNotes={item.validation_notes}
            />
          </Disclosure>
        </div>
      ) : null}
    </div>
  );

  if (isExtremeAction(item.action)) {
    return <ExtremeActionGate action={item.action}>{cardBody}</ExtremeActionGate>;
  }
  return cardBody;
}
