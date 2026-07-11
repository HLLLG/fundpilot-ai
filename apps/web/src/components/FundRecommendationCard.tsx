"use client";

import { useState } from "react";
import { AlertTriangle, ChevronDown, TrendingDown, TrendingUp } from "lucide-react";
import type { AnalysisFactsHoldingRow, Report } from "@/lib/api";
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
import { SectorOpportunityCard } from "@/components/SectorOpportunityCard";

type Snapshot = Report["snapshots"][number];

type FundRecommendationCardProps = {
  item: Report["fund_recommendations"][number];
  report: Report;
  defaultExpanded: boolean;
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

function navHintForFund(fundCode: string, snapshots: Report["snapshots"]): string | null {
  const snapshot = snapshots.find((item) => item.fund_code === fundCode);
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

function holdingFactsRow(fundCode: string, report: Report): AnalysisFactsHoldingRow | null {
  const facts = report.analysis_facts as { holdings?: AnalysisFactsHoldingRow[] } | undefined;
  return facts?.holdings?.find((holding) => holding.fund_code === fundCode) ?? null;
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
  defaultExpanded,
}: FundRecommendationCardProps) {
  const [summaryOpen, setSummaryOpen] = useState(defaultExpanded);
  const [whyOpen, setWhyOpen] = useState(false);
  const [professionalOpen, setProfessionalOpen] = useState(false);
  const snapshot = report.snapshots.find((entry) => entry.fund_code === item.fund_code);
  const holdingFacts = holdingFactsRow(item.fund_code, report);
  const evidence = holdingFacts?.evidence ?? null;
  const sectorOpportunity = holdingFacts?.sector_opportunity ?? null;
  const divergenceBacktest = holdingFacts?.flow_divergence_backtest ?? null;

  const primaryReason = selectPrimaryReason(item);
  const nextPlan = selectNextTradingPlan(item.points);
  const bullish = meaningfulNewsLines(item.news_bullish);
  const bearish = meaningfulNewsLines(item.news_bearish);
  const reasons = keyReasonLines(item);
  const diagnostic = safeDiagnosticMetrics(snapshot ?? {});
  const referenceLabel = confidenceDisplayLabel(item.confidence);
  const navHint = navHintForFund(item.fund_code, report.snapshots);
  const actionAccentClass = actionAccentClasses[actionTone(item.action)];

  const cardBody = (
    <div className={`min-w-0 overflow-hidden rounded-2xl border border-l-4 border-slate-200 bg-white ${actionAccentClass}`}>
      <button
        type="button"
        onClick={() => setSummaryOpen((value) => !value)}
        aria-expanded={summaryOpen}
        aria-controls={`${item.fund_code}-summary`}
        aria-label={`${summaryOpen ? "收起" : "展开"} ${item.fund_name}`}
        className="flex min-h-11 w-full min-w-0 flex-col gap-2 px-4 py-3 text-left"
      >
        <span className="flex w-full min-w-0 flex-wrap items-center gap-2">
          <strong className="min-w-0 break-words text-sm text-slate-950 [overflow-wrap:anywhere]">
            {item.fund_name}
          </strong>
          <span className="text-xs text-slate-400">{item.fund_code}</span>
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
        <div id={`${item.fund_code}-summary`} className="min-w-0 border-t border-slate-100 px-4 pb-4">
          {item.suggested_position_change_percent != null ? (
            <PositionChangeBadge
              percent={item.suggested_position_change_percent}
              basis={item.suggested_position_change_basis}
            />
          ) : item.amount_note && item.amount_note !== primaryReason ? (
            <p className="mt-3 break-words rounded-xl bg-blue-50 px-3 py-2 text-sm font-bold text-blue-800 [overflow-wrap:anywhere]">
              {item.amount_note}
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
          <Disclosure
            id={`${item.fund_code}-why`}
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
            id={`${item.fund_code}-professional`}
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
            {sectorOpportunity ? (
              <SectorOpportunityCard item={sectorOpportunity} divergenceBacktest={divergenceBacktest} />
            ) : null}
            {evidence ? (
              <p className="mt-3 break-words text-xs leading-5 text-slate-600 [overflow-wrap:anywhere]">
                完整量化证据：{evidence.summary}
              </p>
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

  if (isExtremeAction(item.action)) {
    return <ExtremeActionGate action={item.action}>{cardBody}</ExtremeActionGate>;
  }
  return cardBody;
}
