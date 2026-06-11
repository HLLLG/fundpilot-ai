"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BrainCircuit, X } from "lucide-react";
import type {
  AnalysisMode,
  FundCodeResolution,
  Holding,
  HoldingFieldWarning,
  InvestorProfile,
  Report,
} from "@/lib/api";
import {
  fetchInvestorProfile,
  fetchPortfolioHoldings,
  fetchPortfolioSummary,
  listReports,
  applyPortfolioHoldings,
  parseOcrUpload,
  saveInvestorProfileRemote,
  startAnalyzeJob,
  type PortfolioSummary,
} from "@/lib/api";
import { AddHoldingModal } from "@/components/AddHoldingModal";
import { AlipayOcrConfirmModal } from "@/components/AlipayOcrConfirmModal";
import { notifyDesktop } from "@/lib/notifications";
import {
  loadAnalysisMode,
  loadInvestorProfile,
  normalizeInvestorProfile,
  saveAnalysisMode,
  saveInvestorProfile,
} from "@/lib/storage";
import { HistoryRail } from "@/components/HistoryRail";
import { JobStatusFloat } from "@/components/JobStatusFloat";
import { displayableHoldings } from "@/lib/holdingMetrics";
import { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";
import { buildWorkflowBlockers, hasBlockingErrors } from "@/lib/workflowBlockers";
import { TodayBlockingChecklist } from "@/components/TodayBlockingChecklist";
import { TradingSessionBar } from "@/components/TradingSessionBar";
import { DatabaseBackupPanel } from "@/components/DatabaseBackupPanel";
import { PortfolioDashboard } from "@/components/PortfolioDashboard";
import { ReportPanel } from "@/components/ReportPanel";
import { YangjibaoHoldingsBoard } from "@/components/YangjibaoHoldingsBoard";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";
import { NewsPreviewPanel } from "@/components/NewsPreviewPanel";
import { RecommendationAccuracyPanel } from "@/components/RecommendationAccuracyPanel";
import { SectorSignalBacktestPanel } from "@/components/SectorSignalBacktestPanel";
import { RiskControls } from "@/components/RiskControls";
import { DiagnosticsAccordion } from "@/components/DiagnosticsAccordion";
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
};

type TabId = "today" | "report" | "history" | "dashboard";

const primaryTabs: Array<{
  id: Extract<TabId, "today" | "dashboard" | "report">;
  label: string;
}> = [
  { id: "today", label: "持有" },
  { id: "dashboard", label: "盈亏分析" },
  { id: "report", label: "生成日报" },
];

export function Dashboard() {
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [profile, setProfile] = useState<InvestorProfile>(() =>
    loadInvestorProfile(defaultProfile),
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
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [selectedHoldingIndex, setSelectedHoldingIndex] = useState<number | null>(null);
  const reportSectionRef = useRef<HTMLDivElement>(null);
  const shouldRefreshOnLoad = useRef(false);
  const profilePersistReady = useRef(false);
  const [isHydratingHoldings, setIsHydratingHoldings] = useState(true);
  const [isOcrUploading, setIsOcrUploading] = useState(false);
  const [pendingOcrHoldings, setPendingOcrHoldings] = useState<Holding[] | null>(null);
  const [pendingOcrResolutions, setPendingOcrResolutions] = useState<FundCodeResolution[]>([]);
  const [pendingOcrNote, setPendingOcrNote] = useState<string | null>(null);
  const [isConfirmingOcr, setIsConfirmingOcr] = useState(false);
  const [showAddHoldingModal, setShowAddHoldingModal] = useState(false);
  const [isManualAdding, setIsManualAdding] = useState(false);

  const todayIso = new Date().toISOString().slice(0, 10);
  const todayReport = useMemo(() => {
    if (report?.created_at?.slice(0, 10) === todayIso) {
      return report;
    }
    return null;
  }, [report, todayIso]);
  const displayReport = todayReport;

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

  const loadHistory = async () => {
    try {
      setReports(await listReports());
    } catch {
      setReports([]);
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
      const jobId = await startAnalyzeJob(targetHoldings, profile, undefined, analysisMode);
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

  const handleOverviewUpload = async (selectedFile: File) => {
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
        throw new Error("未识别到基金持仓，请确认截图为支付宝「我的持有」或养基宝总览。");
      }
      setPendingOcrHoldings(result.holdings);
      setPendingOcrResolutions(result.fund_code_resolutions ?? []);
      setPendingOcrNote(result.amount_semantics?.note ?? null);
      setHoldingWarnings(result.holding_warnings ?? []);
      setShowAddHoldingModal(false);
      setActiveTab("today");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "总览截图识别失败。");
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
      const applied = await applyPortfolioHoldings(pendingOcrHoldings);
      setHoldings(applied.holdings);
      if (applied.portfolio_summary) {
        setPortfolioSummary(applied.portfolio_summary);
      }
      setPendingOcrHoldings(null);
      setPendingOcrResolutions([]);
      setPendingOcrNote(null);
      setMessage(`已更新 ${count} 只基金的账户汇总，板块涨跌已刷新。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "确认更新失败。");
    } finally {
      setIsConfirmingOcr(false);
    }
  };

  return (
    <main className="premium-bg min-h-screen">
      <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col px-4 py-3 sm:px-5 sm:py-4">
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
              <YangjibaoHoldingsBoard
                holdings={holdings}
                portfolioSummary={portfolioSummary}
                sectorRefresh={sectorRefresh}
                isLoading={isHydratingHoldings}
                className="max-w-none"
                onAddHolding={() => setShowAddHoldingModal(true)}
                onSelectHolding={setSelectedHoldingIndex}
              />
            </div>
          ) : null}

          {activeTab === "report" ? (
            <div className="grid min-w-0 gap-4">
              <TradingSessionBar />
              <TodayBlockingChecklist blockers={workflowBlockers} />
              <RiskControls
                profile={profile}
                analysisMode={analysisMode}
                onAnalysisModeChange={setAnalysisMode}
                onChange={setProfile}
                onAnalyze={() => void handleAnalyze()}
                isBusy={isSubmitting}
                ocrWarningCount={ocrWarningCount}
                hasBlockingErrors={blockingErrors}
              />
              <div ref={reportSectionRef} className="min-w-0">
                {todayReport ? (
                  <ReportPanel report={todayReport} />
                ) : (
                  <div className="section-card px-4 py-10 text-center text-sm text-slate-500">
                    确认持仓后，点击上方按钮生成今日操作建议。
                  </div>
                )}
              </div>
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

          {activeTab === "history" ? (
            <div className="grid gap-6">
              <SectorSignalBacktestPanel title="板块信号历史回测（全部 canonical）" />
              <DatabaseBackupPanel
              onImported={() => {
                void loadHistory();
                void hydratePortfolio();
              }}
              />
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

      <JobStatusFloat
        jobId={activeJobId}
        onComplete={(completedReport) => void handleJobComplete(completedReport)}
        onClose={handleJobClose}
        onRetry={() => void handleJobRetry()}
      />

      {selectedHoldingIndex !== null && holdings[selectedHoldingIndex] ? (
        <YangjibaoFundDetail
          holding={holdings[selectedHoldingIndex]}
          holdingIndex={selectedHoldingIndex}
          holdings={holdings}
          portfolioSummary={portfolioSummary}
          sectorMeta={sectorRefresh.sectorMetaByFundCode[holdings[selectedHoldingIndex].fund_code]}
          onClose={() => setSelectedHoldingIndex(null)}
          onNavigate={setSelectedHoldingIndex}
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
        onUpload={(file) => void handleOverviewUpload(file)}
        onManualSubmit={(items) => handleManualAddHoldings(items)}
        isUploading={isOcrUploading}
        isSubmitting={isManualAdding}
      />

      {pendingOcrHoldings ? (
        <AlipayOcrConfirmModal
          holdings={pendingOcrHoldings}
          fundCodeResolutions={pendingOcrResolutions}
          amountSemanticsNote={pendingOcrNote}
          isBusy={isConfirmingOcr}
          onChange={setPendingOcrHoldings}
          onConfirm={() => void handleConfirmOcrHoldings()}
          onClose={() => {
            setPendingOcrHoldings(null);
            setPendingOcrResolutions([]);
            setPendingOcrNote(null);
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
  onSelect: (tab: Extract<TabId, "today" | "dashboard" | "report">) => void;
}) {
  const highlightedTab =
    activeTab === "today" || activeTab === "dashboard" || activeTab === "report"
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
