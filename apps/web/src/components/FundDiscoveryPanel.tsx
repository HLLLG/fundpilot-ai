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
  fetchDiscoveryReportDetail,
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
import { MethodologyNote } from "@/components/MethodologyNote";
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
import { userFacingErrorMessage } from "@/lib/userFacingError";
import { startVisibilityAwarePolling } from "@/lib/visibilityPolling";
import {
  DISCOVERY_RECOVERY_POLL_MS,
  detectCompletedScan,
  sortReportsByCreatedAtDesc,
  streamLooksDead,
} from "@/lib/discoveryScanRecovery";
import {
  deleteDiscoveryReportDetailCache,
  isDiscoveryReportDetailCacheFresh,
  readDiscoveryReportDetailCache,
  readFreshLatestDiscoveryReport,
  writeDiscoveryReportDetailCache,
} from "@/lib/discoveryReportCache";

const DISCOVERY_SECTORS_CACHE_KEY = "discovery-panel:sectors";
const DISCOVERY_REPORTS_CACHE_KEY = "discovery-panel:reports";
const DISCOVERY_SECTORS_STALE_MS = 30 * 60 * 1000;
const DISCOVERY_REPORTS_STALE_MS = 30 * 60 * 1000;
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

// 推荐目标固定为「市场优选」，「组合补缺」已下线，所以不再有可选项。
// 标签表保留是为了正确回显历史报告里记录的 scan_goal。
const SCAN_MODE: DiscoveryScanMode = "full_market";
const DISCOVERY_STRATEGY: DiscoveryStrategy = "opportunity_first";

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
    () => sortReportsByCreatedAtDesc(historyReportsData ?? []),
    [historyReportsData],
  );

  const [focusSectors, setFocusSectors] = useState<string[]>(() => loadDiscoveryFocusSectors());
  const dynamicBudgetYuan = useMemo(
    () => resolveDynamicDiscoveryBudgetYuan(holdings, profile.expected_investment_amount),
    [holdings, profile.expected_investment_amount],
  );
  const [budgetYuan, setBudgetYuan] = useState<string>(() =>
    formatBudgetInput(dynamicBudgetYuan),
  );
  const budgetChangedByUserRef = useRef(false);
  const budgetUserRef = useRef(userId);
  const [report, setReport] = useState<FundDiscoveryReport | null>(() =>
    readFreshLatestDiscoveryReport(userId),
  );
  // 历史列表接口只返回摘要字段，点击某份报告时按 id 拉一次完整详情。
  // 通过递增的 request id 保证快速连点时只应用最后一次的详情。
  const historyDetailRequestId = useRef(0);
  const [historyDetailError, setHistoryDetailError] = useState<string | null>(null);
  /** 打开页面时自动载入最近一份报告，每个账号只做一次。 */
  const autoLoadedLatestForUserRef = useRef<number | null>(null);
  /** 扫描开始那一刻最新报告的 id，用于判断后台是否已经又写了一份新的。 */
  const latestKnownReportIdRef = useRef<string | null>(null);
  /** 流最近一次有动静的时间；用来识别"连接已死但状态还挂着"。 */
  const lastStreamActivityRef = useRef(0);
  const [discoveryPrompt, setDiscoveryPrompt] = useState<DiscoveryPromptConfig>(() =>
    loadDiscoveryPrompt(userId, DEFAULT_DISCOVERY_PROMPT),
  );
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [feedback, setFeedback] = useState<DiscoveryFeedback | null>(null);
  const [configExpanded, setConfigExpanded] = useState(true);
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
    writeDiscoveryReportDetailCache(userId, pendingDiscoveryReport, { asLatest: true });
    setReport(pendingDiscoveryReport);
    setFeedback(null);
    void refreshReports();
    onPendingDiscoveryReportApplied();
  }, [pendingDiscoveryReport, refreshReports, onPendingDiscoveryReportApplied, userId]);

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
    // 也要清掉后台任务 id：`isRunning` 把它算在内，只清流式状态的话按钮仍是
    // 「扫描进行中…」的禁用态，用户依旧走不出去。
    onDiscoveryJobIdChange(null);
    setIsSubmitting(false);
    setFeedback({
      tone: "info",
      message: "已停止扫描，当前条件与页面中的已有结果均已保留。",
    });
  }, [discoveryStreamAbortRef, onDiscoveryJobIdChange, onStreamingDiscoveryChange]);

  const handleScan = useCallback(async () => {
    setIsSubmitting(true);
    setFeedback(null);
    if (report) {
      setConfigExpanded(false);
    }
    // 记下"开扫之前最新的一份是哪个"。流断了之后就靠它判断后台有没有产出新报告，
    // 比对时间戳可靠：created_at 是服务端时钟，startedAt 是浏览器时钟。
    latestKnownReportIdRef.current = historyReports[0]?.id ?? null;
    lastStreamActivityRef.current = streamTimestamp();
    const parsedBudget = budgetYuan.trim() ? Number(budgetYuan) : null;
    const scanOptions = {
      focusSectors,
      budgetYuan:
        parsedBudget !== null && Number.isFinite(parsedBudget) && parsedBudget >= 0
          ? parsedBudget
          : null,
      fundTypePreference: "any" as const,
      selectionStrategy: "balanced" as const,
      scanMode: SCAN_MODE,
      discoveryStrategy: DISCOVERY_STRATEGY,
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
        message: userFacingErrorMessage(scanError, "提交失败"),
      });
    } finally {
      setIsSubmitting(false);
    }
  }, [
    budgetYuan,
    discoveryPrompt.is_custom,
    discoveryPrompt.role_prompt,
    discoveryStreamAbortRef,
    focusSectors,
    historyReports,
    holdings,
    onDiscoveryJobIdChange,
    onDiscoveryStreamComplete,
    onDiscoveryStreamStart,
    onStreamingDiscoveryChange,
    profile,
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
        : SCAN_MODE_LABELS[SCAN_MODE];
  const summaryAnalysisMode = report?.analysis_mode ?? "deep";
  const reportedDiscoveryStrategy =
    report?.discovery_facts?.effective_configuration?.discovery_strategy;
  const summaryDiscoveryStrategy = report
    ? reportedDiscoveryStrategy === "opportunity_first"
      ? "机会优先（20～60交易日）"
      : reportedDiscoveryStrategy === "risk_first"
        ? "稳健筛选"
        : "历史稳健策略"
    : "机会优先（20～60交易日）";
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

  const selectHistoryReport = useCallback((
    selected: FundDiscoveryReport,
    options?: { revealReport?: boolean; asLatest?: boolean },
  ) => {
    // 只有用户主动点选（历史抽屉、删除后切到相邻一份）才移动焦点并滚动过去；
    // 打开页面时的自动载入必须保持安静，否则会抢走焦点、把页面自动滚下去。
    const revealReport = options?.revealReport !== false;
    const asLatest = options?.asLatest === true;
    const cached = readDiscoveryReportDetailCache(userId, selected.id);
    if (cached && isDiscoveryReportDetailCacheFresh(userId, selected.id)) {
      setReport(cached);
      setHistoryDetailError(null);
      setConfigExpanded(false);
      if (!revealReport) return;
      window.setTimeout(() => {
        const region = reportRegionRef.current;
        region?.focus();
        region?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      }, 0);
      return;
    }
    // 先用摘要或过期正文占位切换视图，让用户马上看到标题/时间/方向而不是空白。
    // 关键正文（decision_events / discovery_facts / candidate_pool）稍后从
    // /reports/{id} 拉回后再合并；这段时间 DiscoveryReportPanel 里那些字段读到
    // undefined 会走空态分支，不会崩。
    setReport(cached ?? selected);
    setConfigExpanded(false);
    setHistoryDetailError(null);
    const requestId = ++historyDetailRequestId.current;
    void (async () => {
      try {
        const detail = await fetchDiscoveryReportDetail(selected.id);
        if (requestId !== historyDetailRequestId.current) return;
        writeDiscoveryReportDetailCache(userId, detail, { asLatest });
        setReport(detail);
      } catch (error) {
        if (requestId !== historyDetailRequestId.current) return;
        setHistoryDetailError(
          userFacingErrorMessage(error, "推荐正文加载失败，请稍后重试。"),
        );
      }
    })();
    if (!revealReport) return;
    window.setTimeout(() => {
      const region = reportRegionRef.current;
      region?.focus();
      // scrollIntoView 在 jsdom / 部分内嵌 WebView 里不存在。
      region?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    }, 0);
  }, [userId]);

  const handleHistoryDeleted = useCallback(
    (deletedId: string) => {
      deleteDiscoveryReportDetailCache(userId, deletedId);
      if (report?.id !== deletedId) return;
      const remaining = historyReports.filter((item) => item.id !== deletedId);
      const deletedIndex = historyReports.findIndex((item) => item.id === deletedId);
      const adjacent = remaining[Math.min(Math.max(deletedIndex, 0), remaining.length - 1)] ?? null;
      if (adjacent) {
        // 列表接口只返回摘要，切到相邻一份也要按 id 拉完整详情，避免正文空白。
        selectHistoryReport(adjacent, { asLatest: true });
      } else {
        setReport(null);
        setHistoryDetailError(null);
      }
    },
    [historyReports, report?.id, selectHistoryReport, userId],
  );

  // 打开页面就展示最近一份报告，与日报页一致。历史实现让 report 停在 null，用户每次
  // 都得先打开「历史推荐」抽屉点一下才看得到上次结果。
  // 只在本账号第一次拿到历史列表时做，之后用户手动选/删除/新扫描都不再干预。
  // 30 分钟内的完整正文走内存缓存，切走再回来不必重新拉 5–9 MB 的详情。
  useEffect(() => {
    if (userId == null || autoLoadedLatestForUserRef.current === userId) return;
    if (!historyReports.length) return;
    // 正在扫描、或已有刚扫完待应用的正文时不要抢。
    if (pendingDiscoveryReport || streamingDiscovery) {
      autoLoadedLatestForUserRef.current = userId;
      return;
    }
    const latest = historyReports[0];
    autoLoadedLatestForUserRef.current = userId;
    if (report?.id === latest.id && isDiscoveryReportDetailCacheFresh(userId, latest.id)) {
      return;
    }
    selectHistoryReport(latest, { revealReport: false, asLatest: true });
  }, [historyReports, pendingDiscoveryReport, report, selectHistoryReport, streamingDiscovery, userId]);

  // 每收到一个流事件就刷新"最近有动静"的时间戳（streamingDiscovery 每次事件都是新对象）。
  useEffect(() => {
    if (streamingDiscovery) {
      lastStreamActivityRef.current = streamTimestamp();
    }
  }, [streamingDiscovery]);

  const scanStartedAt = streamingDiscovery?.startedAt ?? null;

  const recoverStuckScan = useCallback(async () => {
    if (!streamLooksDead(lastStreamActivityRef.current, streamTimestamp())) return;
    let reports: FundDiscoveryReport[];
    try {
      reports = await listDiscoveryReports();
    } catch {
      // 网络仍然不通，下一次可见时再试。
      return;
    }
    const recovered = detectCompletedScan({
      reports: sortReportsByCreatedAtDesc(reports),
      knownLatestId: latestKnownReportIdRef.current,
    });
    if (!recovered) return;

    // 后台其实已经跑完了：断掉那条死掉的流，接管结果。
    discoveryStreamAbortRef.current?.abort();
    discoveryStreamAbortRef.current = null;
    onStreamingDiscoveryChange(null);
    onDiscoveryJobIdChange(null);
    setIsSubmitting(false);
    latestKnownReportIdRef.current = recovered.id;
    void refreshReports();
    setFeedback({
      tone: "info",
      message: "扫描已在后台完成（页面切到后台时流式连接被系统挂起），已载入最新结果。",
    });
    selectHistoryReport(recovered, { asLatest: true });
  }, [
    discoveryStreamAbortRef,
    onDiscoveryJobIdChange,
    onStreamingDiscoveryChange,
    refreshReports,
    selectHistoryReport,
  ]);

  // 手机浏览器切走后会挂起 fetch 的 reader，`streamDiscovery` 那个 promise 可能永远不
  // settle，于是 `finally` 不执行、`isSubmitting` 与 `streamingDiscovery` 永远留着，
  // 页面就死在「扫描进行中…」。而流式路径从不登记 discoveryJobId，没有任何轮询兜底。
  // 这里在页面重新可见时补一次核对：流已静默且后台确实产出了新报告，就直接接管。
  useEffect(() => {
    if (scanStartedAt === null || userId == null) return;
    return startVisibilityAwarePolling({
      intervalMs: DISCOVERY_RECOVERY_POLL_MS,
      onTick: () => {
        void recoverStuckScan();
      },
    });
  }, [recoverStuckScan, scanStartedAt, userId]);

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
                  {/* 原来这里是一段 59 字的策略自述。它讲的内容与下面「荐基决策策略」
                      两张卡片高度重合，而用户打开这一屏是为了发起扫描，不是先读一段
                      产品说明。收进口径披露。 */}
                  <MethodologyNote label="扫描逻辑与免责" className="mt-1">
                    优先寻找高波动、高动量与回撤修复机会，再用交易条件、持仓相关性和退出信号控制风险；没有合格基金时不会凑数。仅供参考，不构成投资建议。
                  </MethodologyNote>
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

          <ol className="discovery-decision-rail" aria-label="基金扫描流程">
            <li className={!isRunning && !report ? "is-current" : "is-done"}><span>01</span>方向与约束</li>
            <li className={isRunning ? "is-current" : report ? "is-done" : ""}><span>02</span>扫描与验证</li>
            <li className={report ? "is-current" : ""}><span>03</span>候选与依据</li>
          </ol>

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
          <div id="discovery-scan-settings" className="p-4 sm:p-5">
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
            <DiscoveryStrategySelector />
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
            ) : null}
            {/* 折叠态原来还挂一句"普通扫描无需填写；附录只能……"。这是一个标了
                「（高级）」、状态显示「未添加」的可选项，折叠时再解释一遍它的边界，
                只会给不打算用它的人增加阅读量。展开后 RolePromptEditor 里本来就有说明。 */}
          </div>

          <div className="mt-4">
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
              <p className="mt-2 text-[11px] text-[var(--warn-icon)]">
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

          <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2">
            <p className="text-xs font-black text-slate-800">系统自动选基</p>
            <MethodologyNote label="核验哪些项">
              自动核验申购状态、首次起购额与单日限额；费用可得时按未折扣标准费率估算上限，下单前仍需复核。
            </MethodologyNote>
          </div>

          <div className="mt-4 max-w-md">
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
            </label>
            {/* placeholder 已经写着「按计划投入余额自动计算」，这段只补充口径细节。 */}
            <MethodologyNote label="怎么算的" className="mt-1.5">
              默认按计划投入总额减当前持仓动态计算；手工修改后，本次扫描保留你的输入。
            </MethodologyNote>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="discovery-scan-button"
              disabled={isRunning}
              onClick={() => void handleScan()}
              className="btn-primary min-h-11 w-full !rounded-xl sm:w-auto"
            >
              {isRunning ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Sparkles size={16} />
              )}
              {isRunning ? "扫描进行中…" : report ? "按当前条件重新扫描" : "扫描今日机会"}
            </button>
            {/* 扫描中必须永远留一个出口。手机切走再回来时流式连接可能已被系统挂起，
                此时主按钮是禁用态，若这里没有「停止」，页面就彻底卡死在扫描进行中。 */}
            {isRunning ? (
              <button
                type="button"
                data-testid="discovery-stop-button"
                onClick={handleCancelStream}
                className="btn-ghost min-h-11 w-full !rounded-xl border border-[var(--line)] sm:w-auto"
              >
                停止扫描
              </button>
            ) : null}
          </div>
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
            {historyDetailError ? (
              <InlineNotice
                tone="warning"
                message={historyDetailError}
                className="mb-3"
                action={{
                  label: "重试",
                  onClick: () => selectHistoryReport(report),
                }}
              />
            ) : null}
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
