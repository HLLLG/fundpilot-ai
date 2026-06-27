"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import type {
  AnalysisMode,
  AnalysisPromptConfig,
  FundCodeResolution,
  FundDiscoveryReport,
  Holding,
  HoldingFieldWarning,
  InvestorProfile,
  ParsedTransaction,
  Report,
} from "@/lib/api";
import {
  fetchAnalysisPrompt,
  fetchInvestorProfile,
  fetchPortfolioHoldings,
  fetchPortfolioSummary,
  fetchSectorQuotesStatus,
  listReports,
  applyPortfolioHoldings,
  applyTransactions,
  deletePortfolioHolding,
  parseOcrUpload,
  transactionsOcr,
  saveAnalysisPromptRemote,
  saveInvestorProfileRemote,
  settleOfficialNav,
  startAnalyzeJob,
  type PortfolioSummary,
} from "@/lib/api";
import {
  streamAnalysis,
  submitStreamFollowup,
  appendStreamTokenBuffer,
  markStreamingReportBackgroundFallback,
  streamTimestamp,
  type FundRecommendationPartial,
  type StreamingReportState,
} from "@/lib/streamApi";
import type { StreamingDiscoveryState } from "@/lib/discoveryStreamApi";
import { AddHoldingModal } from "@/components/AddHoldingModal";
import { useAuth } from "@/components/AuthProvider";
import { AlipayOcrConfirmModal } from "@/components/AlipayOcrConfirmModal";
import { BatchTransactionModal } from "@/components/BatchTransactionModal";
import { BatchTransactionConfirmModal } from "@/components/BatchTransactionConfirmModal";
import { notifyDesktop, ensureNotificationPermission } from "@/lib/notifications";
import { formatThinkingNote, stageShortLabel } from "@/lib/streamingStageMeta";
import {
  loadAnalysisMode,
  loadAnalysisPrompt,
  loadDashboardTab,
  loadInvestorProfile,
  normalizeInvestorProfile,
  saveAnalysisMode,
  saveAnalysisPrompt,
  saveDashboardTab,
  saveInvestorProfile,
  type DashboardTabId,
} from "@/lib/storage";
import { HistoryRail } from "@/components/HistoryRail";
import { BackgroundJobsStack } from "@/components/BackgroundJobsStack";
import { DiscoveryJobStatusFloat } from "@/components/DiscoveryJobStatusFloat";
import { JobStatusFloat } from "@/components/JobStatusFloat";
import {
  displayableHoldings,
  findHoldingIndex,
  mergeHoldingsPreserveQuoteFields,
  sumDailyProfit,
  sumPortfolioTotalAssets,
  type HoldingIdentity,
} from "@/lib/holdingMetrics";
import {
  loadCachedPortfolioHoldings,
  saveCachedPortfolioHoldings,
} from "@/lib/portfolioHoldingsCache";
import { scheduleHoldingsDetailPrefetch } from "@/lib/holdingDetailPrefetch";
import { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";
import { useSwingAlerts } from "@/lib/useSwingAlerts";
import { SwingAlertsPanel } from "@/components/SwingAlertsPanel";
import { buildWorkflowBlockers, hasBlockingErrors } from "@/lib/workflowBlockers";
import { TradingSessionBar } from "@/components/TradingSessionBar";
import { PortfolioDashboard } from "@/components/PortfolioDashboard";
import { ReportPanel } from "@/components/ReportPanel";
import { StreamingAnalysisFloat } from "@/components/StreamingAnalysisFloat";
import { DiscoveryStreamingFloat } from "@/components/DiscoveryStreamingFloat";
import { YangjibaoHoldingsBoard } from "@/components/YangjibaoHoldingsBoard";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";
import { NewsPreviewPanel } from "@/components/NewsPreviewPanel";
import { RecommendationAccuracyPanel } from "@/components/RecommendationAccuracyPanel";
import { SectorSignalBacktestPanel } from "@/components/SectorSignalBacktestPanel";
import { RiskControls } from "@/components/RiskControls";
import { DiagnosticsAccordion } from "@/components/DiagnosticsAccordion";
import { FundDiscoveryPanel } from "@/components/FundDiscoveryPanel";
import { FocusSectorToast } from "@/components/FocusSectorToast";
import { MarketTab } from "@/components/MarketTab";
import { UserMenu } from "@/components/UserMenu";
import { BrandMark } from "@/components/BrandMark";
import { DashboardNav } from "@/components/DashboardNav";
const defaultProfile: InvestorProfile = {
  style: "稳健",
  horizon: "半年到一年",
  max_drawdown_percent: 8,
  concentration_limit_percent: 35,
  expected_investment_amount: 30_000,
  prefer_dca: true,
  avoid_chasing: true,
  decision_style: "conservative",
  investment_preset: "conservative_hold",
  round_trip_fee_percent: 1.5,
  min_net_profit_percent: 1.0,
  swing_alerts_enabled: false,
  swing_monitor_scope: "both",
};

type TabId = DashboardTabId;

const defaultAnalysisPrompt: AnalysisPromptConfig = {
  role_prompt: "",
  is_custom: false,
  default_role_prompt: "",
};

export function Dashboard() {
  const { user } = useAuth();
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [profile, setProfile] = useState<InvestorProfile>(() =>
    loadInvestorProfile(defaultProfile),
  );
  const [analysisPrompt, setAnalysisPrompt] = useState<AnalysisPromptConfig>(() =>
    loadAnalysisPrompt(defaultAnalysisPrompt),
  );
  const [report, setReport] = useState<Report | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [portfolioSummary, setPortfolioSummary] = useState<PortfolioSummary | null>(null);
  const [holdingWarnings, setHoldingWarnings] = useState<HoldingFieldWarning[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [activeTab, setActiveTabState] = useState<TabId>("holdings");
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("deep");
  const [profileReady, setProfileReady] = useState(false);
  const [promptReady, setPromptReady] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [streamingReport, setStreamingReport] = useState<StreamingReportState | null>(null);
  const [reportTabUnread, setReportTabUnread] = useState(false);
  const [discoveryTabUnread, setDiscoveryTabUnread] = useState(false);
  const streamAbortRef = useRef<AbortController | null>(null);
  const userLeftReportDuringStreamRef = useRef(false);
  const lastAnalysisStageRef = useRef<{
    stage: string;
    label: string;
    at: number;
    startedAt: number;
  } | null>(null);
  const [discoveryJobId, setDiscoveryJobId] = useState<string | null>(null);
  const [streamingDiscovery, setStreamingDiscovery] = useState<StreamingDiscoveryState | null>(null);
  const discoveryStreamAbortRef = useRef<AbortController | null>(null);
  const userLeftDiscoveryDuringStreamRef = useRef(false);
  const [pendingDiscoveryReport, setPendingDiscoveryReport] = useState<FundDiscoveryReport | null>(
    null,
  );
  const discoveryScanRetryRef = useRef<(() => void) | null>(null);
  const [selectedHoldingKey, setSelectedHoldingKey] = useState<HoldingIdentity | null>(null);
  const selectedHoldingIndex = useMemo(() => {
    if (!selectedHoldingKey) {
      return null;
    }
    const index = findHoldingIndex(holdings, selectedHoldingKey);
    return index >= 0 ? index : null;
  }, [holdings, selectedHoldingKey]);
  const reportSectionRef = useRef<HTMLDivElement>(null);
  const refreshAfterApplyRef = useRef(false);
  const officialNavSettlementAttemptedRef = useRef(false);
  const officialNavSettlementInFlightRef = useRef(false);
  const profilePersistReady = useRef(false);
  const promptPersistReady = useRef(false);
  const [isHydratingHoldings, setIsHydratingHoldings] = useState(true);
  const [holdingsRefreshedAt, setHoldingsRefreshedAt] = useState<string | null>(null);
  const [holdingsPollIntervalMs, setHoldingsPollIntervalMs] = useState(180_000);
  const backgroundJobActiveRef = useRef(false);
  const [isOcrUploading, setIsOcrUploading] = useState(false);
  const [pendingOcrHoldings, setPendingOcrHoldings] = useState<Holding[] | null>(null);
  const [pendingOcrResolutions, setPendingOcrResolutions] = useState<FundCodeResolution[]>([]);
  const [pendingOcrNote, setPendingOcrNote] = useState<string | null>(null);
  const [pendingOcrSource, setPendingOcrSource] = useState<string | null>(null);
  const [showAddHoldingModal, setShowAddHoldingModal] = useState(false);
  const [isManualAdding, setIsManualAdding] = useState(false);
  const [showBatchModal, setShowBatchModal] = useState(false);
  const [isBatchUploading, setIsBatchUploading] = useState(false);
  const [pendingTransactions, setPendingTransactions] = useState<ParsedTransaction[] | null>(null);
  const [isApplyingTransactions, setIsApplyingTransactions] = useState(false);

  const workflowBlockers = useMemo(
    () =>
      buildWorkflowBlockers({
        holdings,
        warnings: holdingWarnings,
        profile,
        hasReportToday: Boolean(
          report?.created_at?.slice(0, 10) === new Date().toISOString().slice(0, 10),
        ),
      }),
    [holdings, holdingWarnings, profile, report?.created_at],
  );

  const ocrWarningCount = holdingWarnings.length;
  const blockingErrors = hasBlockingErrors(workflowBlockers);

  const sectorRefresh = useSectorQuoteRefresh({
    holdings,
    onChange: setHoldings,
    warnings: holdingWarnings,
    onWarningsChange: setHoldingWarnings,
    onMessage: setMessage,
  });

  const swingAlerts = useSwingAlerts({
    holdings,
    profile,
    onBeforeEvaluate: async () => {
      if (holdings.length === 0) {
        return undefined;
      }
      const result = await sectorRefresh.refresh(false, "fast");
      return result?.holdings;
    },
  });

  const loadHistory = async () => {
    try {
      setReports(await listReports());
    } catch {
      // 网络抖动时保留已有列表
    }
  };

  const loadPortfolioSummary = async () => {
    try {
      setPortfolioSummary(await fetchPortfolioSummary());
    } catch {
      setPortfolioSummary(null);
    }
  };

  const settleOfficialNavInBackground = (sourceHoldings: Holding[]) => {
    if (
      officialNavSettlementAttemptedRef.current ||
      officialNavSettlementInFlightRef.current ||
      sourceHoldings.every((holding) => holding.fund_code === "000000")
    ) {
      return;
    }

    officialNavSettlementAttemptedRef.current = true;
    officialNavSettlementInFlightRef.current = true;
    void settleOfficialNav()
      .then((settlement) => {
        if (
          !settlement.ok ||
          settlement.skipped ||
          !settlement.updated_count ||
          settlement.holdings.length === 0
        ) {
          return;
        }
        const refreshedAt = settlement.refreshed_at ?? null;
        setHoldings(settlement.holdings);
        setHoldingsRefreshedAt(refreshedAt);
        if (settlement.portfolio_summary) {
          setPortfolioSummary(settlement.portfolio_summary);
        }
        saveCachedPortfolioHoldings({
          holdings: settlement.holdings,
          portfolio_summary: settlement.portfolio_summary ?? null,
          refreshed_at: refreshedAt,
        });
      })
      .catch(() => {
        // Official NAV settlement is opportunistic; keep the hydrated holdings visible.
      })
      .finally(() => {
        officialNavSettlementInFlightRef.current = false;
      });
  };

  const hydratePortfolio = async () => {
    if (backgroundJobActiveRef.current) {
      return;
    }
    const hadCachedHoldings = loadCachedPortfolioHoldings()?.holdings?.length;
    if (!hadCachedHoldings) {
      setIsHydratingHoldings(true);
    }
    try {
      const payload = await fetchPortfolioHoldings();
      if (payload.portfolio_summary) {
        setPortfolioSummary(payload.portfolio_summary);
      }
      if (payload.holdings.length > 0) {
        const refreshedAt = payload.refreshed_at ?? null;
        setHoldings(payload.holdings);
        setHoldingsRefreshedAt(refreshedAt);
        saveCachedPortfolioHoldings({
          holdings: payload.holdings,
          portfolio_summary: payload.portfolio_summary ?? null,
          refreshed_at: refreshedAt,
        });
        settleOfficialNavInBackground(payload.holdings);
      }
    } catch {
      if (!hadCachedHoldings) {
        await loadPortfolioSummary();
        setMessage("持仓加载失败，请确认后端 API 正常运行后刷新页面。");
      }
    } finally {
      setIsHydratingHoldings(false);
    }
  };

  useLayoutEffect(() => {
    const cached = loadCachedPortfolioHoldings();
    if (!cached?.holdings?.length) {
      return;
    }
    setHoldings(cached.holdings);
    if (cached.portfolio_summary) {
      setPortfolioSummary(cached.portfolio_summary);
    }
    if (cached.refreshed_at) {
      setHoldingsRefreshedAt(cached.refreshed_at);
    }
    setIsHydratingHoldings(false);
  }, []);

  const setActiveTab = useCallback((tab: TabId | ((prev: TabId) => TabId)) => {
    setActiveTabState((prev) => {
      const next = typeof tab === "function" ? tab(prev) : tab;
      if (next === "report") {
        setReportTabUnread(false);
      }
      if (next === "discovery") {
        setDiscoveryTabUnread(false);
      }
      saveDashboardTab(next);
      return next;
    });
  }, []);

  useEffect(() => {
    if (streamingReport && activeTab !== "report") {
      userLeftReportDuringStreamRef.current = true;
    }
  }, [activeTab, streamingReport]);

  useEffect(() => {
    if (streamingDiscovery && activeTab !== "discovery") {
      userLeftDiscoveryDuringStreamRef.current = true;
    }
  }, [activeTab, streamingDiscovery]);

  useLayoutEffect(() => {
    setActiveTabState(loadDashboardTab());
  }, []);

  useEffect(() => {
    const handleDashboardTabEvent = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail;
      if (detail === "holdings" || detail === "report" || detail === "history" || detail === "dashboard" || detail === "market" || detail === "discovery") {
        setActiveTab(detail);
      }
    };
    window.addEventListener("fundpilot-dashboard-tab", handleDashboardTabEvent);
    return () => window.removeEventListener("fundpilot-dashboard-tab", handleDashboardTabEvent);
  }, [setActiveTab]);

  useEffect(() => {
    setAnalysisMode(loadAnalysisMode("deep"));
    void (async () => {
      try {
        const remote = await fetchInvestorProfile();
        const normalized = normalizeInvestorProfile(remote, defaultProfile);
        setProfile(normalized);
        saveInvestorProfile(normalized);
      } catch {
        setProfile((current) => normalizeInvestorProfile(current, defaultProfile));
      } finally {
        profilePersistReady.current = true;
        setProfileReady(true);
      }
    })();
    void (async () => {
      try {
        const remote = await fetchAnalysisPrompt();
        setAnalysisPrompt(remote);
        saveAnalysisPrompt(remote);
      } catch {
        setAnalysisPrompt((current) => loadAnalysisPrompt(current));
      } finally {
        promptPersistReady.current = true;
        setPromptReady(true);
      }
    })();
    void loadHistory();
    void hydratePortfolio();
    // Mount-only bootstrap; avoid re-fetching on callback identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    backgroundJobActiveRef.current = Boolean(
      streamingReport || streamingDiscovery || discoveryJobId || activeJobId,
    );
  }, [streamingReport, streamingDiscovery, discoveryJobId, activeJobId]);

  useEffect(() => {
    void fetchSectorQuotesStatus()
      .then((status) => setHoldingsPollIntervalMs(status.auto_interval_seconds * 1000))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (holdings.length === 0) {
      return;
    }
    let cancelled = false;
    const tick = async () => {
      if (cancelled) {
        return;
      }
      // AI 流式/异步任务会占用 API worker；任务进行中跳过后台 holdings 刷新，避免 504
      if (
        streamingReport ||
        streamingDiscovery ||
        discoveryJobId ||
        activeJobId
      ) {
        return;
      }
      try {
        const status = await fetchSectorQuotesStatus();
        if (!status.auto_refresh_allowed) {
          return;
        }
        await hydratePortfolio();
      } catch {
        // 后台轮询失败不阻断展示
      }
    };
    const timer = window.setInterval(() => void tick(), holdingsPollIntervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // hydratePortfolio 刻意不列入依赖，避免重复拉取
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    holdings.length,
    holdingsPollIntervalMs,
    streamingReport,
    streamingDiscovery,
    discoveryJobId,
    activeJobId,
  ]);

  useEffect(() => {
    if (!refreshAfterApplyRef.current || holdings.length === 0) {
      return;
    }
    refreshAfterApplyRef.current = false;
    void hydratePortfolio();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [holdings]);

  useEffect(() => {
    if (holdings.length === 0) {
      return;
    }
    saveCachedPortfolioHoldings({
      holdings,
      portfolio_summary: portfolioSummary,
      refreshed_at: holdingsRefreshedAt,
    });
  }, [holdings, portfolioSummary, holdingsRefreshedAt]);

  useEffect(() => {
    if (!sectorRefresh.lastFetchedAt) {
      return;
    }
    setHoldingsRefreshedAt(sectorRefresh.lastFetchedAt);
  }, [sectorRefresh.lastFetchedAt]);

  useEffect(() => {
    if (activeTab !== "holdings" || holdings.length === 0) {
      return;
    }
    return scheduleHoldingsDetailPrefetch({
      userId: user?.id ?? null,
      holdings,
      portfolioSummary,
      sectorMetaByFundCode: sectorRefresh.sectorMetaByFundCode,
    });
  }, [
    activeTab,
    holdings,
    portfolioSummary,
    user?.id,
    sectorRefresh.sectorMetaByFundCode,
  ]);

  useEffect(() => {
    if (!profileReady || !profilePersistReady.current) return;
    const normalized = normalizeInvestorProfile(profile, defaultProfile);
    saveInvestorProfile(normalized);
    void saveInvestorProfileRemote(normalized).catch(() => {
      // 离线时仍保留 localStorage；下次启动会从本地缓存恢复。
    });
  }, [profile, profileReady]);

  useEffect(() => {
    if (!promptReady || !promptPersistReady.current) return;
    saveAnalysisPrompt(analysisPrompt);
    const storedValue = analysisPrompt.is_custom ? analysisPrompt.role_prompt : null;
    void saveAnalysisPromptRemote(storedValue).catch(() => {
      // 离线时仍保留 localStorage。
    });
  }, [analysisPrompt, promptReady]);

  useEffect(() => {
    if (!profileReady) return;
    saveAnalysisMode(analysisMode);
  }, [analysisMode, profileReady]);

  const handleCancelStream = useCallback(() => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    userLeftReportDuringStreamRef.current = false;
    setStreamingReport(null);
    setIsSubmitting(false);
    setMessage("已停止生成。");
  }, []);

  const handleStreamFollowup = useCallback(
    async (message: string) => {
      const sessionId = streamingReport?.sessionId;
      if (!sessionId) {
        throw new Error("流式会话未就绪");
      }
      await submitStreamFollowup(sessionId, message);
      setStreamingReport((current) =>
        current
          ? { ...current, followupNotes: [...current.followupNotes, message] }
          : current,
      );
    },
    [streamingReport?.sessionId],
  );

  const runAnalyze = async (targetHoldings: Holding[]) => {
    if (!targetHoldings.length) {
      setMessage("请先上传截图或录入至少一条持仓。");
      return;
    }
    setIsSubmitting(true);
    setMessage(null);
    try {
      try {
        void ensureNotificationPermission();
        userLeftReportDuringStreamRef.current = false;
        const abortController = new AbortController();
        streamAbortRef.current = abortController;
        const startedAt = streamTimestamp();
        lastAnalysisStageRef.current = {
          stage: "fund_data",
          label: "正在连接流式分析...",
          at: startedAt,
          startedAt,
        };
        setStreamingReport({
          stage: "fund_data",
          stageLabel: "正在连接流式分析…",
          fundCodes: targetHoldings.map((holding) => holding.fund_code),
          fundNames: targetHoldings.map((holding) => holding.fund_name),
          partialByCode: {},
          stageLog: [],
          thinkingNotes: [],
          startedAt,
          tokenBuffer: "",
          followupNotes: [],
        });
        setActiveTab("report");
        requestAnimationFrame(() => {
          reportSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        });

        await streamAnalysis(
          targetHoldings,
          profile,
          {
            onSession: (sessionId) =>
              setStreamingReport((current) =>
                current ? { ...current, sessionId } : current,
              ),
            onStage: (stage, label) =>
              setStreamingReport((current) => {
                if (!current) {
                  return current;
                }
                const at = streamTimestamp();
                lastAnalysisStageRef.current = {
                  stage,
                  label,
                  at,
                  startedAt: current.startedAt,
                };
                const entry = { stage, label, at };
                const stageLog = [
                  ...current.stageLog.filter((item) => item.stage !== stage),
                  entry,
                ];
                return { ...current, stage, stageLabel: label, stageLog };
              }),
            onSkeleton: (fundCodes, fundNames) =>
              setStreamingReport((current) =>
                current ? { ...current, fundCodes, fundNames } : current,
              ),
            onToken: (content) =>
              setStreamingReport((current) =>
                current
                  ? {
                      ...current,
                      tokenBuffer: appendStreamTokenBuffer(current.tokenBuffer, content),
                    }
                  : current,
              ),
            onPartial: (field, value) => {
              setStreamingReport((current) => {
                if (!current) {
                  return current;
                }
                const note = formatThinkingNote(field, value);
                const thinkingNotes =
                  note && !current.thinkingNotes.includes(note)
                    ? [...current.thinkingNotes, note]
                    : current.thinkingNotes;
                if (field === "title") {
                  return { ...current, title: String(value), thinkingNotes };
                }
                if (field === "summary") {
                  return { ...current, summary: String(value), thinkingNotes };
                }
                if (field === "caveats" && Array.isArray(value)) {
                  return {
                    ...current,
                    caveats: value.map(String),
                    thinkingNotes,
                  };
                }
                if (field === "fund_recommendation" && value && typeof value === "object") {
                  const rec = value as FundRecommendationPartial;
                  const code = rec.fund_code;
                  if (!code) {
                    return current;
                  }
                  return {
                    ...current,
                    thinkingNotes,
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
              const stayOnCurrentTab = userLeftReportDuringStreamRef.current;
              userLeftReportDuringStreamRef.current = false;
              streamAbortRef.current = null;
              setStreamingReport(null);
              void handleJobComplete(completedReport, {
                navigateToReport: !stayOnCurrentTab,
              });
            },
            onError: (message) => {
              throw new Error(message);
            },
          },
          {
            analysisMode,
            systemRolePrompt: analysisPrompt.role_prompt,
            signal: abortController.signal,
          },
        );
        return;
      } catch (streamError) {
        streamAbortRef.current = null;
        userLeftReportDuringStreamRef.current = false;
        if (streamError instanceof DOMException && streamError.name === "AbortError") {
          setStreamingReport(null);
          lastAnalysisStageRef.current = null;
          setMessage("已停止生成。");
          return;
        }
        setMessage(
          streamError instanceof Error
            ? `${streamError.message}，已切换到后台分析。`
            : "流式分析失败，已切换到后台分析。",
        );
      }

      const lastStage = lastAnalysisStageRef.current;
      const elapsedText = lastStage
        ? `${Math.max(0, Math.round((streamTimestamp() - lastStage.startedAt) / 1000))}s`
        : "";
      const stageText = lastStage
        ? `停在「${stageShortLabel(lastStage.stage)}」：${lastStage.label}${elapsedText ? `，累计 ${elapsedText}` : ""}`
        : "未能定位最后阶段";
      setMessage(`${stageText}，已切换到后台分析。`);

      const jobId = await startAnalyzeJob(
        targetHoldings,
        profile,
        undefined,
        analysisMode,
        analysisPrompt.role_prompt,
      );
      setActiveJobId(jobId);
      setStreamingReport((current) =>
        markStreamingReportBackgroundFallback(
          current,
          jobId,
          lastAnalysisStageRef.current
            ? `停在「${stageShortLabel(lastAnalysisStageRef.current.stage)}」`
            : "流式生成中断",
        ),
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提交分析任务失败。");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleAnalyze = async () => {
    await runAnalyze(displayableHoldings(holdings));
  };

  const handleJobComplete = async (
    completedReport: Report,
    options?: { navigateToReport?: boolean },
  ) => {
    setStreamingReport(null);
    streamAbortRef.current = null;
    lastAnalysisStageRef.current = null;
    setReport(completedReport);
    await loadHistory();
    setActiveJobId(null);

    const shouldNavigate = options?.navigateToReport !== false;
    if (shouldNavigate) {
      setActiveTab("report");
      requestAnimationFrame(() => {
        reportSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    } else {
      setReportTabUnread(true);
    }

    notifyDesktop("FundPilot 日报已生成", { body: completedReport.title });
    setMessage(
      analysisMode === "fast"
        ? "快速模式日报已生成（Flash + 预取新闻）。"
        : "日报已生成并保存到历史记录。",
    );
  };

  const handleJobClose = () => {
    setActiveJobId(null);
  };

  const handleJobRetry = async () => {
    setActiveJobId(null);
    await runAnalyze(displayableHoldings(holdings));
  };

  const handleDiscoveryJobComplete = (completedReport: FundDiscoveryReport) => {
    setPendingDiscoveryReport(completedReport);
    setDiscoveryJobId(null);
    setActiveTab("discovery");
    notifyDesktop("FundPilot 推荐报告已生成", {
      body: completedReport.title ?? "推荐基金扫描已完成",
    });
  };

  const handleDiscoveryStreamComplete = useCallback(
    (completedReport: FundDiscoveryReport) => {
      const stayOnCurrentTab = userLeftDiscoveryDuringStreamRef.current;
      userLeftDiscoveryDuringStreamRef.current = false;
      setStreamingDiscovery(null);
      discoveryStreamAbortRef.current = null;
      setPendingDiscoveryReport(completedReport);

      if (!stayOnCurrentTab) {
        setActiveTab("discovery");
      } else {
        setDiscoveryTabUnread(true);
      }

      notifyDesktop("FundPilot 推荐报告已生成", {
        body: completedReport.title ?? "推荐基金扫描已完成",
      });
    },
    [setActiveTab],
  );

  const handleCancelDiscoveryStream = useCallback(() => {
    discoveryStreamAbortRef.current?.abort();
    discoveryStreamAbortRef.current = null;
    userLeftDiscoveryDuringStreamRef.current = false;
    setStreamingDiscovery(null);
    setMessage("已停止扫描。");
  }, []);

  const handleDiscoveryJobClose = () => {
    setDiscoveryJobId(null);
  };

  const handleDiscoveryJobRetry = () => {
    setDiscoveryJobId(null);
    discoveryScanRetryRef.current?.();
  };

  const registerDiscoveryScanRetry = useCallback((retry: (() => void) | null) => {
    discoveryScanRetryRef.current = retry;
  }, []);

  const clearPendingDiscoveryReport = useCallback(() => {
    setPendingDiscoveryReport(null);
  }, []);

  const handleOcrUpload = async (selectedFile: File) => {
    setIsOcrUploading(true);
    setMessage(null);
    try {
      const formData = new FormData();
      formData.append("file", selectedFile);
      const result = await parseOcrUpload(formData, { preview: true });
      if (result.error) {
        throw new Error(result.error);
      }
      if (!result.holdings.length) {
        throw new Error(
          "未识别到基金持仓，请确认截图为支付宝「我的持有」。",
        );
      }
      setPendingOcrHoldings(result.holdings);
      setPendingOcrResolutions(result.fund_code_resolutions ?? []);
      setPendingOcrNote(result.amount_semantics?.note ?? null);
      setPendingOcrSource(result.ocr_source ?? null);
      setHoldingWarnings(result.holding_warnings ?? []);
      setShowAddHoldingModal(false);
      setActiveTab("holdings");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "截图识别失败。");
    } finally {
      setIsOcrUploading(false);
    }
  };

  const handleManualAddHoldings = async (newHoldings: Holding[]) => {
    if (!newHoldings.length) {
      return;
    }
    setIsManualAdding(true);
    setMessage(null);
    try {
      const merged = [...displayableHoldings(holdings), ...newHoldings];
      const applied = await applyPortfolioHoldings(merged);
      setHoldings(applied.holdings);
      if (applied.portfolio_summary) {
        setPortfolioSummary(applied.portfolio_summary);
      }
      refreshAfterApplyRef.current = true;
      setShowAddHoldingModal(false);
      setMessage(
        newHoldings.length === 1
          ? `已添加 ${newHoldings[0].fund_name} 到账户汇总。`
          : `已添加 ${newHoldings.length} 只基金到账户汇总。`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "手动添加失败。");
    } finally {
      setIsManualAdding(false);
    }
  };

  const handleConfirmOcrHoldings = () => {
    if (!pendingOcrHoldings?.length) {
      return;
    }
    const toApply = pendingOcrHoldings;
    const previousHoldings = holdings;

    setPendingOcrHoldings(null);
    setPendingOcrResolutions([]);
    setPendingOcrNote(null);
    setPendingOcrSource(null);
    setActiveTab("holdings");
    setHoldings(mergeHoldingsPreserveQuoteFields(previousHoldings, toApply));
    refreshAfterApplyRef.current = true;

    void (async () => {
      try {
        const applied = await applyPortfolioHoldings(toApply);
        setHoldings(mergeHoldingsPreserveQuoteFields(previousHoldings, applied.holdings));
        if (applied.portfolio_summary) {
          setPortfolioSummary((current) => {
            const base = current ?? applied.portfolio_summary!;
            return {
              ...base,
              ...applied.portfolio_summary,
              daily_profit:
                applied.portfolio_summary?.daily_profit ?? base.daily_profit ?? null,
              daily_return_percent:
                applied.portfolio_summary?.daily_return_percent ??
                base.daily_return_percent ??
                null,
            };
          });
        }
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "确认更新失败。");
      }
    })();
  };

  const handleDeleteHolding = useCallback(
    (index: number) => {
      const target = holdings[index];
      if (!target) {
        return;
      }

      const rollbackHoldings = holdings;
      const rollbackSummary = portfolioSummary;
      const remaining = holdings.filter((_, itemIndex) => itemIndex !== index);
      const display = displayableHoldings(remaining);
      const totalAssets = sumPortfolioTotalAssets(display) || null;
      const dailyProfit = display.length > 0 ? sumDailyProfit(display) : null;
      let dailyReturnPercent: number | null = null;
      if (totalAssets != null && dailyProfit != null && totalAssets > dailyProfit) {
        const previousAssets = totalAssets - dailyProfit;
        if (previousAssets > 0) {
          dailyReturnPercent = Math.round((dailyProfit / previousAssets) * 10000) / 100;
        }
      }
      const optimisticSummary: PortfolioSummary = {
        ...portfolioSummary,
        total_assets: totalAssets,
        daily_profit: dailyProfit,
        daily_return_percent: dailyReturnPercent,
        holding_count: display.length,
        updated_at: new Date().toISOString(),
      };

      setHoldings(remaining);
      setPortfolioSummary(optimisticSummary);
      setSelectedHoldingKey(null);
      saveCachedPortfolioHoldings({
        holdings: remaining,
        portfolio_summary: optimisticSummary,
        refreshed_at: holdingsRefreshedAt,
      });
      setMessage(`已移除 ${target.fund_name}`);

      sectorRefresh.invalidatePendingRefresh();

      void deletePortfolioHolding(target.fund_code, target.fund_name)
        .then((result) => {
          setHoldings(result.holdings);
          if (result.portfolio_summary) {
            setPortfolioSummary(result.portfolio_summary);
          }
          saveCachedPortfolioHoldings({
            holdings: result.holdings,
            portfolio_summary: result.portfolio_summary ?? optimisticSummary,
            refreshed_at: holdingsRefreshedAt,
          });
        })
        .catch((error) => {
          setHoldings(rollbackHoldings);
          setPortfolioSummary(rollbackSummary);
          saveCachedPortfolioHoldings({
            holdings: rollbackHoldings,
            portfolio_summary: rollbackSummary,
            refreshed_at: holdingsRefreshedAt,
          });
          setMessage(error instanceof Error ? error.message : "删除失败，已恢复列表");
        });
    },
    [holdings, holdingsRefreshedAt, portfolioSummary, sectorRefresh],
  );

  const mergeTransactions = (
    existing: ParsedTransaction[],
    incoming: ParsedTransaction[],
  ): ParsedTransaction[] => {
    const seen = new Set(
      existing.map((tx) => `${tx.direction}|${tx.fund_name}|${tx.amount_yuan}|${tx.trade_time}`),
    );
    const merged = [...existing];
    for (const tx of incoming) {
      const key = `${tx.direction}|${tx.fund_name}|${tx.amount_yuan}|${tx.trade_time}`;
      if (!seen.has(key)) {
        seen.add(key);
        merged.push(tx);
      }
    }
    return merged;
  };

  const handleBatchUpload = async (selectedFile: File) => {
    setIsBatchUploading(true);
    setMessage(null);
    try {
      const result = await transactionsOcr(selectedFile);
      if (!result.transactions.length) {
        throw new Error("未识别到交易记录，请确认截图为支付宝「交易记录 / 交易分析」页。");
      }
      setShowBatchModal(false);
      setPendingTransactions((prev) => mergeTransactions(prev ?? [], result.transactions));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "交易记录识别失败。");
    } finally {
      setIsBatchUploading(false);
    }
  };

  const handleApplyTransactions = async () => {
    if (!pendingTransactions?.length) {
      return;
    }
    const toApply = pendingTransactions.filter((tx) => Boolean(tx.fund_code));
    if (!toApply.length) {
      setMessage("没有可应用的交易（请先为交易匹配基金代码）。");
      return;
    }
    setIsApplyingTransactions(true);
    setMessage(null);
    try {
      const result = await applyTransactions(toApply);
      setHoldings(result.holdings);
      void loadPortfolioSummary();
      setPendingTransactions(null);
      const pendingNote = result.pending > 0 ? `，${result.pending} 笔待净值确认` : "";
      setMessage(`已应用 ${result.inserted} 笔交易${pendingNote}，持仓已更新。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "应用交易失败。");
    } finally {
      setIsApplyingTransactions(false);
    }
  };

  return (
    <main className="premium-bg min-h-screen">
      <div className="dashboard-shell mx-auto flex min-h-screen w-full max-w-6xl flex-col px-4 py-3 sm:px-5 sm:py-4">
        <nav
          className="sticky top-0 z-40 -mx-4 mb-3 flex items-center justify-between gap-3 border-b border-[var(--line)] px-4 py-2.5 backdrop-blur-md sm:-mx-5 sm:px-5"
          style={{ background: "rgba(243, 246, 252, 0.82)" }}
        >
          <BrandMark size="md" />
          <UserMenu onNavigate={setActiveTab} />
        </nav>

        <DashboardNav
          activeTab={activeTab}
          reportTabUnread={reportTabUnread}
          discoveryTabUnread={discoveryTabUnread}
          onSelect={setActiveTab}
          onSelectHistory={() => setActiveTab("history")}
        />

        {message ? (
          <div
            className="mb-3 flex items-start justify-between gap-3 rounded-xl border border-blue-100 bg-white px-3 py-2.5 text-sm text-slate-700"
            role="status"
          >
            <span className="leading-6">{message}</span>
            <button
              type="button"
              onClick={() => setMessage(null)}
              className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100"
              aria-label="关闭提示"
            >
              <X size={14} />
            </button>
          </div>
        ) : null}

        <div className="min-w-0 flex-1 pb-6">
          {activeTab === "holdings" ? (
            <div className="w-full">
              {swingAlerts.alertsActive ? (
                <SwingAlertsPanel
                  items={swingAlerts.items}
                  sessionKind={swingAlerts.sessionKind}
                  isEvaluating={swingAlerts.isEvaluating}
                  error={swingAlerts.error}
                  onRefresh={() => void swingAlerts.evaluate()}
                />
              ) : null}
              <YangjibaoHoldingsBoard
                holdings={holdings}
                portfolioSummary={portfolioSummary}
                sectorRefresh={sectorRefresh}
                refreshedAt={holdingsRefreshedAt}
                isLoading={isHydratingHoldings && holdings.length === 0}
                onAddHolding={() => setShowAddHoldingModal(true)}
                onBatchTransaction={() => setShowBatchModal(true)}
                onSelectHolding={setSelectedHoldingKey}
              />
            </div>
          ) : null}

          {activeTab === "report" ? (
            <div className="grid min-w-0 gap-4">
              <TradingSessionBar />
              <RiskControls
                profile={profile}
                analysisMode={analysisMode}
                rolePrompt={analysisPrompt.role_prompt}
                isRolePromptCustom={analysisPrompt.is_custom}
                onAnalysisModeChange={setAnalysisMode}
                onChange={setProfile}
                onRolePromptChange={(value) =>
                  setAnalysisPrompt((current) => ({
                    ...current,
                    role_prompt: value.slice(0, 4000),
                    is_custom: value.trim() !== current.default_role_prompt.trim(),
                  }))
                }
                onRolePromptReset={() =>
                  setAnalysisPrompt((current) => ({
                    ...current,
                    role_prompt: current.default_role_prompt,
                    is_custom: false,
                  }))
                }
                onAnalyze={() => void handleAnalyze()}
                isBusy={isSubmitting}
                ocrWarningCount={ocrWarningCount}
                hasBlockingErrors={blockingErrors}
              />
              {report || streamingReport ? (
                <div ref={reportSectionRef} className="min-w-0">
                  <ReportPanel
                    report={report}
                    streaming={streamingReport}
                    onCancelStream={activeJobId ? undefined : handleCancelStream}
                    onStreamFollowup={activeJobId ? undefined : handleStreamFollowup}
                  />
                </div>
              ) : null}
              <DiagnosticsAccordion>
                <NewsPreviewPanel holdings={displayableHoldings(holdings)} profile={profile} />
                <RecommendationAccuracyPanel />
                <SectorSignalBacktestPanel
                  sectorLabels={[
                    ...new Set(
                      displayableHoldings(holdings)
                        .map((item) => item.sector_name?.trim())
                        .filter((name): name is string => Boolean(name)),
                    ),
                  ]}
                />
              </DiagnosticsAccordion>
            </div>
          ) : null}

          {activeTab === "dashboard" ? <PortfolioDashboard /> : null}

          {activeTab === "market" ? <MarketTab /> : null}

          {activeTab === "discovery" ? (
            <FundDiscoveryPanel
              holdings={holdings}
              profile={profile}
              onProfileChange={setProfile}
              analysisMode={analysisMode}
              onAnalysisModeChange={setAnalysisMode}
              discoveryJobId={discoveryJobId}
              onDiscoveryJobIdChange={setDiscoveryJobId}
              pendingDiscoveryReport={pendingDiscoveryReport}
              onPendingDiscoveryReportApplied={clearPendingDiscoveryReport}
              onRegisterDiscoveryScanRetry={registerDiscoveryScanRetry}
              streamingDiscovery={streamingDiscovery}
              onStreamingDiscoveryChange={setStreamingDiscovery}
              onDiscoveryStreamComplete={handleDiscoveryStreamComplete}
              onDiscoveryStreamStart={() => {
                userLeftDiscoveryDuringStreamRef.current = false;
              }}
              discoveryStreamAbortRef={discoveryStreamAbortRef}
            />
          ) : null}

          {activeTab === "history" ? (
            <div className="grid gap-6">
              <SectorSignalBacktestPanel title="板块信号历史回测（全部 canonical）" />
              <HistoryRail
              reports={reports}
              onRefresh={loadHistory}
              onSelect={(selectedReport) => {
                setReport(selectedReport);
                setActiveTab("report");
                requestAnimationFrame(() => {
                  reportSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
                });
              }}
              onDeleted={(reportId) => {
                if (report?.id === reportId) {
                  setReport(null);
                }
              }}
            />
            </div>
          ) : null}
        </div>
      </div>

      <BackgroundJobsStack>
        {streamingDiscovery && activeTab !== "discovery" ? (
          <DiscoveryStreamingFloat
            streaming={streamingDiscovery}
            onOpenDiscovery={() => setActiveTab("discovery")}
            onCancel={handleCancelDiscoveryStream}
          />
        ) : null}
        {streamingReport && activeTab !== "report" && !activeJobId ? (
          <StreamingAnalysisFloat
            streaming={streamingReport}
            onOpenReport={() => setActiveTab("report")}
            onCancel={handleCancelStream}
          />
        ) : null}
        {activeJobId ? (
          <JobStatusFloat
            key={`analysis-${activeJobId}`}
            jobId={activeJobId}
            onComplete={(completedReport) => void handleJobComplete(completedReport)}
            onClose={handleJobClose}
            onRetry={() => void handleJobRetry()}
          />
        ) : null}
        {discoveryJobId ? (
          <DiscoveryJobStatusFloat
            key={`discovery-${discoveryJobId}`}
            jobId={discoveryJobId}
            onComplete={handleDiscoveryJobComplete}
            onClose={handleDiscoveryJobClose}
            onRetry={handleDiscoveryJobRetry}
          />
        ) : null}
      </BackgroundJobsStack>

      {selectedHoldingIndex !== null && holdings[selectedHoldingIndex] ? (
        <YangjibaoFundDetail
          holding={holdings[selectedHoldingIndex]}
          holdingIndex={selectedHoldingIndex}
          holdings={holdings}
          portfolioSummary={portfolioSummary}
          sectorMeta={sectorRefresh.sectorMetaByFundCode[holdings[selectedHoldingIndex].fund_code]}
          onClose={() => setSelectedHoldingKey(null)}
          onNavigate={(index) => {
            const target = holdings[index];
            if (target) {
              setSelectedHoldingKey({
                fund_code: target.fund_code,
                fund_name: target.fund_name,
              });
            }
          }}
          onFundCodeUpdated={async (index, updated) => {
            const next = holdings.map((item, itemIndex) => (itemIndex === index ? updated : item));
            setHoldings(next);
            try {
              await applyPortfolioHoldings(next);
              setMessage(`基金代码已更新为 ${updated.fund_code}`);
            } catch (error) {
              setMessage(error instanceof Error ? error.message : "持仓持久化失败，请刷新后重试");
            }
          }}
          onHoldingResolved={(index, resolved) => {
            setHoldings((current) =>
              current.map((item, itemIndex) => (itemIndex === index ? resolved : item)),
            );
          }}
          onDeleteHolding={handleDeleteHolding}
          onPortfolioUpdated={async (nextHoldings) => {
            setHoldings(nextHoldings);
            try {
              await applyPortfolioHoldings(nextHoldings);
            } catch (error) {
              setMessage(error instanceof Error ? error.message : "持仓持久化失败，请刷新后重试");
            }
          }}
        />
      ) : null}

      <AddHoldingModal
        open={showAddHoldingModal}
        onClose={() => setShowAddHoldingModal(false)}
        onUpload={(file) => void handleOcrUpload(file)}
        onManualSubmit={(items) => handleManualAddHoldings(items)}
        isUploading={isOcrUploading}
        isSubmitting={isManualAdding}
      />

      <BatchTransactionModal
        open={showBatchModal}
        onClose={() => setShowBatchModal(false)}
        onUpload={(file) => void handleBatchUpload(file)}
        isUploading={isBatchUploading}
      />

      {pendingTransactions ? (
        <BatchTransactionConfirmModal
          transactions={pendingTransactions}
          isBusy={isApplyingTransactions}
          onChange={setPendingTransactions}
          onConfirm={() => void handleApplyTransactions()}
          onContinueUpload={() => setShowBatchModal(true)}
          onClose={() => setPendingTransactions(null)}
        />
      ) : null}

      {pendingOcrHoldings ? (
        <AlipayOcrConfirmModal
          holdings={pendingOcrHoldings}
          fundCodeResolutions={pendingOcrResolutions}
          amountSemanticsNote={pendingOcrNote}
          ocrSource={pendingOcrSource}
          onChange={setPendingOcrHoldings}
          onConfirm={() => void handleConfirmOcrHoldings()}
          onClose={() => {
            setPendingOcrHoldings(null);
            setPendingOcrResolutions([]);
            setPendingOcrNote(null);
            setPendingOcrSource(null);
          }}
        />
      ) : null}
      <FocusSectorToast />
    </main>
  );
}
