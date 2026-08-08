"use client";

import dynamic from "next/dynamic";
import {
  useCallback,
  useDeferredValue,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Search } from "lucide-react";
import type {
  AnalysisPromptConfig,
  FundCodeResolution,
  FundDiscoveryReport,
  FundSearchItem,
  Holding,
  HoldingAdjustmentPatch,
  HoldingFieldWarning,
  InvestorProfile,
  ParsedTransaction,
  Report,
} from "@/lib/api";
import {
  fetchAnalysisPrompt,
  fetchDashboardBootstrap,
  fetchInvestorProfile,
  fetchPortfolioHoldings,
  fetchPortfolioSummary,
  fetchReportDetail,
  fetchSectorQuotesStatus,
  invalidatePortfolioHoldingsRequest,
  listReports,
  adjustHolding,
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
import { useAuth } from "@/components/AuthProvider";
import { notifyDesktop, ensureNotificationPermission } from "@/lib/notifications";
import { BRAND } from "@/lib/brand";
import { formatThinkingNote, stageShortLabel } from "@/lib/streamingStageMeta";
import {
  loadAnalysisPrompt,
  loadDashboardTab,
  loadInvestorProfile,
  normalizeInvestorProfile,
  saveAnalysisPrompt,
  saveDashboardTab,
  saveInvestorProfile,
  type DashboardTabId,
} from "@/lib/storage";
import { ReportNavigator } from "@/components/ReportNavigator";
import { BackgroundJobsStack } from "@/components/BackgroundJobsStack";
import { DiscoveryJobStatusFloat } from "@/components/DiscoveryJobStatusFloat";
import { JobStatusFloat } from "@/components/JobStatusFloat";
import {
  displayableHoldings,
  findHoldingIndex,
  mergeHoldingsPreserveQuoteFields,
  withApplyDisplayFields,
  dedupeHoldingsByCode,
  patchHoldingRecord,
  type HoldingIdentity,
} from "@/lib/holdingMetrics";
import {
  loadCachedPortfolioHoldings,
  saveCachedPortfolioHoldings,
} from "@/lib/portfolioHoldingsCache";
import { scheduleHoldingsDetailPrefetch } from "@/lib/holdingDetailPrefetch";
import { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";
import { useSwingAlerts } from "@/lib/useSwingAlerts";
import { startVisibilityAwarePolling } from "@/lib/visibilityPolling";
import { SwingAlertsPanel } from "@/components/SwingAlertsPanel";
import { buildWorkflowBlockers, hasBlockingErrors } from "@/lib/workflowBlockers";
import { TradingSessionBar } from "@/components/TradingSessionBar";
import { StreamingAnalysisFloat } from "@/components/StreamingAnalysisFloat";
import { DiscoveryStreamingFloat } from "@/components/DiscoveryStreamingFloat";
import {
  YangjibaoHoldingsBoard,
  type PortfolioLoadState,
} from "@/components/YangjibaoHoldingsBoard";
import type { HoldingMutationResult } from "@/components/YangjibaoFundDetail";
import { RiskControls } from "@/components/RiskControls";
import { FocusSectorToast } from "@/components/FocusSectorToast";
import { UserMenu } from "@/components/UserMenu";
import { BrandMark } from "@/components/BrandMark";
import { DashboardNav } from "@/components/DashboardNav";
import { InlineNotice } from "@/components/InlineNotice";
import { activeAnalysisRolePrompt } from "@/lib/analysisPrompt";
import { resolveReportProviderStatus } from "@/lib/reportPresentation";
import { userFacingErrorMessage } from "@/lib/userFacingError";
// 工作台专属样式：Dashboard 本身是 next/dynamic 懒加载的，样式随它一起按需到达，
// 不会进入匿名首屏与登录/注册/设置/管理员路由的阻塞 CSS。
import "@/app/dashboard.css";

function DashboardTabLoading({ label }: { label: string }) {
  return (
    <section className="section-card flex min-h-40 items-center justify-center text-sm text-slate-500" role="status">
      正在加载{label}…
    </section>
  );
}

function DeferredInteractionLoading({ label }: { label: string }) {
  return (
    // bottom 要避开移动端底栏（`--bottom-nav-h` + 安全区），否则这条加载提示会盖住
    // 导航按钮；桌面没有底栏，回到贴底 1rem。
    <div
      className="pointer-events-none fixed inset-x-0 bottom-[calc(var(--bottom-nav-h,3.25rem)+env(safe-area-inset-bottom,0px)+0.5rem)] z-[70] flex justify-center px-4 lg:bottom-4"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="rounded-full border border-slate-200 bg-white/95 px-4 py-2 text-sm font-medium text-slate-600 shadow-lg backdrop-blur">
        正在加载{label}…
      </span>
    </div>
  );
}

const PortfolioDashboard = dynamic(
  () => import("@/components/PortfolioDashboard").then((module) => module.PortfolioDashboard),
  { loading: () => <DashboardTabLoading label="组合看板" /> },
);
const ReportPanel = dynamic(
  () => import("@/components/ReportPanel").then((module) => module.ReportPanel),
  { loading: () => <DashboardTabLoading label="日报复盘" /> },
);
const ReportDiagnostics = dynamic(
  () =>
    import("@/components/ReportDiagnostics").then(
      (module) => module.ReportDiagnostics,
    ),
  { loading: () => <InlineNotice tone="info" message="正在加载投研诊断…" /> },
);
const ReportHistoryDrawer = dynamic(
  () =>
    import("@/components/ReportHistoryDrawer").then(
      (module) => module.ReportHistoryDrawer,
    ),
  { loading: () => <DeferredInteractionLoading label="历史日报" /> },
);
const FundDiscoveryPanel = dynamic(
  () => import("@/components/FundDiscoveryPanel").then((module) => module.FundDiscoveryPanel),
  { loading: () => <DashboardTabLoading label="推荐基金" /> },
);
const MarketTab = dynamic(
  () => import("@/components/MarketTab").then((module) => module.MarketTab),
  { loading: () => <DashboardTabLoading label="市场行情" /> },
);
const YangjibaoFundDetail = dynamic(
  () =>
    import("@/components/YangjibaoFundDetail").then(
      (module) => module.YangjibaoFundDetail,
    ),
  { loading: () => <DeferredInteractionLoading label="基金详情" /> },
);
const FundSearchDialog = dynamic(
  () => import("@/components/FundSearchDialog").then((module) => module.FundSearchDialog),
  { loading: () => <DeferredInteractionLoading label="基金搜索" /> },
);
const FundResearchDetail = dynamic(
  () => import("@/components/FundResearchDetail").then((module) => module.FundResearchDetail),
  { loading: () => <DeferredInteractionLoading label="基金研究详情" /> },
);
const AddHoldingModal = dynamic(
  () => import("@/components/AddHoldingModal").then((module) => module.AddHoldingModal),
  { loading: () => <DeferredInteractionLoading label="添加持仓" /> },
);
const BatchTransactionModal = dynamic(
  () =>
    import("@/components/BatchTransactionModal").then(
      (module) => module.BatchTransactionModal,
    ),
  { loading: () => <DeferredInteractionLoading label="交易导入" /> },
);
const BatchTransactionConfirmModal = dynamic(
  () =>
    import("@/components/BatchTransactionConfirmModal").then(
      (module) => module.BatchTransactionConfirmModal,
    ),
  { loading: () => <DeferredInteractionLoading label="交易确认" /> },
);
const AlipayOcrConfirmModal = dynamic(
  () =>
    import("@/components/AlipayOcrConfirmModal").then(
      (module) => module.AlipayOcrConfirmModal,
    ),
  { loading: () => <DeferredInteractionLoading label="截图持仓确认" /> },
);
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

// formatter 提到模块作用域：原实现每次调用都新建一个 Intl.DateTimeFormat，
// 而它会在 orderedReports.find() 里按报告条数逐条调用。输出格式逐字不变。
const REPORT_DATE_KEY_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

function reportDateKey(value: string | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return REPORT_DATE_KEY_FORMATTER.format(date);
}

export function Dashboard() {
  const { user } = useAuth();
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [profile, setProfile] = useState<InvestorProfile>(() =>
    loadInvestorProfile(user?.id, defaultProfile),
  );
  const [analysisPrompt, setAnalysisPrompt] = useState<AnalysisPromptConfig>(() =>
    loadAnalysisPrompt(user?.id, defaultAnalysisPrompt),
  );
  const [report, setReport] = useState<Report | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  // 列表接口只返回摘要，切换到某份历史日报时按 id 拉一次完整正文。
  // 递增 requestId 保证快速连点时只应用最后一次的详情。
  const reportDetailRequestId = useRef(0);
  const [reportDetailError, setReportDetailError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [reportHistoryOpen, setReportHistoryOpen] = useState(false);
  const [portfolioSummary, setPortfolioSummary] = useState<PortfolioSummary | null>(null);
  const [holdingWarnings, setHoldingWarnings] = useState<HoldingFieldWarning[]>([]);
  // 工作台不再有任何全局提示位。成功类文案（已移除 X / 已添加 X / 已停止生成…）
  // 由界面自身承载：行消失、列表更新、浮层收起。写入失败则改为「让界面回到真相」
  // 而不是「弹一句话解释」—— 失败路径统一回滚乐观更新，屏幕上不会留下服务端
  // 并未接受的状态。
  //
  // 可诊断性不依赖 UI：apiFetch 已经通过 reportApiFailure 上报网络层失败与网关
  // 错误，普通 500 也在后端连同 traceback 记录过，所以去掉提示不会丢失线索。
  const [isSubmitting, setIsSubmitting] = useState(false);
  // 生成日报失败的原因，交给 RiskControls 挨着「生成」按钮展示。
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);
  const [activeTab, setActiveTabState] = useState<TabId>("holdings");
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
  const [fundSearchOpen, setFundSearchOpen] = useState(false);
  const [researchFund, setResearchFund] = useState<FundSearchItem | null>(null);
  const selectedHoldingIndex = useMemo(() => {
    if (!selectedHoldingKey) {
      return null;
    }
    const index = findHoldingIndex(holdings, selectedHoldingKey);
    return index >= 0 ? index : null;
  }, [holdings, selectedHoldingKey]);
  const researchHolding = useMemo(
    () =>
      researchFund
        ? holdings.find((item) => item.fund_code === researchFund.fund_code) ?? null
        : null,
    [holdings, researchFund],
  );
  const reportSectionRef = useRef<HTMLDivElement>(null);
  const refreshAfterApplyRef = useRef<"sector" | null>(null);
  const initialSectorRefreshDoneRef = useRef(false);
  const holdingsMutationVersionRef = useRef(0);
  const portfolioCacheWriteReadyRef = useRef(false);
  const portfolioMutationQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const officialNavSettlementAttemptedRef = useRef(false);
  const officialNavSettlementInFlightRef = useRef(false);
  const profilePersistReady = useRef(false);
  const promptPersistReady = useRef(false);
  const profileChangedByUserRef = useRef(false);
  const promptChangedByUserRef = useRef(false);
  const [isHydratingHoldings, setIsHydratingHoldings] = useState(true);
  const [portfolioLoadState, setPortfolioLoadState] = useState<PortfolioLoadState>("loading");
  const [portfolioLoadError, setPortfolioLoadError] = useState<string | null>(null);
  const [holdingsRefreshedAt, setHoldingsRefreshedAt] = useState<string | null>(null);
  const [holdingsPollIntervalMs, setHoldingsPollIntervalMs] = useState(180_000);
  const backgroundJobActiveRef = useRef(false);
  const holdingsForPrefetchRef = useRef(holdings);
  const holdingsPrefetchKey = useMemo(
    () =>
      displayableHoldings(holdings)
        .map((h) => h.fund_code || h.fund_name || "")
        .join("|"),
    [holdings],
  );
  const [isOcrUploading, setIsOcrUploading] = useState(false);
  const [pendingOcrHoldings, setPendingOcrHoldings] = useState<Holding[] | null>(null);
  const [pendingOcrResolutions, setPendingOcrResolutions] = useState<FundCodeResolution[]>([]);
  const [pendingOcrNote, setPendingOcrNote] = useState<string | null>(null);
  const [pendingOcrSource, setPendingOcrSource] = useState<string | null>(null);
  const [showAddHoldingModal, setShowAddHoldingModal] = useState(false);
  const [isManualAdding, setIsManualAdding] = useState(false);
  const [addHoldingError, setAddHoldingError] = useState<string | null>(null);
  const [isApplyingOcrHoldings, setIsApplyingOcrHoldings] = useState(false);
  const [ocrApplyError, setOcrApplyError] = useState<string | null>(null);
  const [ocrCompletionCount, setOcrCompletionCount] = useState<number | null>(null);
  const [showBatchModal, setShowBatchModal] = useState(false);
  const [isBatchUploading, setIsBatchUploading] = useState(false);
  const [batchUploadError, setBatchUploadError] = useState<string | null>(null);
  const [pendingTransactions, setPendingTransactions] = useState<ParsedTransaction[] | null>(null);
  const [isApplyingTransactions, setIsApplyingTransactions] = useState(false);
  const [transactionApplyError, setTransactionApplyError] = useState<string | null>(null);

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

  const blockingErrors = hasBlockingErrors(workflowBlockers);
  const blockingMessage =
    workflowBlockers.find((item) => item.severity === "error")?.message ?? null;

  const sectorRefresh = useSectorQuoteRefresh({
    holdings,
    onChange: setHoldings,
    warnings: holdingWarnings,
    onWarningsChange: setHoldingWarnings,
  });

  const swingAlerts = useSwingAlerts({
    holdings,
    profile,
    onBeforeEvaluate: async () => {
      if (holdings.length === 0) {
        return undefined;
      }
      const result = await enqueuePortfolioMutation(() => sectorRefresh.refresh(false, "fast"));
      return result?.holdings;
    },
  });

  const loadHistory = useCallback(async (): Promise<Report[] | null> => {
    setHistoryLoading(true);
    try {
      const next = [...(await listReports())].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      );
      setReports(next);
      setHistoryError(null);
      return next;
    } catch {
      setHistoryError("历史日报加载失败，当前日报仍保留。可稍后重试。");
      return null;
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  const handleProfileChange = useCallback((next: InvestorProfile) => {
    profileChangedByUserRef.current = true;
    setProfile(next);
  }, []);

  const handleRolePromptChange = useCallback((value: string) => {
    promptChangedByUserRef.current = true;
    setAnalysisPrompt((current) => ({
      ...current,
      role_prompt: value.slice(0, 2000),
      is_custom: Boolean(value.trim()),
    }));
  }, []);

  const handleRolePromptReset = useCallback(() => {
    promptChangedByUserRef.current = true;
    setAnalysisPrompt((current) => ({
      ...current,
      role_prompt: current.default_role_prompt,
      is_custom: false,
    }));
  }, []);

  const loadPortfolioSummary = async () => {
    try {
      setPortfolioSummary(await fetchPortfolioSummary());
    } catch {
      setPortfolioSummary(null);
    }
  };

  const markPortfolioCacheWriteReady = useCallback(() => {
    portfolioCacheWriteReadyRef.current = true;
  }, []);

  const enqueuePortfolioMutation = useCallback(<T,>(task: () => Promise<T>): Promise<T> => {
    const queued = portfolioMutationQueueRef.current
      .catch(() => undefined)
      .then(task);
    portfolioMutationQueueRef.current = queued.catch(() => undefined);
    return queued;
  }, []);

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
    const requestVersion = holdingsMutationVersionRef.current;
    void enqueuePortfolioMutation(() => settleOfficialNav())
      .then((settlement) => {
        if (requestVersion !== holdingsMutationVersionRef.current) {
          return;
        }
        if (
          !settlement.ok ||
          settlement.skipped ||
          !settlement.updated_count ||
          settlement.holdings.length === 0
        ) {
          return;
        }
        const refreshedAt = settlement.refreshed_at ?? null;
        const mergedHoldings = mergeHoldingsPreserveQuoteFields(sourceHoldings, settlement.holdings);
        setHoldings((current) =>
          mergeHoldingsPreserveQuoteFields(current.length ? current : sourceHoldings, settlement.holdings),
        );
        setHoldingsRefreshedAt(refreshedAt);
        if (settlement.portfolio_summary) {
          setPortfolioSummary(settlement.portfolio_summary);
        }
        markPortfolioCacheWriteReady();
        saveCachedPortfolioHoldings(user?.id, {
          holdings: mergedHoldings,
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

  const HYDRATE_INITIAL_RETRY_COUNT = 2;
  const HYDRATE_INITIAL_RETRY_DELAY_MS = 1500;

  const hydratePortfolio = async (retriesLeft = HYDRATE_INITIAL_RETRY_COUNT): Promise<void> => {
    if (backgroundJobActiveRef.current) {
      return;
    }
    const requestVersion = holdingsMutationVersionRef.current;
    const hadCachedPortfolio = loadCachedPortfolioHoldings(user?.id) !== null;
    setPortfolioLoadState(hadCachedPortfolio ? "refreshing" : "loading");
    setPortfolioLoadError(null);
    if (!hadCachedPortfolio) {
      setIsHydratingHoldings(true);
    }
    try {
      const payload = await fetchPortfolioHoldings();
      if (requestVersion !== holdingsMutationVersionRef.current) {
        return;
      }
      if (payload.portfolio_summary) {
        setPortfolioSummary(payload.portfolio_summary);
      }
      const refreshedAt = payload.refreshed_at ?? null;
      markPortfolioCacheWriteReady();
      setHoldings(payload.holdings);
      setHoldingsRefreshedAt(refreshedAt);
      setPortfolioLoadState("ready");
      setPortfolioLoadError(null);
      saveCachedPortfolioHoldings(user?.id, {
        holdings: payload.holdings,
        portfolio_summary: payload.portfolio_summary ?? null,
        refreshed_at: refreshedAt,
      });
      if (payload.holdings.length > 0) {
        settleOfficialNavInBackground(payload.holdings);
      }
      setIsHydratingHoldings(false);
    } catch {
      if (requestVersion !== holdingsMutationVersionRef.current) {
        return;
      }
      // 首次没有本地缓存时，如果第一次拉取恰好撞上后端刚重启/网络抖动等瞬时失败，
      // 不能马上把"暂未录入基金"的空状态亮出来——那会被用户误读成持仓丢了。
      // 先保持加载态，短暂延迟后自动重试几次，只有真的多次都失败才降级展示。
      if (!hadCachedPortfolio && retriesLeft > 0) {
        window.setTimeout(() => {
          void hydratePortfolio(retriesLeft - 1);
        }, HYDRATE_INITIAL_RETRY_DELAY_MS);
        return;
      }
      const loadMessage = hadCachedPortfolio
        ? "最新持仓暂时加载失败，当前显示的是上次缓存。"
        : "持仓加载失败，请确认后端 API 正常运行后重试。";
      if (!hadCachedPortfolio) {
        await loadPortfolioSummary();
      }
      setPortfolioLoadState(hadCachedPortfolio ? "stale" : "error");
      setPortfolioLoadError(loadMessage);
      setIsHydratingHoldings(false);
    }
  };

  useLayoutEffect(() => {
    portfolioCacheWriteReadyRef.current = false;
    const cached = loadCachedPortfolioHoldings(user?.id);
    if (!cached) {
      return;
    }
    markPortfolioCacheWriteReady();
    setHoldings(cached.holdings);
    setPortfolioLoadState("refreshing");
    if (cached.portfolio_summary) {
      setPortfolioSummary(cached.portfolio_summary);
    }
    if (cached.refreshed_at) {
      setHoldingsRefreshedAt(cached.refreshed_at);
    }
    setIsHydratingHoldings(false);
  }, [user?.id, markPortfolioCacheWriteReady]);

  // tab 切换的开销全在"挂载另一个重面板"上（13 个 dynamic() 之一 + 它的首屏计算）。
  // 导航高亮、页头文案与后台任务浮层继续读 activeTab，保持点击即时反馈；只有 <main>
  // 里的面板区读这个延后值，让 React 先把交互帧让给用户。可见内容与最终状态不变。
  const deferredActiveTab = useDeferredValue(activeTab);

  const setActiveTab = useCallback((tab: TabId | ((prev: TabId) => TabId)) => {
    setActiveTabState((prev) => {
      const requested = typeof tab === "function" ? tab(prev) : tab;
      const next = requested === "history" ? "report" : requested;
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
    const stored = loadDashboardTab();
    const urlReportId = new URLSearchParams(window.location.search).get("report");
    setActiveTabState(stored === "history" || urlReportId ? "report" : stored);
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
    const loadIndividually = () => {
      void (async () => {
        try {
          const remote = await fetchInvestorProfile();
          const normalized = normalizeInvestorProfile(remote, defaultProfile);
          setProfile(normalized);
          saveInvestorProfile(user?.id, normalized);
        } catch {
          setProfile((current) => loadInvestorProfile(user?.id, current));
        } finally {
          profilePersistReady.current = true;
          setProfileReady(true);
        }
      })();
      void (async () => {
        try {
          const remote = await fetchAnalysisPrompt();
          setAnalysisPrompt(remote);
          saveAnalysisPrompt(user?.id, remote);
        } catch {
          setAnalysisPrompt((current) => loadAnalysisPrompt(user?.id, current));
        } finally {
          promptPersistReady.current = true;
          setPromptReady(true);
        }
      })();
      void hydratePortfolio();
      void fetchSectorQuotesStatus()
        .then((status) =>
          setHoldingsPollIntervalMs(
            (status.auto_refresh_allowed
              ? status.auto_interval_seconds
              : status.idle_interval_seconds) * 1000,
          ),
        )
        .catch(() => undefined);
    };

    void fetchDashboardBootstrap()
      .then((bootstrap) => {
        const normalized = normalizeInvestorProfile(
          bootstrap.investor_profile,
          defaultProfile,
        );
        setProfile(normalized);
        saveInvestorProfile(user?.id, normalized);
        profilePersistReady.current = true;
        setProfileReady(true);

        setAnalysisPrompt(bootstrap.analysis_prompt);
        saveAnalysisPrompt(user?.id, bootstrap.analysis_prompt);
        promptPersistReady.current = true;
        setPromptReady(true);

        const payload = bootstrap.portfolio;
        setHoldings(payload.holdings);
        setHoldingsRefreshedAt(payload.refreshed_at ?? null);
        setPortfolioSummary(payload.portfolio_summary ?? null);
        setPortfolioLoadState("ready");
        setPortfolioLoadError(null);
        setIsHydratingHoldings(false);
        markPortfolioCacheWriteReady();
        saveCachedPortfolioHoldings(user?.id, {
          holdings: payload.holdings,
          portfolio_summary: payload.portfolio_summary ?? null,
          refreshed_at: payload.refreshed_at ?? null,
        });
        const status = bootstrap.sector_quotes_status;
        setHoldingsPollIntervalMs(
          (status.auto_refresh_allowed
            ? status.auto_interval_seconds
            : status.idle_interval_seconds) * 1000,
        );
        if (payload.holdings.length > 0) {
          settleOfficialNavInBackground(payload.holdings);
        }
      })
      .catch(loadIndividually);
    // Mount-only bootstrap; avoid re-fetching on callback identity changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (activeTab !== "report" && activeTab !== "history") return;
    void loadHistory();
  }, [activeTab, loadHistory]);

  const orderedReports = useMemo(
    () =>
      [...reports].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    [reports],
  );
  const todayKey = reportDateKey(new Date().toISOString());
  const selectedReportId = report?.id ?? null;
  // 这些派生值原来每次渲染都重算，其中 todayReport 还会按报告条数逐条格式化日期。
  // todayKey 仍逐次求值并留在依赖里，因此跨日后依然会重新判定"今日"，语义不变。
  const todayReport = useMemo(
    () => orderedReports.find((item) => reportDateKey(item.created_at) === todayKey) ?? null,
    [orderedReports, todayKey],
  );
  const currentReportIndex = useMemo(
    () => (selectedReportId ? orderedReports.findIndex((item) => item.id === selectedReportId) : -1),
    [orderedReports, selectedReportId],
  );
  const previousReport = useMemo(
    () =>
      selectedReportId
        ? orderedReports[currentReportIndex + 1] ?? null
        : orderedReports[0] ?? null,
    [currentReportIndex, orderedReports, selectedReportId],
  );
  const nextReport = useMemo(
    () => (currentReportIndex > 0 ? orderedReports[currentReportIndex - 1] : null),
    [currentReportIndex, orderedReports],
  );
  const viewingToday = !report || report.id === todayReport?.id;

  const updateReportUrl = useCallback((reportId: string | null, mode: "push" | "replace") => {
    const url = new URL(window.location.href);
    if (reportId) url.searchParams.set("report", reportId);
    else url.searchParams.delete("report");
    const nextUrl = `${url.pathname}${url.search}${url.hash}`;
    if (mode === "push") window.history.pushState({}, "", nextUrl);
    else window.history.replaceState({}, "", nextUrl);
  }, []);

  const focusReportRegion = useCallback(() => {
    window.setTimeout(() => {
      reportSectionRef.current?.focus();
      reportSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  }, []);

  // 先用列表摘要占位切换 UI，同时按 id 异步拉完整正文。
  // 列表投影不下发 holdings / snapshots / market_news / fund_recommendations /
  // recommendations，所以摘要在 listReports 里就已被 completeReportSummary 补成空数组，
  // reportPresentation 也不再假设这些字段存在。占位期间只是"证据/新闻区暂空"。
  // 早期版本以为这些字段在下游都用可选链读取，实际并没有，
  // 结果摘要占位期间 ReportPanel 会读到 undefined.length 而整页崩溃。
  //
  // hydrateReport 内部记录 lastHydratedId，避免"详情合并 setReports → orderedReports
  // 变化 → useEffect 重跑 → 又 hydrateReport(todayReport)"这条链形成无限循环。
  const lastHydratedIdRef = useRef<string | null>(null);
  const hydrateReport = useCallback((summary: Report | null) => {
    if (summary == null) {
      reportDetailRequestId.current += 1;
      lastHydratedIdRef.current = null;
      setReport(null);
      setReportDetailError(null);
      return;
    }
    if (lastHydratedIdRef.current === summary.id) {
      // 同一份已在展示（或正在懒加载中），不重复请求；只保证 UI 切到它。
      setReport((current) => (current?.id === summary.id ? current : summary));
      return;
    }
    lastHydratedIdRef.current = summary.id;
    setReport(summary);
    setReportDetailError(null);
    const requestId = ++reportDetailRequestId.current;
    void (async () => {
      try {
        const detail = await fetchReportDetail(summary.id);
        if (requestId !== reportDetailRequestId.current) return;
        setReport(detail);
        setReports((current) =>
          current.map((item) => (item.id === detail.id ? { ...item, ...detail } : item)),
        );
      } catch (error) {
        if (requestId !== reportDetailRequestId.current) return;
        setReportDetailError(
          userFacingErrorMessage(error, "日报正文加载失败，请稍后重试。"),
        );
      }
    })();
  }, []);

  const selectReportInContext = useCallback(
    (selected: Report, mode: "push" | "replace" = "push") => {
      hydrateReport(selected);
      setActiveTab("report");
      updateReportUrl(selected.id, mode);
      focusReportRegion();
    },
    [focusReportRegion, hydrateReport, setActiveTab, updateReportUrl],
  );

  const returnToToday = useCallback(() => {
    hydrateReport(todayReport);
    setActiveTab("report");
    updateReportUrl(null, "push");
    focusReportRegion();
  }, [focusReportRegion, hydrateReport, setActiveTab, todayReport, updateReportUrl]);

  const handleReportDeleted = useCallback(
    (deletedId: string) => {
      const deletedIndex = orderedReports.findIndex((item) => item.id === deletedId);
      const remaining = orderedReports.filter((item) => item.id !== deletedId);
      setReports(remaining);
      if (report?.id !== deletedId) return;
      const adjacent = remaining[Math.min(Math.max(deletedIndex, 0), remaining.length - 1)] ?? null;
      hydrateReport(adjacent);
      updateReportUrl(adjacent?.id ?? null, "replace");
    },
    [hydrateReport, orderedReports, report?.id, updateReportUrl],
  );

  // 拆成两个 effect：恢复逻辑仍按原来的依赖逐次补跑，但 popstate 监听器只在
  // 挂载/卸载时绑一次，不再因为 reports / todayReport 变化而反复解绑重绑。
  const restoreReportFromUrl = useCallback(() => {
    const urlReportId = new URLSearchParams(window.location.search).get("report");
    if (urlReportId) {
      const restored = orderedReports.find((item) => item.id === urlReportId);
      if (restored) {
        hydrateReport(restored);
        setActiveTab("report");
      }
      return;
    }
    if (activeTab === "report") hydrateReport(todayReport);
  }, [activeTab, hydrateReport, orderedReports, setActiveTab, todayReport]);
  const restoreReportFromUrlRef = useRef(restoreReportFromUrl);

  useEffect(() => {
    restoreReportFromUrlRef.current = restoreReportFromUrl;
    restoreReportFromUrl();
  }, [restoreReportFromUrl]);

  useEffect(() => {
    const onPopState = () => restoreReportFromUrlRef.current();
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    backgroundJobActiveRef.current = Boolean(
      streamingReport || streamingDiscovery || discoveryJobId || activeJobId,
    );
  }, [streamingReport, streamingDiscovery, discoveryJobId, activeJobId]);

  useEffect(() => {
    if (holdings.length === 0) {
      return;
    }
    let cancelled = false;
    let tickInFlight = false;
    const tick = async () => {
      if (cancelled || tickInFlight || document.visibilityState !== "visible") {
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
      tickInFlight = true;
      try {
        const status = await fetchSectorQuotesStatus();
        setHoldingsPollIntervalMs(
          (status.auto_refresh_allowed
            ? status.auto_interval_seconds
            : status.idle_interval_seconds) * 1000,
        );
        if (
          cancelled ||
          document.visibilityState !== "visible" ||
          !status.auto_refresh_allowed
        ) {
          return;
        }
        // 周期轮询本身只是重新读取上次持久化的持仓快照，并不会触发板块实时行情
        // 的真实刷新（那只在波段信号评估或编辑持仓后才会发生）。这里顺带触发一次
        // 真实刷新，让"当日涨幅""关联板块"等字段能像正常行情软件一样自动更新。
        // 必须走 enqueuePortfolioMutation 队列串行执行：refresh-sector-quotes 内部会把
        // 传入的持仓整份写回快照，如果和同一时间用户正在做的加仓/删除/OCR 确认并发执行，
        // 耗时更久的旧刷新可能在新的增删之后才落盘，把刚加的基金又冲掉（"批量截图录入
        // 后基金又消失"）。串行化后，同一时刻只会有一个持仓写操作在跑，彻底消除这种竞态。
        await enqueuePortfolioMutation(() => sectorRefresh.refresh(false, "fast"));
        if (cancelled || document.visibilityState !== "visible") {
          return;
        }
        await hydratePortfolio();
      } catch {
        // 后台轮询失败不阻断展示
      } finally {
        tickInFlight = false;
      }
    };
    const stopPolling = startVisibilityAwarePolling({
      intervalMs: holdingsPollIntervalMs,
      onTick: () => void tick(),
    });
    return () => {
      cancelled = true;
      stopPolling();
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
    if (refreshAfterApplyRef.current !== "sector" || holdings.length === 0) {
      return;
    }
    refreshAfterApplyRef.current = null;
    void enqueuePortfolioMutation(() => sectorRefresh.refresh(false, "fast"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [holdings]);

  useEffect(() => {
    // 板块涨跌只有在"编辑持仓后"或"波段信号评估"（默认关闭）时才会触发真实刷新，
    // 首次打开页面时展示的都是上次持久化的旧值。这里在持仓首次加载完成后主动
    // 触发一次真实板块行情刷新，避免用户什么都不做也看不到最新涨跌幅。
    if (initialSectorRefreshDoneRef.current || holdings.length === 0) {
      return;
    }
    initialSectorRefreshDoneRef.current = true;
    void enqueuePortfolioMutation(() => sectorRefresh.refresh(false, "fast"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [holdings.length]);

  useEffect(() => {
    if (!portfolioCacheWriteReadyRef.current) {
      return;
    }
    saveCachedPortfolioHoldings(user?.id, {
      holdings,
      portfolio_summary: portfolioSummary,
      refreshed_at: holdingsRefreshedAt,
    });
  }, [holdings, portfolioSummary, holdingsRefreshedAt, user?.id]);

  useEffect(() => {
    if (!sectorRefresh.lastFetchedAt) {
      return;
    }
    setHoldingsRefreshedAt(sectorRefresh.lastFetchedAt);
  }, [sectorRefresh.lastFetchedAt]);

  useEffect(() => {
    holdingsForPrefetchRef.current = holdings;
  }, [holdings]);

  useEffect(() => {
    if (activeTab !== "holdings" || holdingsForPrefetchRef.current.length === 0) {
      return;
    }
    return scheduleHoldingsDetailPrefetch({
      userId: user?.id ?? null,
      holdings: holdingsForPrefetchRef.current,
      portfolioSummary,
      sectorMetaByFundCode: sectorRefresh.sectorMetaByFundCode,
      onDetailHydrated: (detail) => {
        setHoldings((current) => patchHoldingRecord(current, detail.holding));
      },
    });
  }, [
    activeTab,
    holdingsPrefetchKey,
    portfolioSummary,
    user?.id,
    sectorRefresh.sectorMetaByFundCode,
  ]);

  useEffect(() => {
    if (!profileReady || !profilePersistReady.current) return;
    const normalized = normalizeInvestorProfile(profile, defaultProfile);
    saveInvestorProfile(user?.id, normalized);
    if (!profileChangedByUserRef.current) return;
    void saveInvestorProfileRemote(normalized).catch(() => {
      // 离线时仍保留 localStorage；下次启动会从本地缓存恢复。
    });
  }, [profile, profileReady, user?.id]);

  useEffect(() => {
    if (!promptReady || !promptPersistReady.current) return;
    saveAnalysisPrompt(user?.id, analysisPrompt);
    if (!promptChangedByUserRef.current) return;
    const storedValue = analysisPrompt.is_custom ? analysisPrompt.role_prompt : null;
    void saveAnalysisPromptRemote(storedValue).catch(() => {
      // 离线时仍保留 localStorage。
    });
  }, [analysisPrompt, promptReady, user?.id]);

  const handleCancelStream = useCallback(() => {
    streamAbortRef.current?.abort();
    streamAbortRef.current = null;
    userLeftReportDuringStreamRef.current = false;
    setStreamingReport(null);
    setIsSubmitting(false);
    // 不再提示"已停止生成。"：流式浮层与骨架屏同时消失，界面已经说明了。
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
      // 文案省略：workflowBlockers 的 no-holdings 已由 RiskControls 行内展示，
      // 且该状态下生成按钮本身是禁用的。
      return;
    }
    const systemRolePrompt = activeAnalysisRolePrompt(analysisPrompt);
    setIsSubmitting(true);
    setAnalyzeError(null);
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
            systemRolePrompt,
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
          return;
        }
        // 降级到后台分析时不再弹提示：下面会挂起 JobStatusFloat，
        // 而 markStreamingReportBackgroundFallback 也会把中断阶段写进浮层。
      }

      const jobId = await startAnalyzeJob(
        targetHoldings,
        profile,
        undefined,
        systemRolePrompt,
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
      // 生成是这一屏的主操作，失败必须说一句 —— 否则按钮弹回可用态，用户无法
      // 区分"失败了"和"点了没反应"。文案就近展示在按钮旁，不走全局提示。
      setAnalyzeError(userFacingErrorMessage(error, "提交分析任务失败，请稍后重试。"));
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
    // 生成完成的 completedReport 已含完整正文，无需再走 hydrateReport 拉一次；
    // 但仍要更新 lastHydratedId，避免随后 URL 恢复触发重复请求。
    lastHydratedIdRef.current = completedReport.id;
    reportDetailRequestId.current += 1;
    setReport(completedReport);
    setReportDetailError(null);
    await loadHistory();
    setActiveJobId(null);

    const shouldNavigate = options?.navigateToReport !== false;
    if (shouldNavigate) {
      setActiveTab("report");
      updateReportUrl(null, "replace");
      requestAnimationFrame(() => {
        reportSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    } else {
      setReportTabUnread(true);
    }

    // provider 失败时后端会 fail-closed 换成离线兜底（每条建议降为观察、金额阻断）。
    // 提示语必须跟着走，否则顶部说"已生成 Pro 深度分析"、正文却写"模型服务不可用"。
    const providerStatus = resolveReportProviderStatus(completedReport);
    notifyDesktop(
      providerStatus.modelBacked
        ? `${BRAND.name}日报已生成`
        : `${BRAND.name}日报已降级生成`,
      { body: providerStatus.modelBacked ? completedReport.title : providerStatus.message },
    );
    // 不再额外弹提示：桌面通知已带同样信息，报告正文也会标注降级来源。
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
    notifyDesktop(`${BRAND.name}推荐报告已生成`, {
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

      notifyDesktop(`${BRAND.name}推荐报告已生成`, {
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
    // 同上：扫描浮层与骨架屏消失即反馈。
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
    setAddHoldingError(null);
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
      setShowAddHoldingModal(false);
      setActiveTab("holdings");
    } catch (error) {
      // 只走弹窗内的行内错误：原来同一句话还会再推一条全局提示。
      setAddHoldingError(userFacingErrorMessage(error, "截图识别失败。"));
    } finally {
      setIsOcrUploading(false);
    }
  };

  const handleManualAddHoldings = async (newHoldings: Holding[]) => {
    if (!newHoldings.length) {
      return;
    }
    holdingsMutationVersionRef.current += 1;
    const mutationVersion = holdingsMutationVersionRef.current;
    invalidatePortfolioHoldingsRequest();
    setIsManualAdding(true);
    setAddHoldingError(null);
    try {
      // The server owns membership. Submit only the explicit upserts so a
      // stale browser tab cannot replace newer holdings it has never seen.
      const applied = await enqueuePortfolioMutation(() => applyPortfolioHoldings(newHoldings));
      if (mutationVersion !== holdingsMutationVersionRef.current) {
        return;
      }
      markPortfolioCacheWriteReady();
      setHoldings(applied.holdings);
      setHoldingWarnings(applied.holding_warnings ?? []);
      if (applied.portfolio_summary) {
        setPortfolioSummary(applied.portfolio_summary);
      }
      refreshAfterApplyRef.current = "sector";
      setShowAddHoldingModal(false);
      // 不再提示"已添加…"：弹窗关闭 + 新持仓出现在列表里就是反馈。
    } catch (error) {
      if (mutationVersion !== holdingsMutationVersionRef.current) {
        return;
      }
      setAddHoldingError(userFacingErrorMessage(error, "手动添加失败。"));
    } finally {
      setIsManualAdding(false);
    }
  };

  const handleConfirmOcrHoldings = async () => {
    if (!pendingOcrHoldings?.length) {
      return;
    }
    holdingsMutationVersionRef.current += 1;
    const mutationVersion = holdingsMutationVersionRef.current;
    invalidatePortfolioHoldingsRequest();
    const toApply = pendingOcrHoldings;
    const previousHoldings = holdings;
    setIsApplyingOcrHoldings(true);
    setOcrApplyError(null);

    try {
      const applied = await enqueuePortfolioMutation(() => applyPortfolioHoldings(toApply));
      if (mutationVersion !== holdingsMutationVersionRef.current) {
        return;
      }
      const appliedHoldings = withApplyDisplayFields(
        dedupeHoldingsByCode(
          mergeHoldingsPreserveQuoteFields(previousHoldings, applied.holdings),
        ),
      );
      const nextSummary = applied.portfolio_summary
        ? {
            ...(portfolioSummary ?? applied.portfolio_summary),
            ...applied.portfolio_summary,
            daily_profit:
              applied.portfolio_summary.daily_profit ?? portfolioSummary?.daily_profit ?? null,
            daily_return_percent:
              applied.portfolio_summary.daily_return_percent ??
              portfolioSummary?.daily_return_percent ??
              null,
          }
        : portfolioSummary;

      markPortfolioCacheWriteReady();
      setHoldings(appliedHoldings);
      setHoldingWarnings(applied.holding_warnings ?? []);
      setPortfolioSummary(nextSummary);
      saveCachedPortfolioHoldings(user?.id, {
        holdings: appliedHoldings,
        portfolio_summary: nextSummary,
        refreshed_at: holdingsRefreshedAt,
      });
      setPendingOcrHoldings(null);
      setPendingOcrResolutions([]);
      setPendingOcrNote(null);
      setPendingOcrSource(null);
      setActiveTab("holdings");
      setOcrCompletionCount(toApply.length);
      // 不再提示：上方 workflow-completion 横幅已经写了"已写入 N 只基金"。
    } catch (error) {
      if (mutationVersion !== holdingsMutationVersionRef.current) {
        return;
      }
      setOcrApplyError(userFacingErrorMessage(error, "确认更新失败。"));
    } finally {
      setIsApplyingOcrHoldings(false);
    }
  };

  const handleDeleteHolding = useCallback(
    async (index: number) => {
      const target = holdings[index];
      if (!target) {
        return;
      }

      holdingsMutationVersionRef.current += 1;
      const mutationVersion = holdingsMutationVersionRef.current;
      invalidatePortfolioHoldingsRequest();
      sectorRefresh.invalidatePendingRefresh();

      // 删除刻意不做乐观移除。
      //
      // 原实现先把该行从列表抹掉、把详情页关掉，再 fire-and-forget 发 DELETE，失败时
      // 静默回滚。于是服务端返回 503（主库不可用）时的现象是"行闪一下又回来了"，
      // 用户读作「点击无反应」。删除本身是不可逆动作，等服务端确认再落地才是对的：
      // 失败时列表从头到尾没动过，不需要回滚，也不会出现一份服务端并不存在的状态。
      // 异常向上抛给确认框，由它就地展示原因（见 YangjibaoFundDetail）。
      const result = await enqueuePortfolioMutation(() =>
        deletePortfolioHolding(target.fund_code, target.fund_name),
      );
      if (mutationVersion !== holdingsMutationVersionRef.current) {
        return;
      }
      markPortfolioCacheWriteReady();
      setHoldings(result.holdings);
      if (result.portfolio_summary) {
        setPortfolioSummary(result.portfolio_summary);
      }
      saveCachedPortfolioHoldings(user?.id, {
        holdings: result.holdings,
        portfolio_summary: result.portfolio_summary ?? null,
        refreshed_at: holdingsRefreshedAt,
      });
      setSelectedHoldingKey(null);
    },
    [
      holdings,
      holdingsRefreshedAt,
      sectorRefresh,
      user?.id,
      enqueuePortfolioMutation,
      markPortfolioCacheWriteReady,
    ],
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
    setBatchUploadError(null);
    try {
      const result = await transactionsOcr(selectedFile);
      if (!result.transactions.length) {
        throw new Error("未识别到交易记录，请确认截图为支付宝「交易记录 / 交易分析」页。");
      }
      setShowBatchModal(false);
      setPendingTransactions((prev) => mergeTransactions(prev ?? [], result.transactions));
    } catch (error) {
      setBatchUploadError(userFacingErrorMessage(error, "交易记录识别失败。"));
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
      setTransactionApplyError("没有可应用的交易，请先为交易匹配基金代码。");
      return;
    }
    holdingsMutationVersionRef.current += 1;
    const mutationVersion = holdingsMutationVersionRef.current;
    invalidatePortfolioHoldingsRequest();
    setIsApplyingTransactions(true);
    setTransactionApplyError(null);
    try {
      const result = await enqueuePortfolioMutation(() => applyTransactions(toApply));
      if (mutationVersion !== holdingsMutationVersionRef.current) {
        return;
      }
      markPortfolioCacheWriteReady();
      setHoldings(result.holdings);
      void loadPortfolioSummary();
      setPendingTransactions(null);
      // 不再提示"已应用 N 笔交易"：确认弹窗关闭、持仓列表随即更新。
    } catch (error) {
      if (mutationVersion !== holdingsMutationVersionRef.current) {
        return;
      }
      setTransactionApplyError(userFacingErrorMessage(error, "应用交易失败。"));
    } finally {
      setIsApplyingTransactions(false);
    }
  };

  const handleSingleFundTransaction = async (
    transaction: ParsedTransaction,
  ): Promise<HoldingMutationResult | null> => {
    holdingsMutationVersionRef.current += 1;
    const mutationVersion = holdingsMutationVersionRef.current;
    invalidatePortfolioHoldingsRequest();

    const result = await enqueuePortfolioMutation(() => applyTransactions([transaction]));
    let nextSummary = portfolioSummary;
    try {
      nextSummary = await fetchPortfolioSummary();
    } catch {
      // 交易已经成功写入；汇总刷新失败时沿用当前汇总，避免诱导用户重复提交交易。
    }
    if (mutationVersion !== holdingsMutationVersionRef.current) {
      return null;
    }

    markPortfolioCacheWriteReady();
    setHoldings(result.holdings);
    if (nextSummary) {
      setPortfolioSummary(nextSummary);
    }
    setPortfolioLoadState("ready");
    setPortfolioLoadError(null);
    saveCachedPortfolioHoldings(user?.id, {
      holdings: result.holdings,
      portfolio_summary: nextSummary,
      refreshed_at: holdingsRefreshedAt,
    });
    return { holdings: result.holdings, portfolioSummary: nextSummary };
  };

  const handleAdjustHolding = async (
    fundCode: string,
    patch: HoldingAdjustmentPatch,
  ): Promise<HoldingMutationResult | null> => {
    holdingsMutationVersionRef.current += 1;
    const mutationVersion = holdingsMutationVersionRef.current;
    invalidatePortfolioHoldingsRequest();

    const result = await enqueuePortfolioMutation(() => adjustHolding(fundCode, patch));
    if (mutationVersion !== holdingsMutationVersionRef.current) {
      return null;
    }

    const nextSummary = result.portfolio_summary ?? portfolioSummary;
    const nextRefreshedAt = result.refreshed_at ?? holdingsRefreshedAt;
    markPortfolioCacheWriteReady();
    setHoldings(result.holdings);
    if (result.portfolio_summary) {
      setPortfolioSummary(result.portfolio_summary);
    }
    if (result.refreshed_at) {
      setHoldingsRefreshedAt(result.refreshed_at);
    }
    setPortfolioLoadState("ready");
    setPortfolioLoadError(null);
    saveCachedPortfolioHoldings(user?.id, {
      holdings: result.holdings,
      portfolio_summary: nextSummary,
      refreshed_at: nextRefreshedAt,
    });
    return { holdings: result.holdings, portfolioSummary: nextSummary };
  };

  // 只留「英文眉标 + 中文标题」。原来每个 tab 还挂一句描述（"看清资产与收益，再决定
  // 下一步。"之类），但导航高亮已经说明了当前在哪一屏，这句话不提供新信息，却把真正的
  // 主角（总资产 / 当日收益 / 结论）挤到首屏之外。标题本身保留为 h1 语义地标，字号收小。
  const activePageMeta = {
    holdings: ["账户持仓", "PORTFOLIO"],
    dashboard: ["盈亏分析", "PERFORMANCE"],
    market: ["市场观察", "MARKET"],
    discovery: ["发现基金", "DISCOVERY"],
    report: ["投研日报", "DAILY BRIEF"],
    history: ["历史日报", "ARCHIVE"],
  }[activeTab];

  return (
    <div className="premium-bg min-h-screen">
      <a href="#main-content" className="skip-link">跳到主要内容</a>
      <div className="dashboard-shell mx-auto flex min-h-screen w-full max-w-[1240px] flex-col px-4 py-3 sm:px-6 sm:py-4">
        <header
          className="app-masthead sticky top-0 z-40 -mx-4 mb-3 flex items-center justify-between gap-4 border-b border-[var(--line)] px-4 py-2.5 sm:-mx-6 sm:px-6"
        >
          <BrandMark size="md" />
          <div className="min-w-0 flex-1">
            <DashboardNav
              activeTab={activeTab}
              reportTabUnread={reportTabUnread}
              discoveryTabUnread={discoveryTabUnread}
              onSelect={setActiveTab}
            />
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => setFundSearchOpen(true)}
              className="touch-target inline-flex items-center justify-center rounded-full text-slate-600 transition hover:bg-white hover:text-[var(--brand-strong)]"
              aria-label="搜索基金"
              title="搜索基金"
            >
              <Search size={20} />
            </button>
            <UserMenu />
          </div>
        </header>

        <section className="app-page-heading" aria-labelledby="app-page-title">
          <h1 id="app-page-title" className="font-display">{activePageMeta[0]}</h1>
          <p>{activePageMeta[1]}</p>
        </section>

        {ocrCompletionCount !== null ? (
          <section className="workflow-completion" role="status" aria-live="polite">
            <div><span aria-hidden="true">✓</span><p><strong>持仓恢复完成</strong>已写入 {ocrCompletionCount} 只基金。</p></div>
            <button type="button" onClick={() => setOcrCompletionCount(null)} className="btn-ghost min-h-11">知道了</button>
          </section>
        ) : null}

        <main id="main-content" tabIndex={-1} className="min-w-0 flex-1 pb-6">
          {deferredActiveTab === "holdings" ? (
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
                loadState={portfolioLoadState}
                loadError={portfolioLoadError}
                onRetryLoad={() => void hydratePortfolio()}
                onAddHolding={() => {
                  setAddHoldingError(null);
                  setShowAddHoldingModal(true);
                }}
                onBatchTransaction={() => {
                  setBatchUploadError(null);
                  setShowBatchModal(true);
                }}
                onSelectHolding={setSelectedHoldingKey}
              />
            </div>
          ) : null}

          {deferredActiveTab === "report" ? (
            <div className="grid min-w-0 gap-4">
              <TradingSessionBar />
              <ReportNavigator
                currentReport={report}
                reportCount={orderedReports.length}
                currentLabel={
                  report
                    ? reportDateKey(report.created_at) === todayKey
                      ? "今日日报"
                      : `历史日报 · ${reportDateKey(report.created_at) ?? "日期未知"}`
                    : "今日"
                }
                // 只留真正的增量信息：风险等级。"当前报告已选中"复述视觉状态，
                // 无报告时的两句话则是在向用户解释导航器自己怎么用。
                currentStatus={report ? `风险等级 ${report.risk.level}` : ""}
                hasPrevious={Boolean(previousReport)}
                hasNext={Boolean(nextReport)}
                canReturnToday={!viewingToday}
                historyLoading={historyLoading}
                historyError={historyError}
                onPrevious={() => {
                  if (previousReport) selectReportInContext(previousReport);
                }}
                onNext={() => {
                  if (nextReport) selectReportInContext(nextReport);
                }}
                onToday={returnToToday}
                onOpenHistory={() => setReportHistoryOpen(true)}
              />
              <RiskControls
                profile={profile}
                rolePrompt={analysisPrompt.is_custom ? analysisPrompt.role_prompt : ""}
                isRolePromptCustom={analysisPrompt.is_custom}
                onChange={handleProfileChange}
                onRolePromptChange={handleRolePromptChange}
                onRolePromptReset={handleRolePromptReset}
                onAnalyze={() => void handleAnalyze()}
                isBusy={isSubmitting}
                hasBlockingErrors={blockingErrors}
                blockingMessage={blockingMessage}
                errorMessage={analyzeError}
                readingModeKey={report?.id ?? null}
              />
              {report || streamingReport ? (
                <div
                  ref={reportSectionRef}
                  tabIndex={-1}
                  aria-label="日报阅读区"
                  className="min-w-0 scroll-mt-24 outline-none"
                >
                  {reportDetailError ? (
                    <InlineNotice
                      tone="warning"
                      message={reportDetailError}
                      className="mb-3"
                      action={
                        report
                          ? {
                              label: "重试",
                              onClick: () => {
                                lastHydratedIdRef.current = null;
                                hydrateReport(report);
                              },
                            }
                          : undefined
                      }
                    />
                  ) : null}
                  <ReportPanel
                    report={report}
                    streaming={streamingReport}
                    onCancelStream={activeJobId ? undefined : handleCancelStream}
                    onStreamFollowup={activeJobId ? undefined : handleStreamFollowup}
                    currentHoldings={
                      report?.id === todayReport?.id ? displayableHoldings(holdings) : undefined
                    }
                    diagnostics={() => (
                      <ReportDiagnostics
                        holdings={displayableHoldings(holdings)}
                        profile={profile}
                      />
                    )}
                  />
                </div>
              ) : null}
            </div>
          ) : null}

          {deferredActiveTab === "dashboard" ? (
            <PortfolioDashboard userId={user?.id ?? null} fallbackSummary={portfolioSummary} />
          ) : null}

          {deferredActiveTab === "market" ? <MarketTab /> : null}

          {deferredActiveTab === "discovery" ? (
            <FundDiscoveryPanel
              userId={user?.id ?? null}
              holdings={holdings}
              profile={profile}
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

        </main>

        {reportHistoryOpen ? (
          <ReportHistoryDrawer
            open={reportHistoryOpen}
            reports={orderedReports}
            activeReportId={report?.id}
            loading={historyLoading}
            error={historyError}
            onClose={() => setReportHistoryOpen(false)}
            onRefresh={loadHistory}
            onSelect={(selected) => selectReportInContext(selected)}
            onDeleted={handleReportDeleted}
          />
        ) : null}
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

      {/* 之前无条件挂载，导致这个 dynamic() 组件的 chunk 在工作台挂载时必然下载。
          组件在 !open 时本来就 return null，改成条件挂载后行为一致（焦点捕获与恢复
          都发生在 open 生效的 effect 里），但代码分割重新生效。 */}
      {fundSearchOpen ? (
        <FundSearchDialog
          open={fundSearchOpen}
          onClose={() => setFundSearchOpen(false)}
          onSelect={(selected) => {
            setFundSearchOpen(false);
            setResearchFund(selected);
          }}
        />
      ) : null}

      {researchFund ? (
        <FundResearchDetail
          fund={researchFund}
          holding={researchHolding}
          onClose={() => setResearchFund(null)}
        />
      ) : null}

      {selectedHoldingIndex !== null && holdings[selectedHoldingIndex] ? (
        <YangjibaoFundDetail
          holding={holdings[selectedHoldingIndex]}
          holdingIndex={selectedHoldingIndex}
          holdings={holdings}
          portfolioSummary={portfolioSummary}
          sectorMeta={sectorRefresh.sectorMetaByFundCode[holdings[selectedHoldingIndex].fund_code]}
          onClose={() => setSelectedHoldingKey(null)}
          onNavigate={(target) => {
            setSelectedHoldingKey({
              fund_code: target.fund_code,
              fund_name: target.fund_name,
            });
          }}
          onFundCodeUpdated={async (index, updated) => {
            holdingsMutationVersionRef.current += 1;
            const mutationVersion = holdingsMutationVersionRef.current;
            invalidatePortfolioHoldingsRequest();
            const rollbackHoldings = holdings;
            const next = holdings.map((item, itemIndex) => (itemIndex === index ? updated : item));
            markPortfolioCacheWriteReady();
            setHoldings(next);
            try {
              const applied = await enqueuePortfolioMutation(() => applyPortfolioHoldings([updated]));
              if (mutationVersion !== holdingsMutationVersionRef.current) {
                return;
              }
              setHoldings(applied.holdings);
              setHoldingWarnings(applied.holding_warnings ?? []);
              // 成功不提示：详情页当场显示的就是新代码。
            } catch (error) {
              if (mutationVersion !== holdingsMutationVersionRef.current) {
                return;
              }
              // 撤回乐观更新，别让界面继续显示一个服务端并不存在的代码。
              setHoldings(rollbackHoldings);
              // 然后必须把异常抛回去：调用方 handleFundCodeSave 会 catch 它并写进
              // FundCodeEditModal 的 error（那套 saving/error 插槽本来就有）。
              // 早先在这里吞掉异常，等于让改码弹窗"保存成功似地"关闭 —— 和删除
              // 那个 bug 是同一个形状。
              throw error;
            }
          }}
          onHoldingResolved={(_index, resolved) => {
            setHoldings((current) => {
              const targetIndex = findHoldingIndex(current, resolved);
              if (targetIndex < 0) {
                return current;
              }
              return patchHoldingRecord(current, resolved);
            });
          }}
          onDeleteHolding={handleDeleteHolding}
          onAdjustHolding={handleAdjustHolding}
          onApplyTransaction={handleSingleFundTransaction}
        />
      ) : null}

      {showAddHoldingModal ? (
        <AddHoldingModal
          open={showAddHoldingModal}
          onClose={() => {
            setShowAddHoldingModal(false);
            setAddHoldingError(null);
          }}
          onUpload={(file) => void handleOcrUpload(file)}
          onManualSubmit={(items) => handleManualAddHoldings(items)}
          isUploading={isOcrUploading}
          isSubmitting={isManualAdding}
          errorMessage={addHoldingError}
        />
      ) : null}

      {showBatchModal ? (
        <BatchTransactionModal
          open={showBatchModal}
          onClose={() => {
            setShowBatchModal(false);
            setBatchUploadError(null);
          }}
          onUpload={(file) => void handleBatchUpload(file)}
          isUploading={isBatchUploading}
          errorMessage={batchUploadError}
        />
      ) : null}

      {pendingTransactions && !showBatchModal ? (
        <BatchTransactionConfirmModal
          transactions={pendingTransactions}
          isBusy={isApplyingTransactions}
          errorMessage={transactionApplyError}
          onChange={(transactions) => {
            setTransactionApplyError(null);
            setPendingTransactions(transactions);
          }}
          onConfirm={() => void handleApplyTransactions()}
          onContinueUpload={() => {
            setBatchUploadError(null);
            setTransactionApplyError(null);
            setShowBatchModal(true);
          }}
          onClose={() => {
            setTransactionApplyError(null);
            setPendingTransactions(null);
          }}
        />
      ) : null}

      {pendingOcrHoldings ? (
        <AlipayOcrConfirmModal
          holdings={pendingOcrHoldings}
          fundCodeResolutions={pendingOcrResolutions}
          amountSemanticsNote={pendingOcrNote}
          ocrSource={pendingOcrSource}
          isBusy={isApplyingOcrHoldings}
          errorMessage={ocrApplyError}
          onChange={(nextHoldings) => {
            setOcrApplyError(null);
            setPendingOcrHoldings(nextHoldings);
          }}
          onConfirm={() => void handleConfirmOcrHoldings()}
          onClose={() => {
            setOcrApplyError(null);
            setPendingOcrHoldings(null);
            setPendingOcrResolutions([]);
            setPendingOcrNote(null);
            setPendingOcrSource(null);
          }}
        />
      ) : null}
      <FocusSectorToast />
    </div>
  );
}
