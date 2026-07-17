"use client";

import { useState } from "react";
import { AlertTriangle, ChevronDown, ReceiptText, TrendingDown, TrendingUp } from "lucide-react";
import type {
  AnalysisFactsHoldingRow,
  FactorIcEvidenceStatus,
  Holding,
  ParsedTransaction,
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
import { QuantEvidenceSummary } from "@/components/QuantEvidenceSummary";
import { SectorOpportunityCard } from "@/components/SectorOpportunityCard";
import { FundTradeabilityEvidence } from "@/components/FundTradeabilityEvidence";
import { SingleFundTransactionModal } from "@/components/SingleFundTransactionModal";

type Snapshot = Report["snapshots"][number];

type FundRecommendationCardProps = {
  item: Report["fund_recommendations"][number];
  report: Report;
  recommendationIndex: number;
  defaultExpanded: boolean;
  currentHolding?: Holding;
  onApplyTransaction?: (transaction: ParsedTransaction) => Promise<unknown>;
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
    <p className="mt-2 break-words text-xs leading-5 text-blue-800 [overflow-wrap:anywhere]">
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

function reportIcStatus(report: Report): FactorIcEvidenceStatus | null {
  const facts = report.analysis_facts as {
    factor_scores?: { ic_status?: FactorIcEvidenceStatus };
  } | undefined;
  return facts?.factor_scores?.ic_status ?? null;
}

function FactorIcNotice({ status }: { status: FactorIcEvidenceStatus | null }) {
  if (!status || status.state === "available") {
    return null;
  }
  if (status.state === "stale") {
    return (
      <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
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

function PositionChangeBadge({
  percent,
  basis,
}: {
  percent: number;
  basis?: string;
}) {
  const isAdd = percent > 0;
  const Icon = isAdd ? TrendingUp : TrendingDown;
  const toneClass = isAdd
    ? "border-emerald-200 bg-emerald-50 text-emerald-900"
    : "border-rose-200 bg-rose-50 text-rose-900";
  return (
    <div className={`mt-2 flex items-start gap-2 rounded-xl border px-3 py-2 ${toneClass}`}>
      <Icon size={18} className="mt-0.5 flex-shrink-0" />
      <div className="min-w-0">
        <div className="text-sm font-black">
          建议{isAdd ? "加仓" : "减仓"} {Math.abs(percent).toFixed(0)}%
        </div>
        {basis ? (
          <p className="mt-0.5 break-words text-xs leading-5 opacity-80 [overflow-wrap:anywhere]">
            {translateEvidenceText(basis)}
          </p>
        ) : null}
      </div>
    </div>
  );
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
  currentHolding,
  onApplyTransaction,
}: FundRecommendationCardProps) {
  const [summaryOpen, setSummaryOpen] = useState(defaultExpanded);
  const [whyOpen, setWhyOpen] = useState(false);
  const [professionalOpen, setProfessionalOpen] = useState(false);
  const [transactionOpen, setTransactionOpen] = useState(false);
  const stableIdentity = `${item.fund_code}-${recommendationIndex}`;
  const snapshot = snapshotForRecommendation(recommendationIndex, item, report);
  const holdingFacts = holdingFactsRow(recommendationIndex, item, report);
  const evidence = holdingFacts?.evidence ?? null;
  const sectorOpportunity = holdingFacts?.sector_opportunity ?? null;
  const divergenceBacktest = holdingFacts?.flow_divergence_backtest ?? null;
  const tradeability = item.tradeability ?? holdingFacts?.tradeability;
  const transactionExecution =
    item.transaction_execution ?? holdingFacts?.transaction_execution;
  const hasTradeability = Boolean(
    (tradeability && Object.keys(tradeability).length > 0) ||
      (transactionExecution && Object.keys(transactionExecution).length > 0),
  );
  const isReductionReview = /减仓|清仓/.test(item.action);
  const reviewTargetAmount = transactionExecution?.review_target_amount_yuan ?? null;
  const canRecordReduction = Boolean(
    isReductionReview && currentHolding && onApplyTransaction,
  );
  const icStatus = reportIcStatus(report);

  const primaryReason = selectPrimaryReason(item);
  const primaryReasonKey = exactEvidenceKey(primaryReason);
  const positionChangeBasis =
    exactEvidenceKey(item.suggested_position_change_basis) === primaryReasonKey
      ? undefined
      : item.suggested_position_change_basis;
  const amountDetail = item.amount_note?.trim()
    ? item.amount_note
    : item.amount_yuan != null
      ? `参考金额：约 ${item.amount_yuan.toLocaleString("zh-CN")} 元`
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

  const cardBody = (
    <div className={`min-w-0 overflow-hidden rounded-2xl border border-l-4 border-slate-200 bg-white ${actionAccentClass}`}>
      <button
        type="button"
        onClick={() => setSummaryOpen((value) => !value)}
        aria-expanded={summaryOpen}
        aria-controls={`${stableIdentity}-summary`}
        aria-label={`${summaryOpen ? "收起" : "展开"} ${item.fund_name}`}
        className="flex min-h-11 w-full min-w-0 flex-col gap-2 px-4 py-3 text-left"
      >
        <span className="flex w-full min-w-0 flex-wrap items-center gap-2">
          <strong className="min-w-0 break-words text-sm text-slate-950 [overflow-wrap:anywhere]">
            {item.fund_name}
          </strong>
          <span className="text-xs text-slate-500">{item.fund_code}</span>
          {referenceLabel ? <span className="text-xs text-slate-500">{referenceLabel}</span> : null}
          <span className={`ml-auto max-w-full rounded-full border px-2 py-0.5 text-xs font-bold ${actionBadgeClass(item.action)}`}>
            {item.action}
          </span>
        </span>
        <span className="w-full break-words text-xs leading-5 text-slate-600 [overflow-wrap:anywhere]">
          {primaryReason}
        </span>
      </button>
      {summaryOpen ? (
        <div id={`${stableIdentity}-summary`} className="min-w-0 border-t border-slate-100 px-4 pb-4">
          {item.suggested_position_change_percent != null ? (
            <PositionChangeBadge
              percent={item.suggested_position_change_percent}
              basis={positionChangeBasis}
            />
          ) : visibleAmountDetail ? (
            <p className="mt-3 break-words rounded-xl bg-blue-50 px-3 py-2 text-sm font-bold text-blue-800 [overflow-wrap:anywhere]">
              {visibleAmountDetail}
            </p>
          ) : null}
          {nextPlan ? (
            <p className="mt-3 break-words text-sm leading-6 text-amber-900 [overflow-wrap:anywhere]">
              {nextPlan}
            </p>
          ) : null}
          {item.risks?.[0] ? (
            <p className="mt-3 break-words text-xs leading-5 text-rose-700 [overflow-wrap:anywhere]">
              主要风险：{translateEvidenceText(item.risks[0])}
            </p>
          ) : null}
          {canRecordReduction ? (
            <div className="mt-3 flex flex-col gap-3 rounded-xl border border-orange-200 bg-orange-50 px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-black text-orange-900">目标减仓市值</span>
                  <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-bold text-orange-700">
                    待核对
                  </span>
                </div>
                <div className="mt-1 text-lg font-black tabular-nums text-slate-950">
                  {reviewTargetAmount != null
                    ? `¥${reviewTargetAmount.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`
                    : "金额待规则测算"}
                </div>
                <p className="mt-0.5 text-xs text-orange-900/75">
                  支付宝操作后，回填实际卖出份额即可更新持仓
                </p>
              </div>
              <button
                type="button"
                onClick={() => setTransactionOpen(true)}
                className="inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-black text-white transition hover:bg-slate-800"
              >
                <ReceiptText size={16} />
                核对并记录
              </button>
            </div>
          ) : null}
          <Disclosure
            id={`${stableIdentity}-why`}
            title="为什么这样建议"
            open={whyOpen}
            onToggle={() => setWhyOpen((value) => !value)}
          >
            <ul className="space-y-2 text-sm leading-6 text-slate-700">
              {reasons.map((point) => (
                <li key={point} className="break-words [overflow-wrap:anywhere]">{point}</li>
              ))}
            </ul>
            {bullish.length ? <NewsBlock title="有效利好" tone="positive" items={bullish} /> : null}
            {bearish.length ? <NewsBlock title="有效利空 / 风险" tone="negative" items={bearish} /> : null}
            {item.risks && item.risks.length > 1 ? (
              <ul className="mt-3 space-y-1 text-xs text-rose-700">
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
              <p className="mt-2 text-xs text-amber-800">指标数据异常，已隐藏</p>
            ) : null}
            <FactorIcNotice status={icStatus} />
            {hasTradeability ? (
              <div className="mt-3 space-y-2">
                <FundTradeabilityEvidence
                  tradeability={tradeability}
                  holdingTransactionExecution={transactionExecution}
                  compact
                />
                {isReductionReview &&
                transactionExecution?.reduction_amount_status === "manual_review" ? (
                  <p className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-5 text-amber-900">
                    逐笔持有期未核验，实际操作前请完成赎回条件核对。
                  </p>
                ) : null}
              </div>
            ) : null}
            {sectorOpportunity ? (
              <SectorOpportunityCard item={sectorOpportunity} divergenceBacktest={divergenceBacktest} />
            ) : null}
            {evidence ? (
              <div className="mt-3 rounded-xl border border-slate-200/80 bg-slate-50/70 p-3">
                <div className="mb-2 text-[11px] font-semibold tracking-[0.12em] text-slate-500">量化证据质量</div>
                <QuantEvidenceSummary evidence={evidence} />
              </div>
            ) : null}
            {item.decision_path ? (
              <p className="mt-3 break-words text-sm leading-6 text-blue-950 [overflow-wrap:anywhere]">
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

  const transactionModal = canRecordReduction && currentHolding && onApplyTransaction ? (
    <SingleFundTransactionModal
      open={transactionOpen}
      holding={currentHolding}
      direction="sell"
      latestNav={snapshot?.latest_nav}
      navDateLabel={snapshot?.nav_date}
      reviewTargetAmountYuan={reviewTargetAmount}
      tradeability={tradeability}
      requireRedemptionReview
      onClose={() => setTransactionOpen(false)}
      onSubmit={async (transaction) => {
        await onApplyTransaction(transaction);
      }}
    />
  ) : null;

  if (isExtremeAction(item.action)) {
    return (
      <>
        <ExtremeActionGate action={item.action}>{cardBody}</ExtremeActionGate>
        {transactionModal}
      </>
    );
  }
  return <>{cardBody}{transactionModal}</>;
}
