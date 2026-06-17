"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrainCircuit, X } from "lucide-react";
import type {
  AnalysisMode,
  AnalysisPromptConfig,
  FundCodeResolution,
  FundDiscoveryReport,
  FundProfile,
  Holding,
  HoldingFieldWarning,
  InvestorProfile,
  Report,
} from "@/lib/api";
import {
  fetchAnalysisPrompt,
  fetchInvestorProfile,
  fetchPortfolioHoldings,
  fetchPortfolioSummary,
  listReports,
  applyPortfolioHoldings,
  parseOcrUpload,
  saveAnalysisPromptRemote,
  saveInvestorProfileRemote,
  startAnalyzeJob,
  type PortfolioSummary,
} from "@/lib/api";
import { AddHoldingModal } from "@/components/AddHoldingModal";
import { AlipayOcrConfirmModal } from "@/components/AlipayOcrConfirmModal";
import { notifyDesktop } from "@/lib/notifications";
import {
  loadAnalysisMode,
  loadAnalysisPrompt,
  loadInvestorProfile,
  normalizeInvestorProfile,
  saveAnalysisMode,
  saveAnalysisPrompt,
  saveInvestorProfile,
} from "@/lib/storage";
import { HistoryRail } from "@/components/HistoryRail";
import { BackgroundJobsStack } from "@/components/BackgroundJobsStack";
import { DiscoveryJobStatusFloat } from "@/components/DiscoveryJobStatusFloat";
import { JobStatusFloat } from "@/components/JobStatusFloat";
import { displayableHoldings } from "@/lib/holdingMetrics";
import { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";
import { useSwingAlerts } from "@/lib/useSwingAlerts";
import { SwingAlertsPanel } from "@/components/SwingAlertsPanel";
import { buildWorkflowBlockers, hasBlockingErrors } from "@/lib/workflowBlockers";
import { TradingSessionBar } from "@/components/TradingSessionBar";
import { PortfolioDashboard } from "@/components/PortfolioDashboard";
import { ReportPanel } from "@/components/ReportPanel";
import { YangjibaoHoldingsBoard } from "@/components/YangjibaoHoldingsBoard";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";
import { NewsPreviewPanel } from "@/components/NewsPreviewPanel";
import { RecommendationAccuracyPanel } from "@/components/RecommendationAccuracyPanel";
import { SectorSignalBacktestPanel } from "@/components/SectorSignalBacktestPanel";
import { RiskControls } from "@/components/RiskControls";
import { DiagnosticsAccordion } from "@/components/DiagnosticsAccordion";
import { FundDiscoveryPanel } from "@/components/FundDiscoveryPanel";
import { MarketTab } from "@/components/MarketTab";
import { UserMenu } from "@/components/UserMenu";
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

type TabId = "today" | "report" | "history" | "dashboard" | "market" | "discovery";

const primaryTabs: Array<{
  id: Extract<TabId, "today" | "dashboard" | "market" | "discovery" | "report">;
  label: string;
}> = [
  { id: "today", label: "持有" },
  { id: "dashboard", label: "盈亏分析" },
  { id: "market", label: "市场" },
  { id: "discovery", label: "推荐基金" },
  { id: "report", label: "生成日报" },
];

const defaultAnalysisPrompt: AnalysisPromptConfig = {
  role_prompt: "",
  is_custom: false,
  default_role_prompt: "",
};

export function Dashboard() {
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
  const [activeTab, setActiveTab] = useState<TabId>("today");
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("deep");
  const [profileReady, setProfileReady] = useState(false);
  const [promptReady, setPromptReady] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [discoveryJobId, setDiscoveryJobId] = useState<string | null>(null);
  const [pendingDiscoveryReport, setPendingDiscoveryReport] = useState<FundDiscoveryReport | null>(
    null,
  );
  const discoveryScanRetryRef = useRef<(() => void) | null>(null);
  const [selectedHoldingIndex, setSelectedHoldingIndex] = useState<number | null>(null);
  const reportSectionRef = useRef<HTMLDivElement>(null);
  const shouldRefreshOnLoad = useRef(false);
  const profilePersistReady = useRef(false);
  const promptPersistReady = useRef(false);
  const [isHydratingHoldings, setIsHydratingHoldings] = useState(true);
  const [isOcrUploading, setIsOcrUploading] = useState(false);
  const [pendingOcrHoldings, setPendingOcrHoldings] = useState<Holding[] | null>(null);
  const [pendingOcrResolutions, setPendingOcrResolutions] = useState<FundCodeResolution[]>([]);
  const [pendingOcrNote, setPendingOcrNote] = useState<string | null>(null);
  const [pendingOcrSource, setPendingOcrSource] = useState<string | null>(null);
  const [pendingDetailProfile, setPendingDetailProfile] = useState<FundProfile | null>(null);
  const [isConfirmingOcr, setIsConfirmingOcr] = useState(false);
  const [showAddHoldingModal, setShowAddHoldingModal] = useState(false);
  const [isManualAdding, setIsManualAdding] = useState(false);

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

  const hydratePortfolio = async () => {
    setIsHydratingHoldings(true);
    try {
      const payload = await fetchPortfolioHoldings();
      if (payload.portfolio_summary) {
        setPortfolioSummary(payload.portfolio_summary);
      }
      if (payload.holdings.length > 0) {
        setHoldings(payload.holdings);
        shouldRefreshOnLoad.current = true;
      }
    } catch {
      await loadPortfolioSummary();
      setMessage("持仓加载失败，请确认后端 API 正常运行后刷新页面。");
    } finally {
      setIsHydratingHoldings(false);
    }
  };

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
    if (!shouldRefreshOnLoad.current || holdings.length === 0) {
      return;
    }
    shouldRefreshOnLoad.current = false;
    void sectorRefresh.refresh(false);
    // One-shot refresh after portfolio hydration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [holdings.length]);

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

  const runAnalyze = async (targetHoldings: Holding[]) => {
    if (!targetHoldings.length) {
      setMessage("请先上传截图或录入至少一条持仓。");
      return;
    }
    setIsSubmitting(true);
    setMessage(null);
    try {
      const jobId = await startAnalyzeJob(
        targetHoldings,
        profile,
        undefined,
        analysisMode,
        analysisPrompt.role_prompt,
      );
      setActiveJobId(jobId);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提交分析任务失败。");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleAnalyze = async () => {
    await runAnalyze(displayableHoldings(holdings));
  };

  const handleJobComplete = async (completedReport: Report) => {
    setReport(completedReport);
    await loadHistory();
    setActiveTab("report");
    setActiveJobId(null);
    requestAnimationFrame(() => {
      reportSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
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
          "未识别到基金持仓，请确认截图为支付宝「我的持有」、养基宝总览或养基宝单基金详情。",
        );
      }
      setPendingOcrHoldings(result.holdings);
      setPendingOcrResolutions(result.fund_code_resolutions ?? []);
      setPendingOcrNote(result.amount_semantics?.note ?? null);
      setPendingOcrSource(result.ocr_source ?? null);
      setPendingDetailProfile(result.detail_profile ?? null);
      setHoldingWarnings(result.holding_warnings ?? []);
      setShowAddHoldingModal(false);
      setActiveTab("today");
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

  const handleConfirmOcrHoldings = async () => {
    if (!pendingOcrHoldings?.length) {
      return;
    }
    const count = pendingOcrHoldings.length;
    setIsConfirmingOcr(true);
    try {
      const detailProfiles =
        pendingDetailProfile && pendingOcrHoldings[0]
          ? [
              {
                ...pendingDetailProfile,
                fund_code:
                  pendingOcrHoldings[0].fund_code !== "000000"
                    ? pendingOcrHoldings[0].fund_code
                    : pendingDetailProfile.fund_code,
                fund_name: pendingOcrHoldings[0].fund_name,
                holding_amount: pendingOcrHoldings[0].holding_amount,
                holding_profit: pendingOcrHoldings[0].holding_profit,
                sector_name:
                  pendingOcrHoldings[0].sector_name ?? pendingDetailProfile.sector_name,
                sector_return_percent:
                  pendingOcrHoldings[0].sector_return_percent ??
                  pendingDetailProfile.sector_return_percent,
              },
            ]
          : pendingDetailProfile
            ? [pendingDetailProfile]
            : [];
      const applied = await applyPortfolioHoldings(pendingOcrHoldings, {
        detailProfiles,
      });
      setHoldings(applied.holdings);
      if (applied.portfolio_summary) {
        setPortfolioSummary(applied.portfolio_summary);
      }
      setPendingOcrHoldings(null);
      setPendingOcrResolutions([]);
      setPendingOcrNote(null);
      setPendingOcrSource(null);
      setPendingDetailProfile(null);
      setMessage(
        pendingOcrSource === "yangjibao_detail"
          ? "详情页已建档并刷新板块涨跌。"
          : `已更新 ${count} 只基金的账户汇总，板块涨跌已刷新。`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "确认更新失败。");
    } finally {
      setIsConfirmingOcr(false);
    }
  };

  return (
    <main className="premium-bg min-h-screen">
      <div className="mx-auto flex min-h-screen w-full max-w-6xl flex-col px-4 py-3 sm:px-5 sm:py-4">
        <nav className="relative z-40 mb-3 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--brand)] text-white">
              <BrainCircuit size={18} />
            </div>
            <span className="text-sm font-black tracking-tight text-slate-950">FundPilot</span>
          </div>
          <UserMenu onNavigate={setActiveTab} />
        </nav>

        <TabNav activeTab={activeTab} onSelect={setActiveTab} />

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
          {activeTab === "today" ? (
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
                isLoading={isHydratingHoldings}
                onAddHolding={() => setShowAddHoldingModal(true)}
                onSelectHolding={setSelectedHoldingIndex}
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
              {report ? (
                <div ref={reportSectionRef} className="min-w-0">
                  <ReportPanel report={report} />
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
          onClose={() => setSelectedHoldingIndex(null)}
          onNavigate={setSelectedHoldingIndex}
          onUploadDetailScreenshot={(file) => void handleOcrUpload(file)}
          isDetailOcrUploading={isOcrUploading}
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

      {pendingOcrHoldings ? (
        <AlipayOcrConfirmModal
          holdings={pendingOcrHoldings}
          fundCodeResolutions={pendingOcrResolutions}
          amountSemanticsNote={pendingOcrNote}
          ocrSource={pendingOcrSource}
          isBusy={isConfirmingOcr}
          onChange={setPendingOcrHoldings}
          onConfirm={() => void handleConfirmOcrHoldings()}
          onClose={() => {
            setPendingOcrHoldings(null);
            setPendingOcrResolutions([]);
            setPendingOcrNote(null);
            setPendingOcrSource(null);
            setPendingDetailProfile(null);
          }}
        />
      ) : null}
    </main>
  );
}

function TabNav({
  activeTab,
  onSelect,
}: {
  activeTab: TabId;
  onSelect: (tab: Extract<TabId, "today" | "dashboard" | "market" | "discovery" | "report">) => void;
}) {
  const highlightedTab =
    activeTab === "today" ||
    activeTab === "dashboard" ||
    activeTab === "market" ||
    activeTab === "discovery" ||
    activeTab === "report"
      ? activeTab
      : null;

  return (
    <div className="tab-segment mb-3">
      {primaryTabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          onClick={() => onSelect(tab.id)}
          aria-current={tab.id === highlightedTab ? "page" : undefined}
          className="tab-segment-btn"
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
