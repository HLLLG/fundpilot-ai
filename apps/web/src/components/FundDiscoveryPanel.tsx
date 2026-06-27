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

type DiscoveryPrefill = {
  scanMode?: DiscoveryScanMode;
  focusSectors?: string[];
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
    cacheKey: DISCOVERY_REPORTS_CACHE_KEY,
    fetcher: listDiscoveryReports,
    staleTimeMs: DISCOVERY_REPORTS_STALE_MS,
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
    loadDiscoveryPrompt({
      role_prompt: "",
      default_role_prompt: "",
    }),
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
    void (async () => {
      try {
        const remote = await fetchDiscoveryPrompt();
        setDiscoveryPrompt(remote);
        saveDiscoveryPrompt(remote);
      } catch {
        setDiscoveryPrompt((current) => loadDiscoveryPrompt(current));
      } finally {
        promptPersistReady.current = true;
        setPromptReady(true);
      }
    })();
  }, []);

  useEffect(() => {
    if (!promptReady || !promptPersistReady.current) return;
    saveDiscoveryPrompt(discoveryPrompt);
    if (!promptChangedByUserRef.current) return;
    const storedValue = discoveryPrompt.is_custom ? discoveryPrompt.role_prompt : null;
    void saveDiscoveryPromptRemote(storedValue).catch(() => {
      // 离线时仍保留 localStorage
    });
  }, [discoveryPrompt, promptReady]);

  useEffect(() => {
    if (!pendingDiscoveryReport) return;
    setReport(pendingDiscoveryReport);
    void refreshReports();
    onPendingDiscoveryReportApplied();
  }, [pendingDiscoveryReport, refreshReports, onPendingDiscoveryReportApplied]);

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
    setError("已停止扫描。");
  }, [discoveryStreamAbortRef, onStreamingDiscoveryChange]);

  const handleScan = useCallback(async () => {
    setIsSubmitting(true);
    setError(null);
    setReport(null);
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
        setError(
          streamError instanceof Error
            ? `${streamError.message}，已切换到后台扫描。`
            : "流式扫描失败，已切换到后台扫描。",
        );
      }

      const jobId = await startDiscoveryJob(displayableHoldings(holdings), profile, scanOptions);
      onDiscoveryJobIdChange(jobId);
    } catch (scanError) {
      setError(scanError instanceof Error ? scanError.message : "提交失败");
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

  return (
    <div className="mx-auto grid min-w-0 max-w-3xl gap-6 xl:max-w-6xl xl:grid-cols-[minmax(0,1fr)_280px]">
      <div className="grid min-w-0 gap-4">
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

          <div className="p-4 sm:p-5">
          <div>
            <p className="mb-2 text-[11px] font-bold text-slate-400">投资风格预设</p>
            <InvestmentPresetSelector profile={profile} onChange={onProfileChange} compact />
          </div>

          <div className="mt-4 overflow-hidden rounded-xl border border-[var(--line)]">
            <div className="flex items-center justify-between gap-2 border-b border-[var(--line)] px-3 py-2.5">
              <div className="flex items-center gap-2">
                <Sparkles size={15} className="text-[var(--brand)]" />
                <span className="text-xs font-bold text-slate-700">AI 角色设定</span>
              </div>
              {discoveryPrompt.is_custom ? (
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
                  className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] font-bold text-slate-600 transition hover:bg-slate-50"
                >
                  <RotateCcw size={12} />
                  恢复默认
                </button>
              ) : (
                <span className="text-[11px] font-semibold text-slate-400">默认模板</span>
              )}
            </div>
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

          <div className="mt-4">
            <div className="mb-2 text-xs font-semibold text-slate-700">扫描模式</div>
            <div className="flex flex-wrap gap-2">
              {SCAN_MODE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  title={option.hint}
                  onClick={() => setScanMode(option.id)}
                  className={`chip-btn ${scanMode === option.id ? "chip-btn-active" : ""}`}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] leading-5 text-slate-500">
              {SCAN_MODE_OPTIONS.find((item) => item.id === scanMode)?.hint}
            </p>
          </div>

          {scanMode === "dip_swing" && !isAggressiveProfile ? (
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50/80 px-3 py-2.5">
              <p className="text-xs font-semibold leading-5 text-rose-900">
                短线抄底更适合「激进波段」预设（3～7 天、扣费后止盈）。
              </p>
              <button
                type="button"
                onClick={() => onProfileChange(applyInvestmentPreset("aggressive_swing", profile))}
                className="shrink-0 rounded-full border border-rose-300 bg-white px-3 py-1.5 text-xs font-bold text-rose-800 transition hover:bg-rose-100"
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
                className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left text-xs font-bold text-slate-600 hover:bg-slate-50"
              >
                <span>抄底筛选（高级）</span>
                <ChevronDown
                  size={14}
                  className={`shrink-0 transition ${dipAdvancedOpen ? "rotate-180" : ""}`}
                />
              </button>
              {dipAdvancedOpen ? (
                <div className="grid gap-3 border-t border-slate-100 p-3 sm:grid-cols-2">
                  <div>
                    <p className="mb-2 text-[11px] font-bold text-slate-400">回看天数</p>
                    <div className="flex flex-wrap gap-2">
                      {([3, 5] as const).map((days) => (
                        <button
                          key={days}
                          type="button"
                          onClick={() => setDipLookbackDays(days)}
                          className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                            dipLookbackDays === days
                              ? "border-[var(--brand)] bg-[var(--brand)] text-white"
                              : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                          }`}
                        >
                          {days} 日
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="mb-2 text-[11px] font-bold text-slate-400">最小跌幅</p>
                    <div className="flex flex-wrap gap-2">
                      {([3, 5] as const).map((pct) => (
                        <button
                          key={pct}
                          type="button"
                          onClick={() => setDipMinDropPercent(pct)}
                          className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                            dipMinDropPercent === pct
                              ? "border-[var(--brand)] bg-[var(--brand)] text-white"
                              : "border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100"
                          }`}
                        >
                          ≥ {pct}%
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <p className="border-t border-slate-100 px-3 py-2 text-[11px] leading-5 text-slate-500">
                  回看 {dipLookbackDays} 日、板块跌幅 ≥ {dipMinDropPercent}%
                </p>
              )}
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
                        className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                          selected
                            ? "border-rose-500 bg-rose-500 text-white"
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
              <p className="mt-2 flex items-center gap-2 text-[11px] text-slate-400">
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
                  className="ml-1 font-semibold underline"
                >
                  重试
                </button>
              </p>
            ) : null}
          </div>

          <div className="mt-4">
            <div className="mb-2 text-xs font-semibold text-slate-700">选基策略</div>
            <div className="flex flex-wrap gap-2">
              {SELECTION_STRATEGY_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  title={option.hint}
                  onClick={() => setSelectionStrategy(option.id)}
                  className={`chip-btn ${selectionStrategy === option.id ? "chip-btn-active" : ""}`}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] leading-5 text-slate-500">
              {SELECTION_STRATEGY_OPTIONS.find((item) => item.id === selectionStrategy)?.hint}
            </p>
          </div>

          <div className="mt-4">
            <div className="mb-2 text-xs font-semibold text-slate-700">基金类型偏好</div>
            <div className="flex flex-wrap gap-2">
              {FUND_TYPE_OPTIONS.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setFundTypePreference(option.id)}
                  className={`chip-btn ${fundTypePreference === option.id ? "chip-btn-active" : ""}`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

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
                className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-[var(--brand)]"
              />
            </label>
            <div>
              <div className="text-xs font-semibold text-slate-700">分析模式</div>
              <div className="mt-1 flex rounded-xl border border-slate-200 p-1">
                {(["fast", "deep"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => onAnalysisModeChange(mode)}
                    className={`flex-1 rounded-lg px-3 py-2 text-xs font-bold ${
                      analysisMode === mode ? "bg-slate-900 text-white" : "text-slate-600"
                    }`}
                  >
                    {mode === "fast" ? "快速" : "深度"}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {error ? <p className="mt-3 text-sm text-red-600">{error}</p> : null}

          <button
            type="button"
            data-testid="discovery-scan-button"
            disabled={isSubmitting || Boolean(discoveryJobId) || Boolean(streamingDiscovery)}
            onClick={() => void handleScan()}
            className="btn-primary mt-4 w-full !rounded-xl sm:w-auto"
          >
            {isSubmitting || discoveryJobId || streamingDiscovery ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Sparkles size={16} />
            )}
            {discoveryJobId || streamingDiscovery ? "扫描进行中…" : "扫描今日机会"}
          </button>
          </div>
        </section>

        {streamingDiscovery ? (
          <DiscoverySkeleton streaming={streamingDiscovery} onCancel={handleCancelStream} />
        ) : null}

        {report && !streamingDiscovery ? (
          <DiscoveryReportPanel report={report} onOpenFund={handleOpenFund} />
        ) : null}
      </div>

      <DiscoveryHistoryRail
        reports={historyReports}
        activeReportId={report?.id}
        onRefresh={() => void refreshReports()}
        onSelect={(selected) => setReport(selected)}
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
