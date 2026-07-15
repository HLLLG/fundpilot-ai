"use client";

import { useId } from "react";
import {
  CircleHelp,
  Clock3,
  Database,
  Fingerprint,
  Layers3,
} from "lucide-react";

import type {
  FundLookthroughCandidate,
  FundLookthroughExposure,
  FundLookthroughResearch,
  FundLookthroughSnapshot,
} from "@/lib/api";

type FundLookthroughEvidenceProps = {
  research?: FundLookthroughResearch | null;
  candidateNames?: Record<string, string | undefined>;
  context?: "daily" | "discovery";
};

type ExposureGroupProps = {
  title: string;
  rows: FundLookthroughExposure[];
  labelKeys: Array<keyof FundLookthroughExposure>;
  unknownMass?: number | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }
  return null;
}

function textValue(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return null;
}

function normalizeFundCode(value: string | null): string | null {
  if (!value) return null;
  return /^\d{1,6}$/.test(value) ? value.padStart(6, "0") : value;
}

function formatPercent(value: number): string {
  return new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(Math.abs(value) < 0.005 ? 0 : value);
}

function formatMoment(value: string | null): string | null {
  if (!value) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(parsed);
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}`;
}

function exposureRows(value: unknown): FundLookthroughExposure[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord) as FundLookthroughExposure[];
}

function normalizeCandidates(
  value: FundLookthroughResearch["candidates"],
): FundLookthroughCandidate[] {
  if (Array.isArray(value)) {
    return value.filter(isRecord) as FundLookthroughCandidate[];
  }
  if (!isRecord(value)) return [];
  return Object.entries(value).flatMap(([fundCode, candidate]) => {
    if (!isRecord(candidate)) return [];
    return [
      {
        ...candidate,
        fund_code: textValue(candidate.fund_code) ?? fundCode,
      } as FundLookthroughCandidate,
    ];
  });
}

function scopeLabel(research: FundLookthroughResearch): string {
  const portfolioScope = textValue(research.portfolio?.scope);
  const rawScope =
    portfolioScope ??
    textValue(research.scope) ??
    (isRecord(research.scope)
      ? textValue(research.scope.kind, research.scope.scope_kind, research.scope.name)
      : null);
  if (rawScope === "whole_account") return "全账户口径";
  if (rawScope === "fund_holdings_only") return "基金持仓口径";
  if (rawScope === "portfolio_only") return "仅当前组合";
  if (rawScope === "portfolio_and_candidates") return "当前组合 + 候选";
  return rawScope ?? "披露持仓口径";
}

function statusMeta(status: string | null) {
  if (status === "qualified" || status === "complete") {
    return {
      label: "披露证据已核验",
      className: "border-emerald-300/40 bg-emerald-300/15 text-emerald-50",
      message: "仅陈列可核验的披露下限，并保留未披露质量。",
    };
  }
  if (status === "partial") {
    return {
      label: "部分披露",
      className: "border-amber-200/40 bg-amber-200/15 text-amber-50",
      message: "证据不完整，仅展示当前可核验的披露下限。",
    };
  }
  if (status === "unavailable" || status === "invalid") {
    return {
      label: "资料暂不可用",
      className: "border-slate-300/35 bg-white/10 text-slate-100",
      message: "穿透资料暂不可用；未知质量保持未知，不记为 0。",
    };
  }
  return {
    label: "研究证据",
    className: "border-cyan-200/30 bg-cyan-200/10 text-cyan-50",
    message: "当前结果按披露范围解释，不外推完整组合。",
  };
}

function snapshotDates(
  research: FundLookthroughResearch,
  candidates: FundLookthroughCandidate[],
) {
  const existingSnapshots = Array.isArray(research.existing_funds)
    ? research.existing_funds.map((fund) => fund?.snapshot)
    : [];
  const snapshots = [
    research.portfolio?.snapshot,
    ...existingSnapshots,
    ...candidates.map((candidate) => candidate.snapshot),
  ].filter(Boolean) as FundLookthroughSnapshot[];
  const uniqueValues = (values: Array<string | null>) =>
    [...new Set(values.filter((value): value is string => value != null))].sort();
  const reportPeriods = uniqueValues(
    snapshots.map((item) => textValue(item.report_period)),
  );
  const asOfDates = uniqueValues(snapshots.map((item) => textValue(item.as_of_date)));
  const availableTimes = uniqueValues(
    snapshots.map((item) => textValue(item.available_at, item.checked_at)),
  );
  const reportPeriod =
    reportPeriods.length > 1
      ? "多报告期 · 最新披露拼图"
      : reportPeriods[0] ?? null;
  const asOfDate = asOfDates.length > 1 ? "多时点" : asOfDates[0] ?? null;
  const availableAt = availableTimes.slice(-1)[0] ?? null;
  return { reportPeriod, asOfDate, availableAt };
}

function metricValue(value: number | null, lowerBound = false): string {
  if (value == null) return "待核验";
  return `${lowerBound ? "≥ " : ""}${formatPercent(value)}%`;
}

function ExposureGroup({ title, rows, labelKeys, unknownMass }: ExposureGroupProps) {
  const normalized = rows
    .map((row) => ({
      row,
      label: textValue(...labelKeys.map((key) => row[key])),
      value: finiteNumber(row.exposure_lower_bound_percent, row.weight_lower_bound_percent),
    }))
    .filter(
      (item): item is { row: FundLookthroughExposure; label: string; value: number } =>
        item.label != null && item.value != null,
    )
    .slice(0, 5);
  if (!normalized.length) return null;

  return (
    <section className="rounded-xl border border-slate-200/80 bg-white/85 p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-1">
        <h4 className="text-[11px] font-black tracking-wide text-slate-700">{title}</h4>
        {unknownMass != null ? (
          <span className="text-[9px] font-semibold text-amber-700">
            未知质量 {formatPercent(unknownMass)}%
          </span>
        ) : null}
      </div>
      <ol className="mt-2 space-y-2">
        {normalized.map(({ row, label, value }, index) => (
          <li key={`${label}-${index}`} className="min-w-0">
            <div className="flex min-w-0 items-baseline justify-between gap-2 text-xs">
              <span className="truncate font-semibold text-slate-700" title={label}>
                {label}
              </span>
              <span className="shrink-0 font-mono font-black tabular-nums text-[var(--brand-strong)]">
                ≥ {formatPercent(value)}%
              </span>
            </div>
            <div aria-hidden="true" className="mt-1 h-1 overflow-hidden rounded-full bg-slate-100">
              <span
                className="block h-full rounded-full bg-gradient-to-r from-cyan-700 to-emerald-500"
                style={{ width: `${Math.min(Math.max(value, 1.5), 100)}%` }}
              />
            </div>
            {textValue(row.security_key) && textValue(row.security_key) !== label ? (
              <p className="mt-0.5 truncate font-mono text-[9px] text-slate-400">
                {textValue(row.security_key)}
              </p>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function candidateTitle(
  candidate: FundLookthroughCandidate,
  candidateNames: Record<string, string | undefined>,
) {
  const rawCode = textValue(candidate.fund_code);
  const code = normalizeFundCode(rawCode);
  const mappedName = code
    ? candidateNames[code] ?? (rawCode ? candidateNames[rawCode] : undefined)
    : undefined;
  return {
    code,
    name: textValue(candidate.fund_name, mappedName) ?? "候选基金",
  };
}

const CROSS_VINTAGE_INTERPRETATIONS = new Set([
  "cross_vintage_disclosed_similarity",
  "cross_vintage_descriptive_similarity",
  "cross_vintage_no_common_in_disclosed_scope",
  "cross_vintage_identity_evidence_insufficient",
]);

function vintageAlignmentStatus(candidate: FundLookthroughCandidate): string | null {
  return textValue(candidate.vintage_alignment?.status);
}

function isCrossVintageCandidate(candidate: FundLookthroughCandidate): boolean {
  const status = vintageAlignmentStatus(candidate);
  if (status === "cross_vintage" || status === "mixed") return true;
  return CROSS_VINTAGE_INTERPRETATIONS.has(
    textValue(candidate.portfolio_overlap_interpretation) ?? "",
  );
}

function isPositiveSameVintageCandidate(candidate: FundLookthroughCandidate): boolean {
  const status = vintageAlignmentStatus(candidate);
  const sameVintage = status === "same_as_of_date" || candidate.vintage_aligned === true;
  return (
    sameVintage &&
    !isCrossVintageCandidate(candidate) &&
    textValue(candidate.portfolio_overlap_interpretation) ===
      "positive_disclosed_overlap_lower_bound"
  );
}

function overlapMessage(candidate: FundLookthroughCandidate): string {
  const interpretation = textValue(candidate.portfolio_overlap_interpretation);
  if (isCrossVintageCandidate(candidate)) {
    return "报告期不一致，仅作跨期披露相似度，不是当前重合下界";
  }
  if (
    interpretation === "no_common_in_disclosed_scope" ||
    interpretation === "no_common"
  ) {
    return "披露范围内未发现共同证券，完整组合重合未知";
  }
  if (interpretation === "identity_evidence_insufficient") {
    return "证券身份披露不足，完整组合重合未知";
  }
  if (interpretation === "snapshot_not_eligible") {
    return "持仓快照未通过时点校验，完整组合重合未知";
  }
  const lowerBound = finiteNumber(
    candidate.portfolio_security_overlap_lower_bound_percent,
    candidate.portfolio_security_overlap_lower_bound,
  );
  if (isPositiveSameVintageCandidate(candidate) && lowerBound != null && lowerBound > 0) {
    return `已披露重合下限 ≥ ${formatPercent(lowerBound)}%`;
  }
  return "披露重合证据不足，完整组合重合未知";
}

function commonSecurities(candidate: FundLookthroughCandidate) {
  return exposureRows(candidate.top_common_with_portfolio ?? candidate.top_common_securities)
    .map((row) => ({
      label: textValue(row.security_name, row.security_key, row.security_code, row.label),
      value: finiteNumber(row.overlap_contribution_lower_bound_percent),
    }))
    .filter(
      (item): item is { label: string; value: number | null } => item.label != null,
    )
    .slice(0, 5);
}

function CandidateEvidence({
  candidate,
  candidateNames,
}: {
  candidate: FundLookthroughCandidate;
  candidateNames: Record<string, string | undefined>;
}) {
  const { code, name } = candidateTitle(candidate, candidateNames);
  const interpretation = textValue(candidate.portfolio_overlap_interpretation);
  const noCommon =
    interpretation === "no_common_in_disclosed_scope" || interpretation === "no_common";
  const crossVintage = isCrossVintageCandidate(candidate);
  const positiveSameVintage = isPositiveSameVintageCandidate(candidate);
  const maxExisting = finiteNumber(
    candidate.max_existing_fund_overlap_lower_bound_percent,
    candidate.max_existing_fund_overlap_lower_bound,
  );
  const common = commonSecurities(candidate);
  const snapshot = candidate.snapshot;
  const reportPeriod = formatMoment(textValue(snapshot?.report_period));
  const asOfDate = formatMoment(textValue(snapshot?.as_of_date));
  const availableAt = formatMoment(textValue(snapshot?.available_at, snapshot?.checked_at));
  const unavailable = candidate.status === "unavailable";

  return (
    <article
      aria-label={`${name}持仓重合证据`}
      className="overflow-hidden rounded-xl border border-slate-200 bg-white"
    >
      <header className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-100 px-3 py-2.5">
        <div className="min-w-0">
          <h5 className="truncate text-sm font-black text-slate-900">{name}</h5>
          {code ? <p className="mt-0.5 font-mono text-[10px] text-slate-500">{code}</p> : null}
        </div>
        <span
          className={`rounded-full border px-2 py-1 text-[10px] font-black ${
            unavailable
              ? "border-slate-200 bg-slate-100 text-slate-600"
              : crossVintage
                ? "border-violet-200 bg-violet-50 text-violet-900"
                : noCommon
                  ? "border-cyan-200 bg-cyan-50 text-cyan-900"
                  : "border-emerald-200 bg-emerald-50 text-emerald-800"
          }`}
        >
          {unavailable ? "快照待补" : crossVintage ? "跨期描述" : "披露范围"}
        </span>
      </header>

      <div className="px-3 py-3">
        <p className="text-sm font-bold leading-6 text-slate-800">{overlapMessage(candidate)}</p>
        {positiveSameVintage && maxExisting != null && maxExisting > 0 ? (
          <p className="mt-1 text-[11px] leading-5 text-slate-500">
            与单只现有基金的最高披露重合下限 ≥ {formatPercent(maxExisting)}%
          </p>
        ) : null}

        {common.length ? (
          <div className="mt-2.5 rounded-lg bg-slate-50 px-2.5 py-2">
            <p className="text-[10px] font-black tracking-wide text-slate-500">
              {crossVintage ? "跨期共同披露证券 · 仅作相似度" : "共同证券 · 披露范围"}
            </p>
            <ul className="mt-1.5 space-y-1 text-[11px] text-slate-700">
              {common.map((item, index) => (
                <li key={`${item.label}-${index}`} className="flex justify-between gap-2">
                  <span className="min-w-0 flex-1 truncate">{item.label}</span>
                  {item.value != null && positiveSameVintage ? (
                    <span className="shrink-0 font-mono font-bold tabular-nums">
                      贡献下限 ≥ {formatPercent(item.value)}%
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {reportPeriod || asOfDate || availableAt ? (
          <p className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1 text-[10px] leading-4 text-slate-500">
            {reportPeriod ? <span>报告期 {reportPeriod}</span> : null}
            {asOfDate ? <span>数据截至 {asOfDate}</span> : null}
            {availableAt ? <span>时点 {availableAt}</span> : null}
          </p>
        ) : null}
      </div>
    </article>
  );
}

export function FundLookthroughEvidence({
  research,
  candidateNames = {},
  context = "daily",
}: FundLookthroughEvidenceProps) {
  const headingId = useId();
  if (!research || !isRecord(research)) return null;

  const portfolio = isRecord(research.portfolio) ? research.portfolio : null;
  const candidates = normalizeCandidates(research.candidates);
  const status = textValue(research.status);
  const statusPresentation = statusMeta(status);
  const knownMass = finiteNumber(
    portfolio?.identity_known_security_mass_lower_bound_percent,
  );
  const disclosedMass = finiteNumber(
    portfolio?.disclosed_security_mass_lower_bound_percent,
  );
  const unknownAccount = finiteNumber(portfolio?.unknown_account_mass_percent);
  const unknownFundScope = finiteNumber(
    portfolio?.unknown_fund_holdings_scope_mass_percent,
  );
  const industryUnknown = finiteNumber(portfolio?.industry_unknown_mass_percent);
  const marketUnknown = finiteNumber(portfolio?.listing_market_unknown_mass_percent);
  const securities = exposureRows(
    portfolio?.security_exposure_lower_bounds ??
      portfolio?.top_security_exposure_lower_bounds,
  );
  const industries = exposureRows(
    portfolio?.industry_exposure_lower_bounds ??
      portfolio?.top_industry_exposure_lower_bounds,
  );
  const markets = exposureRows(
    portfolio?.listing_market_exposure_lower_bounds ??
      portfolio?.top_listing_market_exposure_lower_bounds,
  );
  const dates = snapshotDates(research, candidates);
  const reportPeriod = formatMoment(dates.reportPeriod);
  const asOfDate = formatMoment(dates.asOfDate);
  const decisionAt = formatMoment(textValue(research.decision_at, dates.availableAt));
  const hasEvidence = Boolean(portfolio || candidates.length);
  const hasResolutionAudit =
    (Array.isArray(research.resolution_audit) && research.resolution_audit.length > 0) ||
    (isRecord(research.resolution_audit) &&
      Object.keys(research.resolution_audit).length > 0);
  const hasQualificationAudit =
    isRecord(research.qualification) && Object.keys(research.qualification).length > 0;

  return (
    <section
      aria-labelledby={headingId}
      className="overflow-hidden rounded-2xl border border-slate-200 bg-[#f5f9f8] shadow-sm"
    >
      <header className="bg-[linear-gradient(135deg,#071f29_0%,#123847_58%,#176b70_125%)] px-4 py-4 text-white sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 text-[10px] font-black tracking-[0.18em] text-cyan-100/80">
              <Layers3 size={13} aria-hidden="true" />
              LOOKTHROUGH · DISCLOSED SCOPE
            </p>
            <h3 id={headingId} className="font-display mt-1.5 text-lg font-black text-white">
              基金持仓穿透证据
            </h3>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-200">
              {statusPresentation.message}
            </p>
          </div>
          <div className="flex flex-wrap justify-end gap-1.5">
            <span
              className={`rounded-full border px-2.5 py-1 text-[10px] font-black ${statusPresentation.className}`}
            >
              {statusPresentation.label}
            </span>
            <span className="rounded-full border border-white/15 bg-black/10 px-2.5 py-1 text-[10px] font-bold text-slate-100">
              {scopeLabel(research)}
            </span>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-white/10 pt-2.5 text-[10px] text-slate-300">
          <span className="inline-flex items-center gap-1.5">
            <Database size={11} aria-hidden="true" />
            报告期 {reportPeriod ?? "未记录"}
          </span>
          {asOfDate ? <span>数据截至 {asOfDate}</span> : null}
          <span className="inline-flex items-center gap-1.5">
            <Clock3 size={11} aria-hidden="true" />
            时点 {decisionAt ?? "未记录"}
          </span>
          <span className="font-semibold text-amber-100">仅风险研究，不授权配置</span>
        </div>
      </header>

      {hasEvidence ? (
        <div className="space-y-4 p-3 sm:p-4">
          <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-slate-200 bg-slate-200 sm:grid-cols-3">
            <div className="bg-white px-3 py-3">
              <dt className="flex items-center gap-1.5 text-[10px] font-black tracking-wide text-slate-500">
                <Fingerprint size={12} aria-hidden="true" className="text-cyan-700" />
                已披露下限 · 已识别证券
              </dt>
              <dd className="mt-1.5 font-mono text-xl font-black tabular-nums text-[var(--brand-strong)]">
                {metricValue(knownMass, true)}
              </dd>
              <p className="mt-1 text-[10px] leading-4 text-slate-500">
                {disclosedMass != null
                  ? `证券披露质量下限 ≥ ${formatPercent(disclosedMass)}%`
                  : "不对未披露持仓做补齐或重标化"}
              </p>
            </div>
            <div className="bg-white px-3 py-3">
              <dt className="flex items-center gap-1.5 text-[10px] font-black tracking-wide text-slate-500">
                <CircleHelp size={12} aria-hidden="true" className="text-amber-600" />
                未知质量 · 全账户
              </dt>
              <dd className="mt-1.5 font-mono text-xl font-black tabular-nums text-slate-800">
                {metricValue(unknownAccount)}
              </dd>
              <p className="mt-1 text-[10px] leading-4 text-slate-500">
                {unknownAccount == null ? "账户分母或身份覆盖尚未核验" : "保留未识别与未覆盖质量"}
              </p>
            </div>
            <div className="bg-white px-3 py-3">
              <dt className="flex items-center gap-1.5 text-[10px] font-black tracking-wide text-slate-500">
                <CircleHelp size={12} aria-hidden="true" className="text-amber-600" />
                未知质量 · 基金持仓口径
              </dt>
              <dd className="mt-1.5 font-mono text-xl font-black tabular-nums text-slate-800">
                {metricValue(unknownFundScope)}
              </dd>
              <p className="mt-1 text-[10px] leading-4 text-slate-500">
                {unknownFundScope == null ? "非该口径或数据尚未核验" : "仅以已提供基金持仓为分母"}
              </p>
            </div>
          </dl>

          {securities.length || industries.length || markets.length ? (
            <div>
              <div className="flex flex-wrap items-baseline justify-between gap-2 px-1">
                <h4 className="text-sm font-black text-slate-900">组合敞口下限</h4>
                <p className="text-[10px] text-slate-500">每项均为已披露下限，并非完整组合占比</p>
              </div>
              <div className="mt-2 grid gap-2 lg:grid-cols-3">
                <ExposureGroup
                  title="证券 · 已披露下限"
                  rows={securities}
                  labelKeys={["security_name", "security_key", "security_code", "label"]}
                />
                <ExposureGroup
                  title="行业 · 已披露下限"
                  rows={industries}
                  labelKeys={["industry", "industry_name", "label"]}
                  unknownMass={industryUnknown}
                />
                <ExposureGroup
                  title="上市市场 · 已披露下限"
                  rows={markets}
                  labelKeys={["listing_market", "label"]}
                  unknownMass={marketUnknown}
                />
              </div>
            </div>
          ) : null}

          {candidates.length ? (
            <section aria-label="候选基金持仓重合" className="space-y-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2 px-1">
                <h4 className="text-sm font-black text-slate-900">
                  {context === "discovery" ? "候选与当前组合" : "候选基金重合证据"}
                </h4>
                <p className="text-[10px] text-slate-500">解释字段优先；未知不等于零</p>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                {candidates.map((candidate, index) => (
                  <CandidateEvidence
                    key={`${textValue(candidate.fund_code) ?? "candidate"}-${index}`}
                    candidate={candidate}
                    candidateNames={candidateNames}
                  />
                ))}
              </div>
            </section>
          ) : null}
        </div>
      ) : (
        <div className="flex items-start gap-3 px-4 py-4 text-sm leading-6 text-slate-600 sm:px-5">
          <CircleHelp size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-slate-400" />
          <p>
            暂无可展示的持仓穿透快照。系统保留未知状态，不将缺失资料解释为零敞口或零重合。
          </p>
        </div>
      )}

      <footer className="flex flex-wrap items-start justify-between gap-2 border-t border-slate-200 bg-white/70 px-4 py-2.5 text-[10px] leading-5 text-slate-500">
        <p>
          下限仅来自已披露持仓；披露范围内未发现共同证券，不代表完整组合零重合。
        </p>
        <span className="flex flex-wrap gap-x-3 gap-y-1">
          {hasQualificationAudit ? (
            <span className="inline-flex items-center gap-1 font-semibold text-slate-600">
              <Database size={11} aria-hidden="true" />
              穿透资格审计已记录
            </span>
          ) : null}
          {hasResolutionAudit ? (
            <span className="inline-flex items-center gap-1 font-semibold text-slate-600">
              <Fingerprint size={11} aria-hidden="true" />
              证券身份解析审计已记录
            </span>
          ) : null}
        </span>
      </footer>
    </section>
  );
}
