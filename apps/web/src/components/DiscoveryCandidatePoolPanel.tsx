"use client";

import { useState } from "react";
import {
  BarChart3,
  ChevronDown,
  CircleHelp,
  Layers,
  Scale,
  ShieldAlert,
} from "lucide-react";
import type { DiscoveryCandidatePoolItem, EliminatedCandidate } from "@/lib/api";
import { translateEvidenceText } from "@/lib/decisionText";
import { FundTradeabilityEvidence } from "@/components/FundTradeabilityEvidence";

const CORE_FIELD_LABELS: Record<string, string> = {
  return_3m_percent: "近3月收益",
  return_6m_percent: "近6月收益",
  max_drawdown_1y_percent: "近1年回撤",
  fund_scale_yi: "最新规模",
  established_date: "成立日期",
  fund_manager: "基金经理",
  nav_date: "净值日期",
};

export type DiscoveryCandidateDecisionStatus =
  | "actionable"
  | "conditional_wait"
  | "watch_only";

const DECISION_STATUS_META: Record<
  DiscoveryCandidateDecisionStatus,
  { label: string; badgeClass: string; rowClass: string }
> = {
  actionable: {
    label: "可执行",
    badgeClass: "bg-emerald-100 text-emerald-900",
    rowClass: "border-emerald-200 bg-emerald-50/70",
  },
  conditional_wait: {
    label: "等待条件",
    badgeClass: "bg-amber-100 text-amber-900",
    rowClass: "border-amber-200 bg-amber-50/70",
  },
  watch_only: {
    label: "研究观察",
    badgeClass: "bg-slate-200 text-slate-800",
    rowClass: "border-slate-200 bg-slate-50/80",
  },
};

type DiscoveryCandidatePoolPanelProps = {
  pool: DiscoveryCandidatePoolItem[];
  selectedCodes?: string[];
  decisionStatusByCode?: Record<string, DiscoveryCandidateDecisionStatus>;
  /** M4/M5：被双向 guard 因证据强烈共振剔除的候选（不出现在 recommendations 里）。 */
  eliminatedCandidates?: EliminatedCandidate[];
};

type CandidateQualityPresentation = {
  fieldLabel: string;
  fieldBadgeClass: string;
  gateLabel: string;
  gateBadgeClass: string;
  missingLabels: string[];
  staleLabels: string[];
  pending: boolean;
  impact: string;
  degraded: boolean;
  unknown: boolean;
};

function formatPercent(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return `${value}%`;
}

function formatScore(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "—";
  }
  return Number(value).toFixed(2).replace(/\.00$/, "");
}

function formatMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "待核验";
  return `¥${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
}

function listText(items: string[] | undefined, fallback = "—"): string {
  return items?.length ? items.join("；") : fallback;
}

function profileSourceLabel(source: string): string {
  if (source.includes("fund_scale_open_sina")) return "新浪基金规模";
  if (source.includes("fund_individual_basic_info_xq")) return "雪球/蛋卷基金详情";
  return "基金资料源";
}

const PEER_METRIC_ORDER = [
  "return_3m_percent",
  "return_6m_percent",
  "return_1y_percent",
  "max_drawdown_1y_percent",
  "fund_scale_yi",
] as const;
const PEER_SUMMARY_METRIC_ORDER = [
  "return_6m_percent",
  "return_1y_percent",
  "return_3m_percent",
  "max_drawdown_1y_percent",
  "fund_scale_yi",
] as const;

function peerStatusLabel(status: string | undefined): string {
  if (status === "qualified") return "描述数据完整";
  if (status === "descriptive_only") return "样本仅供描述";
  if (status === "insufficient") return "样本不足";
  return "描述状态未记录";
}

function formatPeerPercentile(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "分位缺失";
  return `${Number(value).toFixed(1)} 分位`;
}

function formatSignedPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const rounded = Number(value).toFixed(2);
  return `${value > 0 ? "+" : ""}${rounded}%`;
}

function ResearchEvidence({ item }: { item: DiscoveryCandidatePoolItem }) {
  const peerRank =
    item.peer_rank && Object.keys(item.peer_rank).length
      ? item.peer_rank
      : item.peer_research;
  const peerGroup =
    item.peer_group && Object.keys(item.peer_group).length
      ? item.peer_group
      : peerRank?.peer_group;
  const groupLabel = peerGroup?.group_label ?? peerRank?.group_label;
  const peerCount =
    peerRank?.universe?.independent_peer_family_count ??
    peerRank?.independent_peer_family_count;
  const metrics = peerRank?.metrics ?? {};
  const orderedMetrics = [
    ...PEER_METRIC_ORDER.filter((key) => metrics[key]),
    ...Object.keys(metrics).filter(
      (key) => !PEER_METRIC_ORDER.includes(key as (typeof PEER_METRIC_ORDER)[number]),
    ),
  ]
    .map((key) => [key, metrics[key]] as const)
    .filter(
      ([, metric]) =>
        metric &&
        metric.applicable !== false &&
        metric.applicability !== "not_applicable" &&
        metric.percentile != null,
    );
  const benchmark = [
    item.benchmark_research,
    item.benchmark_comparison,
    peerGroup?.benchmark,
    peerRank?.benchmark,
  ].find((value) => value && Object.keys(value).length);
  const benchmarkMetrics = item.benchmark_metrics;
  const benchmarkSpec = item.benchmark_spec;
  const benchmarkName =
    benchmarkMetrics?.benchmark_name ??
    benchmarkMetrics?.benchmark_code ??
    benchmark?.benchmark_name ??
    benchmark?.benchmark_code ??
    benchmarkSpec?.benchmark_name ??
    benchmarkSpec?.benchmark_code;
  const formalBenchmark =
    benchmark?.comparison_role === "formal_excess" &&
    benchmark.formal_excess_eligible === true &&
    Boolean(benchmark.mapping_id) &&
    (benchmark.qualified === true ||
      benchmark.contract_verification_kind === "verified_fund_contract");
  const trackingReference = benchmark?.comparison_role === "tracking_reference";
  const metricsRole = benchmarkMetrics?.comparison_role;
  const effectiveFormalBenchmark =
    benchmarkMetrics?.formal_excess_eligible === true && metricsRole === "formal_excess";
  const effectiveTrackingReference = metricsRole === "tracking_reference";
  const verifiedFormalBenchmark =
    formalBenchmark ||
    (benchmarkMetrics?.status === "qualified" && effectiveFormalBenchmark);
  const visibleTrackingReference = trackingReference || effectiveTrackingReference;
  const benchmarkHorizonEntry = (["1y", "6m", "3m"] as const)
    .map((key) => [key, benchmarkMetrics?.horizons?.[key]] as const)
    .find(([, value]) => value?.status === "available");
  const benchmarkHorizonLabel = benchmarkHorizonEntry?.[0] === "1y"
    ? "近1年"
    : benchmarkHorizonEntry?.[0] === "6m"
      ? "近6月"
      : benchmarkHorizonEntry?.[0] === "3m"
        ? "近3月"
        : null;
  const benchmarkHorizon = benchmarkHorizonEntry?.[1];
  const comparisonDifference = effectiveFormalBenchmark
    ? benchmarkHorizon?.formal_excess_return_percent
    : effectiveTrackingReference
      ? benchmarkHorizon?.reference_difference_percent
      : null;
  const rollingWinRate = effectiveFormalBenchmark
    ? benchmarkMetrics?.rolling_comparison?.formal_excess_win_rate_percent
    : effectiveTrackingReference
      ? benchmarkMetrics?.rolling_comparison?.reference_outperformance_rate_percent
      : null;
  const hasPeer = Boolean(groupLabel || peerRank?.status || orderedMetrics.length);
  const hasBenchmark = Boolean(
    benchmarkName || benchmark?.comparison_role || benchmarkMetrics?.status,
  );
  const peerSnapshotAfterDecision = Boolean(
    peerRank?.reasons?.includes("target_membership_available_after_decision_at") ||
      peerRank?.reason === "target_membership_available_after_decision_at",
  );

  if (!hasPeer && !hasBenchmark) {
    return (
      <div
        aria-label="同类研究与基准未记录"
        className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500"
      >
        历史报告未记录同类分位与基准角色
      </div>
    );
  }

  return (
    <div
      role="group"
      aria-label="同类研究与基准"
      className="space-y-2 rounded-xl border border-slate-200 bg-slate-50/80 p-2.5"
    >
      {hasPeer ? (
        <div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="inline-flex items-center gap-1 text-[11px] font-black text-slate-800">
              <BarChart3 size={13} aria-hidden="true" className="text-[var(--brand)]" />
              {groupLabel || "同类组待确认"}
            </span>
            <span className="rounded-full border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] font-bold text-slate-600">
              {peerStatusLabel(peerRank?.status)}
            </span>
          </div>
          {peerCount != null ? (
            <p className="mt-1 text-[10px] tabular-nums text-slate-500">
              独立基金家族样本 {peerCount}
            </p>
          ) : null}
          {orderedMetrics.length ? (
            <dl className="mt-1.5 grid grid-cols-2 gap-x-2 gap-y-1 text-[10px] leading-4">
              {orderedMetrics.map(([key, metric]) => (
                <div key={key} className="min-w-0 border-t border-slate-200/80 pt-1">
                  <dt className="truncate text-slate-500">{metric.label ?? key}</dt>
                  <dd className="font-bold tabular-nums text-slate-800">
                    {formatPeerPercentile(metric.percentile)}
                    {metric.sample_count != null ? ` · n=${metric.sample_count}` : ""}
                  </dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="mt-1.5 rounded-lg bg-white px-2 py-1.5 text-[10px] leading-4 text-slate-500">
              {peerCount === 0
                ? peerSnapshotAfterDecision
                  ? "本次同类快照未通过时点校验，已隐藏空分位。"
                  : "当前未形成独立同类样本；不重复展示空分位。"
                : "当前可比指标样本不足，分位暂不可用。"}
            </p>
          )}
          <p className="mt-1.5 text-[10px] font-semibold leading-4 text-amber-800">
            仅研究描述，不参与金额分配
          </p>
        </div>
      ) : null}

      {hasBenchmark ? (
        <div className="border-t border-slate-200 pt-2">
          <div className="flex items-start gap-1.5">
            <Scale size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-slate-500" />
            <div className="min-w-0 text-[10px] leading-4">
              <p className="font-black text-slate-800">
                {verifiedFormalBenchmark
                  ? "正式业绩基准"
                  : visibleTrackingReference
                    ? "跟踪参考（非正式基准）"
                    : "基准线索（身份未核验）"}
              </p>
              <p className="break-words text-slate-600 [overflow-wrap:anywhere]">
                {benchmarkName || "未记录基准名称"}
              </p>
              {benchmarkMetrics?.status === "qualified" && benchmarkHorizon ? (
                <dl className="mt-1.5 space-y-1 border-t border-slate-200/80 pt-1.5 tabular-nums text-slate-600">
                  <div className="flex flex-wrap justify-between gap-x-2">
                    <dt>
                      {benchmarkHorizonLabel}
                      {effectiveFormalBenchmark ? "正式超额" : "相对参考差异"}
                    </dt>
                    <dd className="font-black text-slate-800">
                      {formatSignedPercent(comparisonDifference)}
                    </dd>
                  </div>
                  <div className="flex flex-wrap justify-between gap-x-2">
                    <dt>基金 / 参考收益</dt>
                    <dd className="font-semibold text-slate-700">
                      {formatSignedPercent(benchmarkHorizon.fund_return_percent)} / {formatSignedPercent(benchmarkHorizon.benchmark_return_percent)}
                    </dd>
                  </div>
                  {rollingWinRate != null ? (
                    <div className="flex flex-wrap justify-between gap-x-2">
                      <dt>{benchmarkMetrics.rolling_comparison?.window_days ?? 20}日滚动胜率</dt>
                      <dd className="font-semibold text-slate-700">{Number(rollingWinRate).toFixed(1)}%</dd>
                    </div>
                  ) : null}
                  <div className="flex flex-wrap justify-between gap-x-2">
                    <dt>对齐样本</dt>
                    <dd className="font-semibold text-slate-700">
                      {benchmarkMetrics.alignment?.common_return_sample_days ?? "—"} 日
                    </dd>
                  </div>
                </dl>
              ) : benchmarkMetrics?.status ? (
                <p className="mt-1 text-slate-500">
                  对齐指标暂不可用
                  {benchmarkMetrics.reason_codes?.length
                    ? `（${benchmarkMetrics.reason_codes.join("、")}）`
                    : ""}
                </p>
              ) : null}
              {!verifiedFormalBenchmark ? (
                <p className="mt-0.5 text-slate-500">不得用于正式超额收益判断</p>
              ) : null}
              <p className="mt-1 font-semibold text-amber-800">对齐指标仅研究描述，不参与金额分配</p>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function CandidateTradeSummary({ item }: { item: DiscoveryCandidatePoolItem }) {
  const tradeability = item.tradeability;
  const gate = item.tradeability_gate ?? tradeability?.tradeability_gate;
  const hasSnapshot = Boolean(
    (tradeability && Object.keys(tradeability).length) ||
      (gate && Object.keys(gate).length),
  );
  if (!hasSnapshot) {
    return (
      <div
        className="rounded-xl bg-slate-50 px-3 py-2 text-[11px] text-slate-500"
        aria-label="交易条件摘要"
      >
        交易条件未记录
      </div>
    );
  }

  const state = tradeability?.purchase_state ?? "unknown";
  const stateLabel =
    state === "open"
      ? "开放"
      : state === "limited"
        ? "限大额"
        : state === "suspended"
          ? "暂停"
          : state === "closed"
            ? "封闭"
            : "待核验";
  const statusUsable =
    ["complete", "partial"].includes(tradeability?.data_status ?? "") &&
    tradeability?.freshness === "fresh";
  const initialMinimum =
    gate?.effective_initial_min_purchase_yuan ??
    tradeability?.minimum_initial_purchase_yuan ??
    tradeability?.minimum_purchase_yuan;
  const unlimited =
    gate?.max_purchase_unlimited ?? tradeability?.daily_purchase_limit_unlimited;
  const limit = gate?.max_purchase_yuan ?? tradeability?.daily_purchase_limit_yuan;
  const gateLabel =
    gate?.status === "eligible"
      ? "执行门禁通过"
      : gate?.status === "excluded"
        ? "场外申购排除"
        : "仅研究观察";

  return (
    <div
      className="rounded-xl border border-slate-200 bg-slate-50/80 px-3 py-2 text-[11px] leading-5"
      aria-label="交易条件摘要"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <span className={statusUsable ? "font-black text-emerald-800" : "font-black text-amber-900"}>
          {statusUsable ? `申购${stateLabel}` : `申购记录${stateLabel}（证据${tradeability?.freshness === "stale" ? "过期" : "不可用"}）`}
        </span>
        <span className="font-semibold text-slate-700">{gateLabel}</span>
      </div>
      <p className="text-slate-500">
        起购 {formatMoney(initialMinimum)} · 单日{unlimited ? "无限额" : `限额 ${formatMoney(limit)}`}
      </p>
    </div>
  );
}

function buildCandidateResearchSummary(item: DiscoveryCandidatePoolItem) {
  const peerRank =
    item.peer_rank && Object.keys(item.peer_rank).length
      ? item.peer_rank
      : item.peer_research;
  const peerGroup =
    item.peer_group && Object.keys(item.peer_group).length
      ? item.peer_group
      : peerRank?.peer_group;
  const peerCount =
    peerRank?.universe?.independent_peer_family_count ??
    peerRank?.independent_peer_family_count;
  const metrics = peerRank?.metrics ?? {};
  const preferredMetric = PEER_SUMMARY_METRIC_ORDER
    .map((key) => metrics[key])
    .find((metric) => metric?.percentile != null);
  if (
    peerCount == null ||
    peerCount <= 0 ||
    preferredMetric?.percentile == null ||
    peerGroup?.qualified === false
  ) {
    return null;
  }
  const benchmark = [
    item.benchmark_research,
    item.benchmark_comparison,
    item.benchmark_metrics,
  ].find((value) => value && Object.keys(value).length);
  const benchmarkLabel =
    benchmark?.comparison_role === "formal_excess" &&
    benchmark.formal_excess_eligible === true
      ? "正式基准"
      : benchmark?.comparison_role === "tracking_reference"
        ? "跟踪参考"
        : benchmark
          ? "基准待核验"
          : null;

  const configuredMinimum = Number(
    peerRank?.qualification_policy?.minimum_independent_peer_families,
  );
  const minimumPeerCount =
    Number.isFinite(configuredMinimum) && configuredMinimum > 0
      ? configuredMinimum
      : 20;
  return {
    peerCount,
    metricLabel: preferredMetric.label ?? "代表指标",
    percentile: preferredMetric.percentile,
    benchmarkLabel,
    smallSample: peerCount < minimumPeerCount,
  };
}

type CandidateResearchSummaryData = NonNullable<
  ReturnType<typeof buildCandidateResearchSummary>
>;

function CandidateResearchSummary({
  summary,
}: {
  summary: CandidateResearchSummaryData;
}) {
  const { peerCount, metricLabel, percentile, benchmarkLabel, smallSample } =
    summary;

  return (
    <div
      className="rounded-xl border border-sky-100 bg-[linear-gradient(135deg,rgba(240,249,255,0.92),rgba(248,250,252,0.86))] px-3 py-2 text-[11px] leading-5"
      aria-label="同类研究摘要"
    >
      <p className="flex flex-wrap items-center gap-1.5 font-black text-slate-800">
        <span>同类研究 · {peerCount} 家</span>
        {smallSample ? (
          <span className="rounded-full bg-amber-100 px-1.5 py-0.5 text-[9px] leading-4 text-amber-800">
            小样本
          </span>
        ) : null}
      </p>
      <p className="text-slate-500">
        {metricLabel} {formatPeerPercentile(percentile)}
        {benchmarkLabel ? ` · ${benchmarkLabel}` : ""}
      </p>
    </div>
  );
}

function qualityPresentation(
  item: DiscoveryCandidatePoolItem,
  eliminated: boolean,
): CandidateQualityPresentation {
  const gate = item.quality_gate;
  if (!gate) {
    return {
      fieldLabel: "完整性未记录",
      fieldBadgeClass: "bg-slate-100 text-slate-700",
      gateLabel: eliminated ? "已剔除" : "门禁状态未知",
      gateBadgeClass: eliminated
        ? "bg-rose-100 text-rose-800"
        : "bg-slate-100 text-slate-700",
      missingLabels: [],
      staleLabels: [],
      pending: false,
      impact: eliminated
        ? "已被系统剔除，不会进入推荐。"
        : "缺少历史质量门禁快照，应按保守口径理解，不能仅凭该行形成买入动作。",
      degraded: eliminated,
      unknown: true,
    };
  }

  const missingLabels = gate.missing_fields.map(
    (field) => CORE_FIELD_LABELS[field] ?? "其他核心字段",
  );
  const staleLabels = [
    ...new Set([
      ...(item.profile_stale_fields ?? []),
      ...(gate.profile_stale_fields ?? []),
    ]),
  ].map((field) => CORE_FIELD_LABELS[field] ?? "其他档案字段");
  const pending = missingLabels.length > 0 || staleLabels.length > 0;
  const excluded = eliminated || gate.status === "excluded";
  const degraded = excluded || gate.status === "watch_only";

  return {
    fieldLabel: pending
      ? `待补/刷新 ${new Set([...missingLabels, ...staleLabels]).size} 项`
      : "核心字段完整",
    fieldBadgeClass: pending
      ? "bg-amber-100 text-amber-900"
      : "bg-emerald-100 text-emerald-900",
    gateLabel: excluded ? "已剔除" : degraded ? "质量降级" : "质量门禁通过",
    gateBadgeClass: excluded
      ? "bg-rose-100 text-rose-800"
      : degraded
        ? "bg-slate-200 text-slate-800"
        : "bg-emerald-100 text-emerald-900",
    missingLabels,
    staleLabels,
    pending,
    impact: excluded
      ? "该候选已被系统剔除，不会进入推荐。"
      : degraded
        ? "该候选仅作研究观察，不会形成可执行买入动作。"
        : "核心字段质量门禁已通过；最终动作仍需结合策略与风险守卫。",
    degraded,
    unknown: false,
  };
}

function QualityDetails({
  item,
  quality,
  eliminated,
  className = "",
}: {
  item: DiscoveryCandidatePoolItem;
  quality: CandidateQualityPresentation;
  eliminated: boolean;
  className?: string;
}) {
  const profileFacts = [
    item.fund_scale_yi != null
      ? `规模 ${formatScore(item.fund_scale_yi)} 亿元（${
          item.fund_scale_basis === "nav_times_xq_latest_shares"
            ? "净值×雪球最近份额估算"
            : "净值×最近份额估算"
        }）`
      : null,
    item.fund_manager ? `经理 ${item.fund_manager}` : null,
    item.established_date ? `成立 ${item.established_date}` : null,
  ].filter(Boolean);
  const profileStatus = item.profile_status ?? item.quality_gate?.profile_status;
  const profileSources = item.profile_sources ?? item.quality_gate?.profile_sources ?? [];
  const staleFieldLabels = quality.staleLabels;
  const reason = eliminated
    ? "已被证据强度规则剔除"
    : listText(item.quality_reasons, item.selection_reason ?? "暂无补充理由");

  return (
    <details className={`group rounded-xl border border-slate-200 bg-white/85 ${className}`}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 rounded-xl px-3 text-xs font-bold text-slate-700 outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2 [&::-webkit-details-marker]:hidden">
        <span>查看数据完整性与质量依据</span>
        <ChevronDown
          size={15}
          aria-hidden="true"
          className="shrink-0 text-slate-400 transition group-open:rotate-180"
        />
      </summary>
      <div className="space-y-1.5 border-t border-slate-100 px-3 py-2.5 text-xs leading-5 text-slate-600">
        {item.quality_gate ? (
          <p className="text-slate-500">
            字段覆盖 {item.quality_gate.coverage_percent}%
            {item.quality_gate.data_as_of ? ` · 数据时点 ${item.quality_gate.data_as_of}` : ""}
          </p>
        ) : null}
        {quality.missingLabels.length ? (
          <p>
            <span className="font-bold text-amber-900">待补字段：</span>
            {quality.missingLabels.join("、")}
          </p>
        ) : null}
        {profileFacts.length ? (
          <p>
            <span className="font-bold text-slate-800">核心档案：</span>
            {profileFacts.join(" · ")}
          </p>
        ) : null}
        {profileStatus ? (
          <p className="text-slate-500">
            档案补全：
            {profileStatus === "complete"
              ? "核心档案已补全"
              : profileStatus === "partial"
                ? "部分字段待补"
                : profileStatus === "stale_fallback"
                  ? "刷新失败，使用过期缓存"
                  : profileStatus === "unavailable"
                    ? "双源暂不可用"
                    : "状态待确认"}
            {profileSources.length
              ? ` · ${[...new Set(profileSources.map(profileSourceLabel))].join(" + ")}`
              : ""}
          </p>
        ) : null}
        {staleFieldLabels.length ? (
          <p className="font-semibold text-amber-900">
            <span className="font-bold">待刷新字段：</span>
            {staleFieldLabels.join("、")}
          </p>
        ) : null}
        <p>
          <span className="font-bold text-slate-800">质量依据：</span>
          {reason}
        </p>
        {item.quality_gate?.reasons.length ? (
          <p>
            <span className="font-bold text-slate-800">门禁原因：</span>
            {listText(item.quality_gate.reasons)}
          </p>
        ) : null}
        {item.quality_penalties?.length ? (
          <p>
            <span className="font-bold text-amber-900">风险短板：</span>
            {listText(item.quality_penalties)}
          </p>
        ) : null}
        <p className={quality.degraded ? "font-semibold text-amber-900" : "text-slate-500"}>
          <span className="font-bold">决策影响：</span>
          {quality.impact}
        </p>
      </div>
    </details>
  );
}

export function DiscoveryCandidatePoolPanel({
  pool,
  selectedCodes = [],
  decisionStatusByCode = {},
  eliminatedCandidates = [],
}: DiscoveryCandidatePoolPanelProps) {
  const [open, setOpen] = useState(false);
  if (!pool.length) {
    return null;
  }

  const selected = new Set(selectedCodes);
  const eliminatedByCode = new Map(eliminatedCandidates.map((item) => [item.fund_code, item]));
  const presentations = new Map(
    pool.map((item) => [
      item.fund_code,
      qualityPresentation(item, eliminatedByCode.has(item.fund_code)),
    ]),
  );
  const completeCount = pool.filter(
    (item) => !presentations.get(item.fund_code)?.unknown && !presentations.get(item.fund_code)?.pending,
  ).length;
  const pendingCount = pool.filter(
    (item) => Boolean(presentations.get(item.fund_code)?.pending),
  ).length;
  const degradedCount = pool.filter(
    (item) => presentations.get(item.fund_code)?.degraded,
  ).length;
  const unknownCount = pool.filter(
    (item) => presentations.get(item.fund_code)?.unknown,
  ).length;

  return (
    <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex min-h-11 w-full items-start justify-between gap-3 px-5 py-4 text-left"
        aria-expanded={open}
        aria-controls="discovery-candidate-pool-content"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-bold text-slate-900">
            <Layers size={16} className="shrink-0 text-[var(--brand)]" />
            本次候选池（{pool.length} 只）
          </div>
          <div
            className="mt-2 flex flex-wrap gap-1.5 text-[11px] font-bold"
            aria-label={`核心字段完整 ${completeCount} 只，待补全或刷新 ${pendingCount} 只，质量降级 ${degradedCount} 只，状态未记录 ${unknownCount} 只`}
          >
            <span className="rounded-full bg-emerald-100 px-2 py-1 text-emerald-900">
              字段完整 {completeCount}
            </span>
            <span className="rounded-full bg-amber-100 px-2 py-1 text-amber-900">
              待补/刷新 {pendingCount}
            </span>
            <span className="rounded-full bg-slate-200 px-2 py-1 text-slate-800">
              质量降级 {degradedCount}
            </span>
            {unknownCount ? (
              <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-700">
                状态未记录 {unknownCount}
              </span>
            ) : null}
          </div>
        </div>
        <ChevronDown
          size={18}
          aria-hidden="true"
          className={`mt-1 shrink-0 text-slate-500 transition ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open ? (
        <div id="discovery-candidate-pool-content" className="border-t border-slate-100">
          <div className="mx-3 mt-3 flex items-start gap-2 rounded-xl bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-600">
            <CircleHelp size={15} aria-hidden="true" className="mt-0.5 shrink-0 text-slate-500" />
            <p>
              核心字段缺失会触发质量降级，候选仅作研究观察；已剔除项不会进入推荐。
              “字段完整”也不等于必然买入，仍需通过策略与风险守卫。同类分位只作描述性研究，
              不参与金额；只有通过合同核验的正式基准才能用于超额收益判断。
            </p>
          </div>

          {eliminatedCandidates.length ? (
            <details className="group mx-3 mt-3 rounded-xl border border-rose-200 bg-rose-50/80">
              <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 px-3 text-xs font-black text-rose-900 [&::-webkit-details-marker]:hidden">
                <span className="flex items-center gap-1.5">
                  <ShieldAlert size={14} aria-hidden="true" />
                  系统已剔除 {eliminatedCandidates.length} 只候选
                </span>
                <ChevronDown
                  size={15}
                  aria-hidden="true"
                  className="transition group-open:rotate-180"
                />
              </summary>
              <ul className="space-y-1 border-t border-rose-200 px-3 py-2.5 text-xs leading-5 text-rose-900">
                {eliminatedCandidates.map((item) => (
                  <li key={item.fund_code} className="break-words [overflow-wrap:anywhere]">
                    <span className="font-mono font-semibold">{item.fund_code}</span> {item.fund_name}
                    {item.sector_name ? `（${item.sector_name}）` : ""}：
                    {translateEvidenceText(item.basis || item.reasons.join("；"))}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}

          <div
            className="grid gap-3 px-3 pb-4 pt-3 xl:grid-cols-2"
            role="region"
            aria-label="基金候选池重点信息"
          >
            {pool.map((item) => {
              const picked = selected.has(item.fund_code);
              const eliminated = eliminatedByCode.has(item.fund_code);
              const decisionStatus =
                decisionStatusByCode[item.fund_code] ?? (picked ? "actionable" : undefined);
              const decisionMeta = decisionStatus ? DECISION_STATUS_META[decisionStatus] : null;
              const quality = presentations.get(item.fund_code)!;
              const primaryReason =
                item.quality_gate?.reasons?.[0] ??
                item.quality_penalties?.[0] ??
                item.quality_reasons?.[0] ??
                item.selection_reason;
              const researchSummary = buildCandidateResearchSummary(item);
              return (
                <article
                  key={item.fund_code}
                  className={`min-w-0 rounded-2xl border p-3.5 ${
                    eliminated
                      ? "border-rose-200 bg-rose-50/70"
                      : decisionMeta
                        ? decisionMeta.rowClass
                        : "border-slate-200 bg-white"
                  }`}
                  aria-label={`${item.fund_name}，${eliminated ? "已剔除" : decisionMeta?.label ?? quality.gateLabel}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className={`break-words text-sm font-black text-slate-900 ${eliminated ? "line-through" : ""}`}>
                        {item.fund_name}
                      </h3>
                      <p className="mt-1 text-xs text-slate-500">
                        <span className="font-mono font-bold">{item.fund_code}</span>
                        {item.sector_label ? ` · ${item.sector_label}` : ""}
                      </p>
                    </div>
                    <div className="flex shrink-0 flex-wrap justify-end gap-1">
                      {item.is_new_issue ? (
                        <span className="rounded-full bg-amber-100 px-2 py-1 text-[11px] font-bold text-amber-800">新发</span>
                      ) : null}
                      <span className={`rounded-full px-2 py-1 text-[11px] font-bold ${quality.fieldBadgeClass}`}>
                        {quality.fieldLabel}
                      </span>
                      <span className={`rounded-full px-2 py-1 text-[11px] font-bold ${
                        eliminated
                          ? "bg-rose-100 text-rose-800"
                          : decisionMeta?.badgeClass ?? quality.gateBadgeClass
                      }`}>
                        {eliminated ? "已剔除" : decisionMeta?.label ?? quality.gateLabel}
                      </span>
                    </div>
                  </div>

                  <dl className="mt-3 grid grid-cols-2 gap-1.5 text-xs sm:grid-cols-5">
                    {[
                      ["质量分", formatScore(item.fund_quality_score)],
                      ["匹配分", formatScore(item.sector_fit_score)],
                      ["近3月", formatPercent(item.return_3m_percent)],
                      ["近6月", formatPercent(item.return_6m_percent)],
                      ["近1年", formatPercent(item.return_1y_percent)],
                    ].map(([label, value]) => (
                      <div key={label} className="rounded-xl bg-white/80 px-2.5 py-2">
                        <dt className="text-[11px] text-slate-500">{label}</dt>
                        <dd className="mt-0.5 font-black tabular-nums text-slate-900">{value}</dd>
                      </div>
                    ))}
                  </dl>

                  <div
                    className={`mt-2 grid gap-2 ${researchSummary ? "sm:grid-cols-2" : "grid-cols-1"}`}
                  >
                    <CandidateTradeSummary item={item} />
                    {researchSummary ? (
                      <CandidateResearchSummary summary={researchSummary} />
                    ) : null}
                  </div>

                  {primaryReason ? (
                    <p className="mt-2 line-clamp-2 text-[11px] leading-5 text-slate-600">
                      <span className="font-bold text-slate-800">关键约束：</span>
                      {translateEvidenceText(primaryReason)}
                    </p>
                  ) : null}

                  <details className="group mt-2 rounded-xl border border-slate-200 bg-white/85">
                    <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 rounded-xl px-3 text-xs font-bold text-slate-700 outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)] focus-visible:ring-offset-2 [&::-webkit-details-marker]:hidden">
                      <span>查看交易条件、同类研究与完整依据</span>
                      <ChevronDown
                        size={15}
                        aria-hidden="true"
                        className="shrink-0 text-slate-400 transition group-open:rotate-180"
                      />
                    </summary>
                    <div className="grid gap-2 border-t border-slate-100 p-2.5 lg:grid-cols-2">
                      <FundTradeabilityEvidence
                        tradeability={item.tradeability}
                        tradeabilityGate={item.tradeability_gate}
                        costAssessment={item.cost_assessment}
                        compact
                      />
                      <ResearchEvidence item={item} />
                      <QualityDetails
                        item={item}
                        quality={quality}
                        eliminated={eliminated}
                        className="lg:col-span-2"
                      />
                    </div>
                  </details>
                </article>
              );
            })}
          </div>
        </div>
      ) : null}
    </section>
  );
}
