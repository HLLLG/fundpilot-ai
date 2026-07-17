"use client";

import type {
  Dispatch,
  MutableRefObject,
  SetStateAction,
} from "react";
import { ChevronDown, History, Loader2, RotateCcw, Sparkles, Target } from "lucide-react";
import type {
  DiscoveryPromptConfig,
  DiscoveryRecommendation,
  DiscoverySectorHeat,
  FundDiscoveryReport,
  Holding,
  InvestorProfile,
  DiscoveryScanMode,
  DiscoveryStrategy,
} from "@/lib/api";
import {
  fetchDiscoveryPrompt,
  fetchDiscoverySectors,
  listDiscoveryReports,
  saveDiscoveryPromptRemote,
  startDiscoveryJob,
} from "@/lib/api";
import { DiscoveryHistoryWorkspace } from "@/components/DiscoveryHistoryWorkspace";
import { InlineNotice, type NoticeTone } from "@/components/InlineNotice";
import { DiscoveryReportPanel } from "@/components/DiscoveryReportPanel";
import { DiscoverySkeleton } from "@/components/DiscoverySkeleton";
import { FocusSectorPicker } from "@/components/FocusSectorPicker";
import { DiscoveryStrategySelector } from "@/components/DiscoveryStrategySelector";
import { RolePromptEditor } from "@/components/RolePromptEditor";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";
import { displayableHoldings } from "@/lib/holdingMetrics";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  appendStreamTokenBuffer,
  streamDiscovery,
  streamTimestamp,
  type DiscoveryRecommendationPartial,
  type StreamingDiscoveryState,
} from "@/lib/discoveryStreamApi";
import { ensureNotificationPermission } from "@/lib/notifications";
import { loadDiscoveryPrompt, loadDiscoverySectorHeatCache, saveDiscoveryPrompt, saveDiscoverySectorHeatCache } from "@/lib/storage";
import { useCachedFetch } from "@/lib/useCachedFetch";
import { buildClientCacheKey } from "@/lib/clientCache";
import {
  DISCOVERY_FOCUS_CHANGED_EVENT,
  loadDiscoveryFocusSectors,
  setDiscoveryFocusSectors,
} from "@/lib/discoveryFocusSectors";

const DISCOVERY_SECTORS_CACHE_KEY = "discovery-panel:sectors";
const DISCOVERY_REPORTS_CACHE_KEY = "discovery-panel:reports";
const DISCOVERY_SECTORS_STALE_MS = 30 * 60 * 1000;
const DISCOVERY_REPORTS_STALE_MS = 2 * 60 * 1000;
const DEFAULT_DISCOVERY_PROMPT: DiscoveryPromptConfig = {
  role_prompt: "",
  default_role_prompt: "",
  is_custom: false,
};

export function resolveDynamicDiscoveryBudgetYuan(
  holdings: Holding[],
  expectedInvestmentAmount: number | null | undefined,
): number {
  const totalHoldings = displayableHoldings(holdings).reduce((total, holding) => {
    const amount = Number(holding.holding_amount);
    return total + (Number.isFinite(amount) && amount > 0 ? amount : 0);
  }, 0);
  const expected = Number(expectedInvestmentAmount);
  const plannedTotal = Number.isFinite(expected) && expected > 0 ? expected : totalHoldings;
  return Math.max(Math.round((plannedTotal - totalHoldings) * 100) / 100, 0);
}

function formatBudgetInput(value: number): string {
  return Number.isInteger(value)
    ? String(value)
    : value.toFixed(2).replace(/\.?0+$/, "");
}

type DiscoveryFeedback = {
  tone: NoticeTone;
  message: string;
};

const PRIMARY_SCAN_MODE_OPTIONS: { id: DiscoveryScanMode; label: string; hint: string }[] = [
  { id: "full_market", label: "市场优选", hint: "跨方向比较后，只保留证据与质量门通过的候选" },
  { id: "portfolio_gap", label: "组合补缺", hint: "优先未重仓、热度靠前的缺口板块" },
];

const SCAN_MODE_LABELS: Record<DiscoveryScanMode, string> = {
  full_market: "市场优选",
  portfolio_gap: "组合补缺",
};

type FundDiscoveryPanelProps = {
  userId: number | null;
  holdings: Holding[];
  profile: InvestorProfile;
  discoveryJobId: string | null;
  onDiscoveryJobIdChange: (jobId: string | null) => void;
  pendingDiscoveryReport: FundDiscoveryReport | null;
  onPendingDiscoveryReportApplied: () => void;
  onRegisterDiscoveryScanRetry: (retry: (() => void) | null) => void;
  streamingDiscovery: StreamingDiscoveryState | null;
  onStreamingDiscoveryChange: Dispatch<SetStateAction<StreamingDiscoveryState | null>>;
  onDiscoveryStreamComplete: (report: FundDiscoveryReport) => void;
  onDiscoveryStreamStart?: () => void;
  discoveryStreamAbortRef: MutableRefObject<AbortController | null>;
};

export function FundDiscoveryPanel({
  userId,
  holdings,
  profile,
  discoveryJobId,
  onDiscoveryJobIdChange,
  pendingDiscoveryReport,
  onPendingDiscoveryReportApplied,
  onRegisterDiscoveryScanRetry,
  streamingDiscovery,
  onStreamingDiscoveryChange,
  onDiscoveryStreamComplete,
  onDiscoveryStreamStart,
  discoveryStreamAbortRef,
}: FundDiscoveryPanelProps) {
  const {
    data: sectorRows,
    error: sectorsError,
    loading: loadingSectors,
    refresh: refreshSectors,
  } = useCachedFetch<DiscoverySectorHeat[]>({
    cacheKey: DISCOVERY_SECTORS_CACHE_KEY,
    fetcher: fetchDiscoverySectors,
    staleTimeMs: DISCOVERY_SECTORS_STALE_MS,
    bootstrap: () => loadDiscoverySectorHeatCache(),
    keepPreviousUnless: (rows) => rows.length > 0,
  });
  const {
    data: historyReportsData,
    refresh: refreshReports,
  } = useCachedFetch<FundDiscoveryReport[]>({
    cacheKey: buildClientCacheKey(DISCOVERY_REPORTS_CACHE_KEY, userId ?? "anonymous"),
    fetcher: listDiscoveryReports,
    staleTimeMs: DISCOVERY_REPORTS_STALE_MS,
    enabled: userId != null,
    keepPreviousUnless: () => true,
  });

  const rawSectors = useMemo(() => sectorRows ?? [], [sectorRows]);
  const historyReports = useMemo(
    () =>
      [...(historyReportsData ?? [])].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [historyReportsData],
  );

  const [focusSectors, setFocusSectors] = useState<string[]>(() => loadDiscoveryFocusSectors());
  const [scanMode, setScanMode] = useState<DiscoveryScanMode>("full_market");
  const [discoveryStrategy, setDiscoveryStrategy] = useState<DiscoveryStrategy>(
    "opportunity_first",
  );
  const dynamicBudgetYuan = useMemo(
    () => resolveDynamicDiscoveryBudgetYuan(holdings, profile.expected_investment_amount),
    [holdings, profile.expected_investment_amount],
  );
  const [budgetYuan, setBudgetYuan] = useState<string>(() =>
    formatBudgetInput(dynamicBudgetYuan),
  );
  const budgetChangedByUserRef = useRef(false);
  const budgetUserRef = useRef(userId);
  const [report, setReport] = useState<FundDiscoveryReport | null>(null);
  const [discoveryPrompt, setDiscoveryPrompt] = useState<DiscoveryPromptConfig>(() =>
    loadDiscoveryPrompt(userId, DEFAULT_DISCOVERY_PROMPT),
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<DiscoveryFeedback | null>(null);
  const [configExpanded, setConfigExpanded] = useState(true);
  const [showDiscoveryCustomization, setShowDiscoveryCustomization] = useState(false);
  const [rolePromptOpen, setRolePromptOpen] = useState(false);
  const [previewHolding, setPreviewHolding] = useState<Holding | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const reportRegionRef = useRef<HTMLDivElement>(null);
  const promptPersistReady = useRef(false);
  const promptChangedByUserRef = useRef(false);
  const [promptReady, setPromptReady] = useState(false);

  useEffect(() => {
    if (budgetUserRef.current !== userId) {
      budgetUserRef.current = userId;
      budgetChangedByUserRef.current = false;
    }
    if (!budgetChangedByUserRef.current) {
      setBudgetYuan(formatBudgetInput(dynamicBudgetYuan));
    }
  }, [dynamicBudgetYuan, userId]);

  useEffect(() => {
    if (rawSectors.length > 0) {
      saveDiscoverySectorHeatCache(rawSectors);
    }
  }, [rawSectors]);

  useEffect(() => {
    const onFocusChanged = (event: Event) => {
      setFocusSectors((event as CustomEvent<string[]>).detail);
    };
    window.addEventListener(DISCOVERY_FOCUS_CHANGED_EVENT, onFocusChanged);
    return () => window.removeEventListener(DISCOVERY_FOCUS_CHANGED_EVENT, onFocusChanged);
  }, []);

  const allSectorLabels = useMemo(() => {
    const seen = new Set<string>();
    const merged: string[] = [];
    for (const label of [...rawSectors.map((row) => row.sector_label), ...focusSectors]) {
      const trimmed = label.trim();
      if (!trimmed || seen.has(trimmed)) {
        continue;
      }
      seen.add(trimmed);
      merged.push(trimmed);
    }
    return merged.sort((a, b) => a.localeCompare(b, "zh-CN"));
  }, [rawSectors, focusSectors]);

  const handleFocusSectorsChange = useCallback((next: string[]) => {
    setFocusSectors(next);
    setDiscoveryFocusSectors(next);
  }, []);

  useEffect(() => {
    promptPersistReady.current = false;
    promptChangedByUserRef.current = false;
    setPromptReady(false);
    setDiscoveryPrompt(loadDiscoveryPrompt(userId, DEFAULT_DISCOVERY_PROMPT));
    if (userId == null) {
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const remote = await fetchDiscoveryPrompt();
        if (cancelled) return;
        setDiscoveryPrompt(remote);
        saveDiscoveryPrompt(userId, remote);
      } catch {
        if (cancelled) return;
        setDiscoveryPrompt(loadDiscoveryPrompt(userId, DEFAULT_DISCOVERY_PROMPT));
      } finally {
        if (cancelled) return;
        promptPersistReady.current = true;
        setPromptReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [userId]);

  useEffect(() => {
    if (!promptReady || !promptPersistReady.current) return;
    saveDiscoveryPrompt(userId, discoveryPrompt);
    if (!promptChangedByUserRef.current) return;
    const storedValue = discoveryPrompt.is_custom ? discoveryPrompt.role_prompt : null;
    void saveDiscoveryPromptRemote(storedValue).catch(() => {
      // 离线时仍保留 localStorage
    });
  }, [discoveryPrompt, promptReady, userId]);

  useEffect(() => {
    if (!pendingDiscoveryReport) return;
    setReport(pendingDiscoveryReport);
    setFeedback(null);
    void refreshReports();
    onPendingDiscoveryReportApplied();
  }, [pendingDiscoveryReport, refreshReports, onPendingDiscoveryReportApplied]);

  const reportId = report?.id ?? null;
  useEffect(() => {
    if (reportId) {
      setConfigExpanded(false);
    }
  }, [reportId]);

  const handleCancelStream = useCallback(() => {
    discoveryStreamAbortRef.current?.abort();
    discoveryStreamAbortRef.current = null;
    onStreamingDiscoveryChange(null);
    setIsSubmitting(false);
    setFeedback({
      tone: "info",
      message: "已停止扫描，当前条件与页面中的已有结果均已保留。",
    });
  }, [discoveryStreamAbortRef, onStreamingDiscoveryChange]);

  const handleScan = useCallback(async () => {
    setIsSubmitting(true);
    setFeedback(null);
    if (report) {
      setConfigExpanded(false);
    }
    const parsedBudget = budgetYuan.trim() ? Number(budgetYuan) : null;
    const scanOptions = {
      focusSectors,
      budgetYuan:
        parsedBudget !== null && Number.isFinite(parsedBudget) && parsedBudget >= 0
          ? parsedBudget
          : null,
      fundTypePreference: "any" as const,
      selectionStrategy: "balanced" as const,
      scanMode,
      discoveryStrategy,
      systemRolePrompt: discoveryPrompt.is_custom ? discoveryPrompt.role_prompt : null,
    };

    try {
      try {
        void ensureNotificationPermission();
        onDiscoveryStreamStart?.();
        const abortController = new AbortController();
        discoveryStreamAbortRef.current = abortController;
        onStreamingDiscoveryChange({
          stage: "sector_heat",
          stageLabel: "正在连接流式扫描…",
          fundCodes: [],
          fundNames: [],
          partialByCode: {},
          stageLog: [],
          tokenBuffer: "",
          startedAt: streamTimestamp(),
        });

        await streamDiscovery(
          displayableHoldings(holdings),
          profile,
          {
            onStage: (stage, label) =>
              onStreamingDiscoveryChange((current) => {
                if (!current) {
                  return current;
                }
                const entry = { stage, label, at: streamTimestamp() };
                const stageLog = [
                  ...current.stageLog.filter((item) => item.stage !== stage),
                  entry,
                ];
                return { ...current, stage, stageLabel: label, stageLog };
              }),
            onSkeleton: (fundCodes, fundNames) =>
              onStreamingDiscoveryChange((current) =>
                current ? { ...current, fundCodes, fundNames } : current,
              ),
            onToken: (content) =>
              onStreamingDiscoveryChange((current) =>
                current
                  ? {
                      ...current,
                      tokenBuffer: appendStreamTokenBuffer(current.tokenBuffer, content),
                    }
                  : current,
              ),
            onPartial: (field, value) => {
              onStreamingDiscoveryChange((current) => {
                if (!current) {
                  return current;
                }
                if (field === "title") {
                  return { ...current, title: String(value) };
                }
                if (field === "summary") {
                  return { ...current, summary: String(value) };
                }
                if (field === "caveats" && Array.isArray(value)) {
                  return { ...current, caveats: value.map(String) };
                }
                if (field === "recommendation" && value && typeof value === "object") {
                  const rec = value as DiscoveryRecommendationPartial;
                  const code = rec.fund_code;
                  if (!code) {
                    return current;
                  }
                  return {
                    ...current,
                    partialByCode: {
                      ...current.partialByCode,
                      [code]: { ...current.partialByCode[code], ...rec },
                    },
                  };
                }
                return current;
              });
            },
            onDone: (completedReport) => {
              discoveryStreamAbortRef.current = null;
              onStreamingDiscoveryChange(null);
              onDiscoveryStreamComplete(completedReport);
              void refreshReports();
            },
            onError: (message) => {
              throw new Error(message);
            },
          },
          { ...scanOptions, signal: abortController.signal },
        );
        return;
      } catch (streamError) {
        discoveryStreamAbortRef.current = null;
        onStreamingDiscoveryChange(null);
        if (streamError instanceof DOMException && streamError.name === "AbortError") {
          return;
        }
        setFeedback({
          tone: "warning",
          message:
            streamError instanceof Error
              ? `${streamError.message}，已切换到后台扫描；完成后会自动更新结果。`
              : "流式连接中断，已切换到后台扫描；完成后会自动更新结果。",
        });
      }

      const jobId = await startDiscoveryJob(displayableHoldings(holdings), profile, scanOptions);
      onDiscoveryJobIdChange(jobId);
    } catch (scanError) {
      setFeedback({
        tone: "error",
        message: scanError instanceof Error ? scanError.message : "提交失败",
      });
    } finally {
      setIsSubmitting(false);
    }
  }, [
    budgetYuan,
    discoveryPrompt.is_custom,
    discoveryPrompt.role_prompt,
    discoveryStrategy,
    discoveryStreamAbortRef,
    focusSectors,
    holdings,
    onDiscoveryJobIdChange,
    onDiscoveryStreamComplete,
    onDiscoveryStreamStart,
    onStreamingDiscoveryChange,
    profile,
    scanMode,
    refreshReports,
    report,
  ]);

  useEffect(() => {
    onRegisterDiscoveryScanRetry(() => {
      void handleScan();
    });
    return () => onRegisterDiscoveryScanRetry(null);
  }, [handleScan, onRegisterDiscoveryScanRetry]);

  const handleOpenFund = (recommendation: DiscoveryRecommendation) => {
    setPreviewHolding({
      fund_code: recommendation.fund_code,
      fund_name: recommendation.fund_name,
      sector_name: recommendation.sector_name,
      holding_amount: 0,
      return_percent: 0,
      holding_profit: 0,
      holding_return_percent: 0,
    });
  };

  const isRunning = isSubmitting || Boolean(discoveryJobId) || Boolean(streamingDiscovery);
  const reportedScanGoal =
    report?.discovery_facts?.effective_configuration?.scan_goal ??
    report?.discovery_facts?.portfolio_gap?.scan_mode;
  const summaryScanModeLabel =
    reportedScanGoal === "full_market" || reportedScanGoal === "portfolio_gap"
      ? SCAN_MODE_LABELS[reportedScanGoal]
      : report
        ? "历史模式"
        : SCAN_MODE_LABELS[scanMode];
  const summaryAnalysisMode = report?.analysis_mode ?? "deep";
  const reportedDiscoveryStrategy =
    report?.discovery_facts?.effective_configuration?.discovery_strategy;
  const summaryDiscoveryStrategy = report
    ? reportedDiscoveryStrategy === "opportunity_first"
      ? "机会优先（20～60交易日）"
      : reportedDiscoveryStrategy === "risk_first"
        ? "稳健筛选"
        : "历史稳健策略"
    : discoveryStrategy === "opportunity_first"
      ? "机会优先（20～60交易日）"
      : "稳健筛选";
  const summaryFocusSectors = report ? report.focus_sectors : focusSectors;
  const reportedSelectionPolicy =
    report?.discovery_facts?.effective_configuration?.selection_policy ??
    report?.discovery_facts?.selection_strategy;
  const summarySelectionLabel = report
    ? reportedSelectionPolicy === "with_new_issue"
        ? "历史策略：含新发观察"
        : reportedSelectionPolicy === "balanced"
          ? "均衡质量策略"
          : "自动质量优选"
    : "自动质量优选";
  const reportedShareClassPolicy =
    report?.discovery_facts?.effective_configuration?.share_class_policy;
  const reportedFundTypePreference =
    report?.discovery_facts?.effective_configuration?.legacy_fund_type_preference ??
    report?.discovery_facts?.fund_type_preference;
  const summaryShareClassLabel = !report || reportedShareClassPolicy
    ? "同基金份额自动去重（费用待核对）"
    : reportedFundTypePreference === "etf_link"
      ? "历史偏好：ETF联接"
      : reportedFundTypePreference === "no_c_class"
        ? "历史偏好：排除C类"
        : "基金类型不限";
  const configSummary = [
    summaryScanModeLabel,
    summaryDiscoveryStrategy,
    summarySelectionLabel,
    summaryShareClassLabel,
    summaryAnalysisMode === "fast" ? "快速分析" : "深度分析",
    summaryFocusSectors.length ? `关注：${summaryFocusSectors.join("、")}` : "方向：自动筛选",
    !report && budgetYuan.trim() ? `预算：¥${budgetYuan.trim()}` : null,
  ]
    .filter((item): item is string => Boolean(item))
    .join(" · ");

  const selectHistoryReport = useCallback((selected: FundDiscoveryReport) => {
    setReport(selected);
    setConfigExpanded(false);
    window.setTimeout(() => {
      reportRegionRef.current?.focus();
      reportRegionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  }, []);

  const handleHistoryDeleted = useCallback(
    (deletedId: string) => {
      if (report?.id !== deletedId) return;
      const remaining = historyReports.filter((item) => item.id !== deletedId);
      const deletedIndex = historyReports.findIndex((item) => item.id === deletedId);
      const adjacent = remaining[Math.min(Math.max(deletedIndex, 0), remaining.length - 1)] ?? null;
      setReport(adjacent);
    },
    [historyReports, report?.id],
  );

  return (
    <div className="discovery-workspace mx-auto grid min-w-0 max-w-6xl gap-6">
      <div className="flex min-w-0 flex-col gap-4">
        <section className="discovery-composer overflow-hidden">
          <div className="report-control-hero border-b border-[var(--line)] px-4 py-4 sm:px-5">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 items-start gap-3">
                <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[var(--brand-soft)] text-[var(--brand-strong)]">
                  <Target size={20} strokeWidth={2.3} />
                </span>
                <div className="min-w-0">
                  <h2 className="font-display text-lg font-extrabold text-slate-950">发现基金机会</h2>
                  <p className="mt-1 text-sm leading-6 text-slate-600">
                    先找当前机会，再用历史回撤、波动与持仓相关性控制首批金额；没有合格基金时不会凑数。仅供参考，不构成投资建议。
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setHistoryOpen(true)}
                className="discovery-history-trigger min-h-11 shrink-0"
                aria-haspopup="dialog"
                aria-expanded={historyOpen}
                aria-label={`历史推荐${historyReports.length ? `，共 ${historyReports.length} 份` : ""}`}
              >
                <History size={17} />
                <span>历史推荐</span>
                {historyReports.length ? <strong>{historyReports.length}</strong> : null}
              </button>
            </div>
          </div>

          {report && !configExpanded ? (
            <div className="p-4 sm:p-5" data-testid="discovery-config-summary">
              <span id="discovery-scan-settings" hidden />
              <p className="section-eyebrow">当前运行条件</p>
              <p className="mt-2 text-sm font-semibold leading-6 text-slate-700">{configSummary}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setConfigExpanded(true)}
                  aria-expanded={false}
                  aria-controls="discovery-scan-settings"
                  className="btn-secondary min-h-11 px-4 text-sm"
                >
                  调整条件
                </button>
                <button
                  type="button"
                  onClick={() => void handleScan()}
                  disabled={isRunning}
                  className="btn-primary min-h-11 px-4 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isRunning ? "扫描进行中…" : "重新扫描"}
                </button>
              </div>
            </div>
          ) : (
          <div id="discovery-scan-settings" className="flex flex-col p-4 sm:p-5">
          <div className="order-5 mt-4 overflow-hidden rounded-xl border border-[var(--line)]">
            <button
              type="button"
              onClick={() => setShowDiscoveryCustomization((value) => !value)}
              className="flex min-h-11 w-full items-center justify-between gap-3 px-3 text-left"
              aria-expanded={showDiscoveryCustomization}
              aria-controls="discovery-custom-settings"
            >
              <span>
                <span className="block text-xs font-bold text-slate-700">自定义扫描方式</span>
                <span className="mt-0.5 block text-[11px] text-slate-500">
                  默认使用机会优先策略；有明确偏好时再调整
                </span>
              </span>
              <ChevronDown
                size={15}
                className={`shrink-0 text-slate-500 transition ${showDiscoveryCustomization ? "rotate-180" : ""}`}
                aria-hidden
              />
            </button>
            {showDiscoveryCustomization ? (
            <div id="discovery-custom-settings" className="border-t border-[var(--line)] p-3">
            <ol className="discovery-decision-rail !mb-4 !border !border-[var(--line)]" aria-label="基金扫描流程">
              <li className={!isRunning && !report ? "is-current" : "is-done"}><span>01</span>方向与约束</li>
              <li className={isRunning ? "is-current" : report ? "is-done" : ""}><span>02</span>扫描与验证</li>
              <li className={report ? "is-current" : ""}><span>03</span>候选与依据</li>
            </ol>
          <div>
            <div className="mb-2 flex min-h-11 items-center justify-between gap-3">
              <p className="text-[11px] font-bold text-slate-500">荐基决策策略</p>
              {report ? (
                <button
                  type="button"
                  onClick={() => setConfigExpanded(false)}
                  aria-expanded={true}
                  aria-controls="discovery-scan-settings"
                  className="min-h-11 rounded-full px-3 text-xs font-bold text-[var(--brand-strong)] hover:bg-[var(--brand-soft)]"
                >
                  收起条件
                </button>
              ) : null}
            </div>
            <DiscoveryStrategySelector
              value={discoveryStrategy}
              onChange={setDiscoveryStrategy}
            />
          </div>

          <div className="mt-4 overflow-hidden rounded-xl border border-[var(--line)]">
            <div className="flex items-center gap-2 px-2">
              <button
                type="button"
                onClick={() => setRolePromptOpen((current) => !current)}
                className="flex min-h-11 min-w-0 flex-1 items-center justify-between gap-2 rounded-lg px-1 text-left hover:bg-slate-50"
                aria-expanded={rolePromptOpen}
                aria-controls="discovery-role-prompt-settings"
              >
                <span className="flex min-w-0 items-center gap-2">
                  <Sparkles size={15} className="shrink-0 text-[var(--brand)]" />
                  <span className="text-xs font-bold text-slate-700">AI 分析偏好附录（高级）</span>
                  <span className="truncate text-[11px] font-semibold text-slate-500">
                    {discoveryPrompt.is_custom ? "已添加" : "未添加"}
                  </span>
                </span>
                <ChevronDown
                  size={15}
                  className={`shrink-0 text-slate-500 transition ${rolePromptOpen ? "rotate-180" : ""}`}
                  aria-hidden
                />
              </button>
              {rolePromptOpen && discoveryPrompt.is_custom ? (
                <button
                  type="button"
                  onClick={() => {
                    promptChangedByUserRef.current = true;
                    setDiscoveryPrompt((current) => ({
                      ...current,
                      role_prompt: current.default_role_prompt,
                      is_custom: false,
                    }));
                  }}
                  className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 text-[11px] font-bold text-slate-600 transition hover:bg-slate-50"
                >
                  <RotateCcw size={12} />
                  清空附录
                </button>
              ) : null}
            </div>
            {rolePromptOpen ? (
              <div id="discovery-role-prompt-settings" className="border-t border-[var(--line)]">
                <RolePromptEditor
                  value={discoveryPrompt.is_custom ? discoveryPrompt.role_prompt : ""}
                  onChange={(value) => {
                    promptChangedByUserRef.current = true;
                    setDiscoveryPrompt((current) => ({
                      ...current,
                      role_prompt: value,
                      is_custom: Boolean(value.trim()),
                    }));
                  }}
                />
              </div>
            ) : (
              <p className="border-t border-[var(--line)] px-3 py-2 text-[11px] leading-5 text-slate-500">
                普通扫描无需填写；附录只能补充表达风格和关注角度，不能修改系统决策约束。
              </p>
            )}
          </div>
          </div>
            ) : null}
          </div>

          <fieldset className="order-1">
            <legend className="mb-2 text-xs font-semibold text-slate-700">推荐目标</legend>
            <div className="flex flex-wrap gap-2">
              {PRIMARY_SCAN_MODE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  title={option.hint}
                  onClick={() => setScanMode(option.id)}
                  aria-pressed={scanMode === option.id}
                  aria-describedby="discovery-scan-mode-hint"
                  className={`chip-btn min-h-11 ${scanMode === option.id ? "chip-btn-active" : ""}`}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <p id="discovery-scan-mode-hint" className="mt-1.5 text-[11px] leading-5 text-slate-500">
              {PRIMARY_SCAN_MODE_OPTIONS.find((item) => item.id === scanMode)?.hint}
            </p>
          </fieldset>

          <div className="order-2 mt-4">
            <div className="mb-2 text-xs font-semibold text-slate-700">
              关注方向（可选，最多 3 个）
            </div>
            <FocusSectorPicker
              selected={focusSectors}
              onChange={handleFocusSectorsChange}
              allLabels={allSectorLabels}
              heatRows={rawSectors}
              loading={loadingSectors && allSectorLabels.length === 0}
              error={sectorsError}
              onRetry={() => void refreshSectors()}
            />
            {loadingSectors && rawSectors.length === 0 ? (
              <p className="mt-2 flex items-center gap-2 text-[11px] text-slate-500">
                <Loader2 size={12} className="animate-spin" />
                同步板块热度…
              </p>
            ) : null}
            {sectorsError && rawSectors.length === 0 ? (
              <p className="mt-2 text-[11px] text-amber-700">
                板块热度暂不可用，仍可搜索选择关注方向。
                <button
                  type="button"
                  onClick={() => void refreshSectors()}
                  className="ml-1 inline-flex min-h-11 items-center rounded-lg px-2 font-semibold underline"
                >
                  重试
                </button>
              </p>
            ) : null}
          </div>

          {showDiscoveryCustomization ? (
          <div className="order-5 mt-3 rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2.5">
            <p className="text-xs font-black text-slate-800">系统自动选基</p>
            <p className="mt-1 text-[11px] leading-5 text-slate-500">
              自动核验申购状态、首次起购额与单日限额；费用可得时按未折扣标准费率估算上限，下单前仍需复核。
            </p>
          </div>
          ) : null}

          <div className="order-3 mt-4 max-w-md">
            <label className="block text-xs font-semibold text-slate-700">
              本次可投入预算（元）
              <input
                type="number"
                min={0}
                step={500}
                value={budgetYuan}
                onChange={(event) => {
                  budgetChangedByUserRef.current = true;
                  setBudgetYuan(event.target.value);
                }}
                placeholder="按计划投入余额自动计算"
                className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-[var(--brand)]"
              />
              <span className="mt-1 block text-[11px] font-normal leading-5 text-slate-500">
                默认按计划投入总额减当前持仓动态计算；手工修改后，本次扫描保留你的输入。
              </span>
            </label>
          </div>

          <button
            type="button"
            data-testid="discovery-scan-button"
            disabled={isRunning}
            onClick={() => void handleScan()}
            className="btn-primary order-4 mt-4 min-h-11 w-full !rounded-xl sm:w-auto"
          >
            {isRunning ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Sparkles size={16} />
            )}
            {isRunning ? "扫描进行中…" : report ? "按当前条件重新扫描" : "扫描今日机会"}
          </button>
          </div>
          )}
        </section>

        {feedback ? (
          <InlineNotice
            tone={feedback.tone}
            message={feedback.message}
            onDismiss={() => setFeedback(null)}
          />
        ) : null}

        {streamingDiscovery ? (
          <DiscoverySkeleton streaming={streamingDiscovery} onCancel={handleCancelStream} />
        ) : null}

        {report && streamingDiscovery ? (
          <InlineNotice tone="info" message="新扫描正在进行，下方继续显示上次报告，完成后会自动替换。" />
        ) : null}

        {report ? (
          <div
            ref={reportRegionRef}
            tabIndex={-1}
            aria-label="推荐报告阅读区"
            className="scroll-mt-24 outline-none"
          >
            <DiscoveryReportPanel report={report} onOpenFund={handleOpenFund} />
          </div>
        ) : null}
      </div>

      <DiscoveryHistoryWorkspace
        reports={historyReports}
        activeReportId={report?.id}
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onRefresh={() => void refreshReports()}
        onSelect={(selected) => selectHistoryReport(selected)}
        onDeleted={handleHistoryDeleted}
      />

      {previewHolding ? (
        <YangjibaoFundDetail
          holding={previewHolding}
          holdingIndex={0}
          holdings={[previewHolding]}
          onClose={() => setPreviewHolding(null)}
          onNavigate={() => undefined}
        />
      ) : null}
    </div>
  );
}
