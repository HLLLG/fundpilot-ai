"use client";

import type {
  Dispatch,
  MutableRefObject,
  SetStateAction,
} from "react";
import { ChevronDown, Loader2, RotateCcw, Sparkles, Target } from "lucide-react";
import type {
  AnalysisMode,
  DiscoveryPromptConfig,
  DiscoveryRecommendation,
  DiscoverySectorHeat,
  FundDiscoveryReport,
  FundTypePreference,
  Holding,
  InvestorProfile,
  SelectionStrategy,
  DiscoveryScanMode,
} from "@/lib/api";
import {
  fetchDiscoveryPrompt,
  fetchDiscoverySectors,
  listDiscoveryReports,
  saveDiscoveryPromptRemote,
  startDiscoveryJob,
} from "@/lib/api";
import { DiscoveryHistoryRail } from "@/components/DiscoveryHistoryRail";
import { InlineNotice, type NoticeTone } from "@/components/InlineNotice";
import { DiscoveryReportPanel } from "@/components/DiscoveryReportPanel";
import { DiscoverySkeleton } from "@/components/DiscoverySkeleton";
import { FocusSectorPicker } from "@/components/FocusSectorPicker";
import { InvestmentPresetSelector } from "@/components/InvestmentPresetSelector";
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
import { applyInvestmentPreset } from "@/lib/investmentPresets";
import { ensureNotificationPermission } from "@/lib/notifications";
import { loadDiscoveryPrompt, loadDiscoverySectorHeatCache, saveDiscoveryPrompt, saveDiscoverySectorHeatCache } from "@/lib/storage";
import { useCachedFetch } from "@/lib/useCachedFetch";
import { buildClientCacheKey } from "@/lib/clientCache";
import {
  DISCOVERY_FOCUS_CHANGED_EVENT,
  loadDiscoveryFocusSectors,
  setDiscoveryFocusSectors,
} from "@/lib/discoveryFocusSectors";

const DISCOVERY_PREFILL_KEY = "fundpilot-discovery-prefill";
const DISCOVERY_SECTORS_CACHE_KEY = "discovery-panel:sectors";
const DISCOVERY_REPORTS_CACHE_KEY = "discovery-panel:reports";
const DISCOVERY_SECTORS_STALE_MS = 30 * 60 * 1000;
const DISCOVERY_REPORTS_STALE_MS = 2 * 60 * 1000;
const DEFAULT_DISCOVERY_PROMPT: DiscoveryPromptConfig = {
  role_prompt: "",
  default_role_prompt: "",
  is_custom: false,
};

type DiscoveryPrefill = {
  scanMode?: DiscoveryScanMode;
  focusSectors?: string[];
};

type DiscoveryFeedback = {
  tone: NoticeTone;
  message: string;
};

const SCAN_MODE_OPTIONS: { id: DiscoveryScanMode; label: string; hint: string }[] = [
  { id: "full_market", label: "全市场机会", hint: "多板块横向对比，不限于持仓缺口" },
  { id: "portfolio_gap", label: "持仓缺口补充", hint: "优先未重仓、热度靠前的缺口板块" },
  { id: "dip_swing", label: "短线抄底", hint: "近几日大跌、有反弹信号；默认 2～5 天波段" },
];

const FUND_TYPE_OPTIONS: { id: FundTypePreference; label: string }[] = [
  { id: "any", label: "不限" },
  { id: "etf_link", label: "ETF联接优先" },
  { id: "no_c_class", label: "排除C类" },
];

const SELECTION_STRATEGY_OPTIONS: { id: SelectionStrategy; label: string; hint: string }[] = [
  { id: "balanced", label: "均衡潜力", hint: "近3~6月走强、避免追年度冠军" },
  { id: "with_new_issue", label: "含新发观察", hint: "混入近6月新发 + 均衡老基" },
  { id: "dip_rebound", label: "跌深反弹", hint: "近5日回调较深、距高点有反弹空间" },
];

type FundDiscoveryPanelProps = {
  userId: number | null;
  holdings: Holding[];
  profile: InvestorProfile;
  onProfileChange: (profile: InvestorProfile) => void;
  analysisMode: AnalysisMode;
  onAnalysisModeChange: (mode: AnalysisMode) => void;
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
  onProfileChange,
  analysisMode,
  onAnalysisModeChange,
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
  const historyReports = historyReportsData ?? [];

  const [focusSectors, setFocusSectors] = useState<string[]>(() => loadDiscoveryFocusSectors());
  const [fundTypePreference, setFundTypePreference] = useState<FundTypePreference>("any");
  const [selectionStrategy, setSelectionStrategy] = useState<SelectionStrategy>("balanced");
  const [scanMode, setScanMode] = useState<DiscoveryScanMode>("full_market");
  const [dipLookbackDays, setDipLookbackDays] = useState<3 | 5>(5);
  const [dipMinDropPercent, setDipMinDropPercent] = useState<3 | 5>(3);
  const [dipAdvancedOpen, setDipAdvancedOpen] = useState(false);
  const [budgetYuan, setBudgetYuan] = useState<string>("");
  const [report, setReport] = useState<FundDiscoveryReport | null>(null);
  const [discoveryPrompt, setDiscoveryPrompt] = useState<DiscoveryPromptConfig>(() =>
    loadDiscoveryPrompt(userId, DEFAULT_DISCOVERY_PROMPT),
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<DiscoveryFeedback | null>(null);
  const [configExpanded, setConfigExpanded] = useState(true);
  const [rolePromptOpen, setRolePromptOpen] = useState(false);
  const [previewHolding, setPreviewHolding] = useState<Holding | null>(null);
  const promptPersistReady = useRef(false);
  const promptChangedByUserRef = useRef(false);
  const [promptReady, setPromptReady] = useState(false);

  useEffect(() => {
    if (rawSectors.length > 0) {
      saveDiscoverySectorHeatCache(rawSectors);
    }
  }, [rawSectors]);

  useEffect(() => {
    if (scanMode === "dip_swing") {
      setSelectionStrategy("dip_rebound");
    }
  }, [scanMode]);

  useEffect(() => {
    try {
      const raw = window.sessionStorage.getItem(DISCOVERY_PREFILL_KEY);
      if (raw) {
        window.sessionStorage.removeItem(DISCOVERY_PREFILL_KEY);
        const prefill = JSON.parse(raw) as DiscoveryPrefill;
        if (prefill.scanMode) {
          setScanMode(prefill.scanMode);
        }
        if (prefill.focusSectors?.length) {
          setFocusSectors(prefill.focusSectors.slice(0, 3));
        }
      } else {
        const labels = loadDiscoveryFocusSectors();
        if (labels.length) {
          setFocusSectors(labels);
        }
      }
    } catch {
      // ignore malformed prefill
    }
  }, []);

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

  const dipDeepSectors = useMemo(() => {
    return [...rawSectors]
      .filter((row) => row.change_5d_percent != null)
      .sort((a, b) => (a.change_5d_percent ?? 0) - (b.change_5d_percent ?? 0))
      .slice(0, 5);
  }, [rawSectors]);

  const isAggressiveProfile = profile.decision_style === "aggressive";

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

  const toggleSector = (label: string) => {
    if (focusSectors.includes(label)) {
      handleFocusSectorsChange(focusSectors.filter((item) => item !== label));
      return;
    }
    if (focusSectors.length >= 3) {
      return;
    }
    handleFocusSectorsChange([...focusSectors, label]);
  };

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
      analysisMode,
      focusSectors,
      budgetYuan: parsedBudget && !Number.isNaN(parsedBudget) ? parsedBudget : null,
      fundTypePreference,
      selectionStrategy,
      scanMode,
      dipLookbackDays: scanMode === "dip_swing" ? dipLookbackDays : undefined,
      dipMinDropPercent: scanMode === "dip_swing" ? dipMinDropPercent : undefined,
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
    analysisMode,
    budgetYuan,
    discoveryPrompt.is_custom,
    discoveryPrompt.role_prompt,
    discoveryStreamAbortRef,
    focusSectors,
    fundTypePreference,
    holdings,
    onDiscoveryJobIdChange,
    onDiscoveryStreamComplete,
    onDiscoveryStreamStart,
    onStreamingDiscoveryChange,
    profile,
    dipLookbackDays,
    dipMinDropPercent,
    scanMode,
    selectionStrategy,
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
  const scanModeLabel = SCAN_MODE_OPTIONS.find((item) => item.id === scanMode)?.label ?? "全市场机会";
  const strategyLabel =
    SELECTION_STRATEGY_OPTIONS.find((item) => item.id === selectionStrategy)?.label ?? "均衡潜力";
  const fundTypeLabel =
    FUND_TYPE_OPTIONS.find((item) => item.id === fundTypePreference)?.label ?? "不限";
  const configSummary = [
    scanModeLabel,
    strategyLabel,
    `基金类型：${fundTypeLabel}`,
    analysisMode === "fast" ? "快速分析" : "深度分析",
    focusSectors.length ? `关注：${focusSectors.join("、")}` : "方向：自动筛选",
    budgetYuan.trim() ? `预算：¥${budgetYuan.trim()}` : null,
  ]
    .filter((item): item is string => Boolean(item))
    .join(" · ");

  return (
    <div className="mx-auto grid min-w-0 max-w-3xl gap-6 xl:max-w-6xl xl:grid-cols-[minmax(0,1fr)_280px]">
      <div className="flex min-w-0 flex-col gap-4">
        <section className="section-card overflow-hidden">
          <div className="report-control-hero border-b border-[var(--line)] px-4 py-4 sm:px-5">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[var(--brand-soft)] text-[var(--brand-strong)]">
                <Target size={20} strokeWidth={2.3} />
              </span>
              <div>
                <h2 className="font-display text-lg font-extrabold text-slate-950">发现基金机会</h2>
                <p className="mt-1 text-sm leading-6 text-slate-600">
                  从全市场板块热度中筛选候选，AI 精选 3～5 只值得关注的机会。仅供参考，不构成投资建议。
                </p>
              </div>
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
                  className="min-h-11 rounded-full border border-[var(--brand)] bg-white px-4 text-sm font-bold text-[var(--brand-strong)] hover:bg-[var(--brand-soft)]"
                >
                  调整条件
                </button>
                <button
                  type="button"
                  onClick={() => void handleScan()}
                  disabled={isRunning}
                  className="min-h-11 rounded-full bg-[var(--brand-strong)] px-4 text-sm font-bold text-white hover:bg-[var(--brand-deep)] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isRunning ? "扫描进行中…" : "重新扫描"}
                </button>
              </div>
            </div>
          ) : (
          <div id="discovery-scan-settings" className="p-4 sm:p-5">
          <div>
            <div className="mb-2 flex min-h-11 items-center justify-between gap-3">
              <p className="text-[11px] font-bold text-slate-500">投资风格预设</p>
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
            <InvestmentPresetSelector profile={profile} onChange={onProfileChange} compact />
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
                  <span className="text-xs font-bold text-slate-700">AI 角色设定（高级）</span>
                  <span className="truncate text-[11px] font-semibold text-slate-500">
                    {discoveryPrompt.is_custom ? "已自定义" : "默认模板"}
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
                  恢复默认
                </button>
              ) : null}
            </div>
            {rolePromptOpen ? (
              <div id="discovery-role-prompt-settings" className="border-t border-[var(--line)]">
                <RolePromptEditor
                  value={discoveryPrompt.role_prompt}
                  onChange={(value) => {
                    promptChangedByUserRef.current = true;
                    setDiscoveryPrompt((current) => ({
                      ...current,
                      role_prompt: value,
                      is_custom: value.trim() !== current.default_role_prompt.trim(),
                    }));
                  }}
                />
              </div>
            ) : (
              <p className="border-t border-[var(--line)] px-3 py-2 text-[11px] leading-5 text-slate-500">
                普通扫描无需调整；仅在需要固定特殊研究方法时展开编辑。
              </p>
            )}
          </div>

          <fieldset className="mt-4">
            <legend className="mb-2 text-xs font-semibold text-slate-700">扫描模式</legend>
            <div className="flex flex-wrap gap-2">
              {SCAN_MODE_OPTIONS.map((option) => (
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
              {SCAN_MODE_OPTIONS.find((item) => item.id === scanMode)?.hint}
            </p>
          </fieldset>

          {scanMode === "dip_swing" && !isAggressiveProfile ? (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50/80 px-3 py-2.5">
              <p className="text-xs font-semibold leading-5 text-rose-900">
                短线抄底更适合「激进波段」预设（3～7 天、扣费后止盈）。
              </p>
              <button
                type="button"
                onClick={() => onProfileChange(applyInvestmentPreset("aggressive_swing", profile))}
                className="min-h-11 shrink-0 rounded-full border border-rose-300 bg-white px-3 text-xs font-bold text-rose-800 transition hover:bg-rose-100"
              >
                切换激进波段
              </button>
            </div>
          ) : null}

          {scanMode === "dip_swing" ? (
            <div className="mt-4 overflow-hidden rounded-xl border border-slate-100">
              <button
                type="button"
                onClick={() => setDipAdvancedOpen((value) => !value)}
                aria-expanded={dipAdvancedOpen}
                aria-controls="discovery-dip-advanced"
                className="flex min-h-11 w-full items-center justify-between gap-2 px-3 text-left text-xs font-bold text-slate-600 hover:bg-slate-50"
              >
                <span>抄底筛选（高级）</span>
                <ChevronDown
                  size={14}
                  className={`shrink-0 transition ${dipAdvancedOpen ? "rotate-180" : ""}`}
                />
              </button>
              <div id="discovery-dip-advanced">
                {dipAdvancedOpen ? (
                <div className="grid gap-3 border-t border-slate-100 p-3 sm:grid-cols-2">
                  <fieldset>
                    <legend className="mb-2 text-[11px] font-bold text-slate-500">回看天数</legend>
                    <div className="flex flex-wrap gap-2">
                      {([3, 5] as const).map((days) => (
                        <button
                          key={days}
                          type="button"
                          onClick={() => setDipLookbackDays(days)}
                          aria-pressed={dipLookbackDays === days}
                          className={`min-h-11 rounded-full border px-3 text-xs font-medium transition ${
                            dipLookbackDays === days
                              ? "border-[var(--brand)] bg-[var(--brand)] text-white"
                              : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                          }`}
                        >
                          {days} 日
                        </button>
                      ))}
                    </div>
                  </fieldset>
                  <fieldset>
                    <legend className="mb-2 text-[11px] font-bold text-slate-500">最小跌幅</legend>
                    <div className="flex flex-wrap gap-2">
                      {([3, 5] as const).map((pct) => (
                        <button
                          key={pct}
                          type="button"
                          onClick={() => setDipMinDropPercent(pct)}
                          aria-pressed={dipMinDropPercent === pct}
                          className={`min-h-11 rounded-full border px-3 text-xs font-medium transition ${
                            dipMinDropPercent === pct
                              ? "border-[var(--brand)] bg-[var(--brand)] text-white"
                              : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                          }`}
                        >
                          ≥ {pct}%
                        </button>
                      ))}
                    </div>
                  </fieldset>
                </div>
              ) : (
                <p className="border-t border-slate-100 px-3 py-2 text-[11px] leading-5 text-slate-500">
                  回看 {dipLookbackDays} 日、板块跌幅 ≥ {dipMinDropPercent}%
                </p>
                )}
              </div>
            </div>
          ) : null}

          <div className="mt-4">
            <div className="mb-2 text-xs font-semibold text-slate-700">
              关注方向（可选，最多 3 个）
            </div>
            {scanMode === "dip_swing" && dipDeepSectors.length > 0 ? (
              <div className="mb-3">
                <p className="mb-2 text-[11px] font-bold text-rose-600">今日跌深板块</p>
                <div className="flex flex-wrap gap-2">
                  {dipDeepSectors.map((sector) => {
                    const selected = focusSectors.includes(sector.sector_label);
                    const change5d = sector.change_5d_percent;
                    return (
                      <button
                        key={`dip-${sector.sector_label}`}
                        type="button"
                        onClick={() => toggleSector(sector.sector_label)}
                        aria-pressed={selected}
                        className={`min-h-11 rounded-full border px-3 text-xs font-medium transition ${
                          selected
                            ? "border-rose-700 bg-rose-700 text-white"
                            : "border-rose-200 bg-rose-50 text-rose-900 hover:bg-rose-100"
                        }`}
                      >
                        {sector.sector_label}
                        {change5d != null ? (
                          <span className={selected ? "text-rose-100" : "text-rose-700"}>
                            {" "}
                            {change5d >= 0 ? "+" : ""}
                            {change5d.toFixed(2)}%
                          </span>
                        ) : null}
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : null}
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

          <fieldset className="mt-4">
            <legend className="mb-2 text-xs font-semibold text-slate-700">选基策略</legend>
            <div className="flex flex-wrap gap-2">
              {SELECTION_STRATEGY_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  title={option.hint}
                  onClick={() => setSelectionStrategy(option.id)}
                  aria-pressed={selectionStrategy === option.id}
                  aria-describedby="discovery-selection-strategy-hint"
                  className={`chip-btn min-h-11 ${selectionStrategy === option.id ? "chip-btn-active" : ""}`}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <p id="discovery-selection-strategy-hint" className="mt-1.5 text-[11px] leading-5 text-slate-500">
              {SELECTION_STRATEGY_OPTIONS.find((item) => item.id === selectionStrategy)?.hint}
            </p>
          </fieldset>

          <fieldset className="mt-4">
            <legend className="mb-2 text-xs font-semibold text-slate-700">基金类型偏好</legend>
            <div className="flex flex-wrap gap-2">
              {FUND_TYPE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setFundTypePreference(option.id)}
                  aria-pressed={fundTypePreference === option.id}
                  className={`chip-btn min-h-11 ${fundTypePreference === option.id ? "chip-btn-active" : ""}`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </fieldset>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="block text-xs font-semibold text-slate-700">
              本次可投入预算（元，可选）
              <input
                type="number"
                min={0}
                step={500}
                value={budgetYuan}
                onChange={(event) => setBudgetYuan(event.target.value)}
                placeholder="默认按期望投入余额"
                className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-[var(--brand)]"
              />
            </label>
            <fieldset>
              <legend className="text-xs font-semibold text-slate-700">分析模式</legend>
              <div className="mt-1 flex rounded-xl border border-slate-200 p-1">
                {(["fast", "deep"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => onAnalysisModeChange(mode)}
                    aria-pressed={analysisMode === mode}
                    className={`min-h-11 flex-1 rounded-lg px-3 py-2 text-xs font-bold ${
                      analysisMode === mode ? "bg-slate-900 text-white" : "text-slate-600"
                    }`}
                  >
                    {mode === "fast" ? "快速" : "深度"}
                  </button>
                ))}
              </div>
            </fieldset>
          </div>

          <button
            type="button"
            data-testid="discovery-scan-button"
            disabled={isRunning}
            onClick={() => void handleScan()}
            className="btn-primary mt-4 min-h-11 w-full !rounded-xl sm:w-auto"
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
          <DiscoveryReportPanel report={report} onOpenFund={handleOpenFund} />
        ) : null}
      </div>

      <DiscoveryHistoryRail
        reports={historyReports}
        activeReportId={report?.id}
        onRefresh={() => void refreshReports()}
        onSelect={(selected) => {
          setReport(selected);
          setConfigExpanded(false);
        }}
        onDeleted={(reportId) => {
          if (report?.id === reportId) {
            setReport(null);
          }
        }}
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
