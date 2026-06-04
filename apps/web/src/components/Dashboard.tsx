"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, BrainCircuit, FileText, Sun, X } from "lucide-react";
import type {
  AnalysisMode,
  FundProfile,
  Holding,
  HoldingFieldWarning,
  HoldingListDiff,
  InvestorProfile,
  Report,
} from "@/lib/api";
import {
  fetchPortfolioHoldings,
  fetchPortfolioSummary,
  importFundProfiles,
  listFundProfiles,
  listReports,
  parseFundProfile,
  startAnalyzeJob,
  type PortfolioSummary,
} from "@/lib/api";
import { notifyDesktop } from "@/lib/notifications";
import {
  loadAnalysisMode,
  loadInvestorProfile,
  saveAnalysisMode,
  saveInvestorProfile,
} from "@/lib/storage";
import { FundProfilePanel } from "@/components/FundProfilePanel";
import { HistoryRail } from "@/components/HistoryRail";
import { HoldingTable } from "@/components/HoldingTable";
import { JobStatusFloat } from "@/components/JobStatusFloat";
import { mergeHoldingsWithPrevious } from "@/lib/holdingReview";
import { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";
import { buildWorkflowBlockers, hasBlockingErrors } from "@/lib/workflowBlockers";
import { TodayBlockingChecklist } from "@/components/TodayBlockingChecklist";
import { TradingSessionBar } from "@/components/TradingSessionBar";
import { DatabaseBackupPanel } from "@/components/DatabaseBackupPanel";
import { PortfolioDashboard } from "@/components/PortfolioDashboard";
import { ReportPanel } from "@/components/ReportPanel";
import {
  CollapsibleReviewSection,
  YangjibaoHoldingsBoard,
} from "@/components/YangjibaoHoldingsBoard";
import { YangjibaoFundDetail } from "@/components/YangjibaoFundDetail";
import { RiskControls } from "@/components/RiskControls";
import { StatusPill } from "@/components/StatusPill";
import { TodayWorkflowSteps } from "@/components/TodayWorkflowSteps";
import { UserMenu } from "@/components/UserMenu";
const defaultProfile: InvestorProfile = {
  style: "稳健",
  horizon: "半年到一年",
  max_drawdown_percent: 8,
  concentration_limit_percent: 35,
  prefer_dca: true,
  avoid_chasing: true,
};

type TabId = "today" | "report" | "history" | "dashboard" | "profiles";

const primaryTabs: Array<{
  id: Extract<TabId, "today" | "report">;
  label: string;
  description: string;
  icon: React.ReactNode;
}> = [
  {
    id: "today",
    label: "今日",
    description: "账户汇总与板块涨跌",
    icon: <Sun size={17} />,
  },
  {
    id: "report",
    label: "生成日报",
    description: "校对持仓与 AI 日报",
    icon: <FileText size={17} />,
  },
];

export function Dashboard() {
  const [rawText] = useState("");
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [profile, setProfile] = useState<InvestorProfile>(defaultProfile);
  const [report, setReport] = useState<Report | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [profiles, setProfiles] = useState<FundProfile[]>([]);
  const [portfolioSummary, setPortfolioSummary] = useState<PortfolioSummary | null>(null);
  const [holdingWarnings, setHoldingWarnings] = useState<HoldingFieldWarning[]>([]);
  const [holdingDiffs] = useState<HoldingListDiff[]>([]);
  const [previousHoldings] = useState<Holding[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isProfiling, setIsProfiling] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>("today");
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("deep");
  const [profileReady, setProfileReady] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [reviewTableOpen, setReviewTableOpen] = useState(false);
  const [selectedHoldingIndex, setSelectedHoldingIndex] = useState<number | null>(null);
  const reportSectionRef = useRef<HTMLDivElement>(null);
  const shouldRefreshOnLoad = useRef(false);
  const [isHydratingHoldings, setIsHydratingHoldings] = useState(true);

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
        portfolioSummary,
        hasReportToday: Boolean(
          report?.created_at?.slice(0, 10) === new Date().toISOString().slice(0, 10),
        ),
      }),
    [holdings, holdingWarnings, profile, portfolioSummary, report?.created_at],
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

  const addEmptyHolding = () => {
    setHoldings([
      ...holdings,
      {
        fund_code: "000000",
        fund_name: "待录入基金",
        holding_amount: 0,
        return_percent: 0,
        daily_profit: null,
        daily_return_percent: null,
        holding_profit: null,
        holding_return_percent: null,
        sector_name: "",
        sector_return_percent: null,
      },
    ]);
    setReviewTableOpen(true);
  };

  const loadHistory = async () => {
    try {
      setReports(await listReports());
    } catch {
      setReports([]);
    }
  };

  const loadProfiles = async () => {
    try {
      setProfiles(await listFundProfiles());
    } catch {
      setProfiles([]);
    }
  };

  const loadPortfolioSummary = async () => {
    try {
      const summary = await fetchPortfolioSummary();
      setPortfolioSummary(summary);
      if (summary.profiles?.length) {
        setProfiles(summary.profiles);
      }
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
      await loadProfiles();
    } catch {
      await loadPortfolioSummary();
      await loadProfiles();
    } finally {
      setIsHydratingHoldings(false);
    }
  };

  useEffect(() => {
    setProfile(loadInvestorProfile(defaultProfile));
    setAnalysisMode(loadAnalysisMode("deep"));
    setProfileReady(true);
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
    if (!profileReady) return;
    saveInvestorProfile(profile);
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
      const jobId = await startAnalyzeJob(targetHoldings, profile, rawText, analysisMode);
      setActiveJobId(jobId);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提交分析任务失败。");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleAnalyze = async () => {
    await runAnalyze(holdings);
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
    await runAnalyze(holdings);
  };

  const handleImportProfiles = async (selectedFile: File) => {
    try {
      const text = await selectedFile.text();
      const payload = JSON.parse(text) as { profiles?: FundProfile[] };
      const profilesToImport = payload.profiles ?? (Array.isArray(payload) ? payload : []);
      if (!Array.isArray(profilesToImport) || profilesToImport.length === 0) {
        throw new Error("JSON 中未找到 profiles 数组。");
      }
      const result = await importFundProfiles(profilesToImport);
      await loadProfiles();
      setMessage(`已导入 ${result.saved} 条基金档案。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导入档案失败。");
    }
  };

  const handleProfileForm = async (formData: FormData) => {
    setIsProfiling(true);
    setMessage(null);
    try {
      const profileResult = await parseFundProfile(formData);
      await loadProfiles();
      if (profileResult.synced_holdings?.length) {
        setHoldings(profileResult.synced_holdings);
        if (profileResult.portfolio_summary) {
          setPortfolioSummary(profileResult.portfolio_summary);
        }
      } else {
        await hydratePortfolio();
      }
      setActiveTab("today");
      setMessage(`基金档案已保存：${profileResult.fund_name}（${profileResult.fund_code}），账户汇总已同步。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "基金详情建档失败。");
    } finally {
      setIsProfiling(false);
    }
  };

  const handleProfileFile = (selectedFile: File) => {
    const formData = new FormData();
    formData.append("file", selectedFile);
    void handleProfileForm(formData);
  };

  return (
    <main className="premium-bg min-h-screen">
      <div className="mx-auto flex min-h-screen w-full max-w-[1520px] flex-col px-4 py-4 sm:px-6 lg:px-8 lg:py-6">
        <nav className="animate-fade-up relative z-40 mb-4 flex items-center justify-between gap-4 rounded-2xl border border-white/80 bg-white/90 px-4 py-3 shadow-sm backdrop-blur-sm lg:mb-5 lg:rounded-full">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[var(--brand)] text-white shadow-[0_10px_24px_rgba(21,101,232,0.28)] lg:h-11 lg:w-11 lg:rounded-full">
              <BrainCircuit size={20} />
            </div>
            <div>
              <div className="text-sm font-black text-slate-950">FundPilot AI</div>
              <div className="text-xs text-slate-500">私人基金投研助手</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="hidden items-center gap-2 md:flex">
              <StatusPill tone="blue">本地优先</StatusPill>
              <StatusPill tone="green">DeepSeek V4 Pro</StatusPill>
            </div>
            <UserMenu onNavigate={setActiveTab} />
          </div>
        </nav>

        <header className="animate-fade-up mb-4 lg:mb-5">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <StatusPill tone="dark">工作台</StatusPill>
            <StatusPill tone="blue">截图 → 日报</StatusPill>
          </div>
          <h1 className="max-w-3xl text-2xl font-black leading-tight tracking-tight text-slate-950 sm:text-3xl lg:text-[2rem]">
            养基宝式持仓看板，真实板块优先，失败时估值兜底
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
            「今日」只看账户汇总；生成 AI 日报请切到「生成日报」。历史报告、档案与仪表盘在右上角用户菜单。
          </p>
        </header>

        {message ? (
          <div
            className="animate-fade-up mb-4 flex items-start justify-between gap-3 rounded-2xl border border-blue-100 bg-white px-4 py-3 text-sm font-semibold text-slate-700 shadow-sm lg:rounded-3xl lg:px-5 lg:py-4"
            role="status"
          >
            <span className="leading-6">{message}</span>
            <div className="flex shrink-0 items-center gap-2">
              <ArrowRight className="hidden text-blue-600 sm:block" size={18} />
              <button
                type="button"
                onClick={() => setMessage(null)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                aria-label="关闭提示"
              >
                <X size={16} />
              </button>
            </div>
          </div>
        ) : null}

        <TabNav activeTab={activeTab} onSelect={setActiveTab} />

        <div className="min-w-0 flex-1">
          {activeTab === "today" ? (
            <div className="mx-auto w-full max-w-xl">
              <YangjibaoHoldingsBoard
                holdings={holdings}
                portfolioSummary={portfolioSummary}
                sectorRefresh={sectorRefresh}
                isLoading={isHydratingHoldings}
                className="max-w-none"
                onOpenCapture={() => setActiveTab("profiles")}
                onAddHolding={() => {
                  addEmptyHolding();
                  setActiveTab("report");
                }}
                onExpandReview={() => {
                  setReviewTableOpen(true);
                  setActiveTab("report");
                }}
                onSelectHolding={setSelectedHoldingIndex}
              />
            </div>
          ) : null}

          {activeTab === "report" ? (
            <div className="grid min-w-0 gap-5 lg:gap-6">
              <TradingSessionBar />
              <TodayWorkflowSteps hasHoldings={holdings.length > 0} hasReport={Boolean(displayReport)} />
              <TodayBlockingChecklist blockers={workflowBlockers} />
              <div className="grid min-w-0 gap-5 lg:grid-cols-2 lg:gap-6">
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
                <div className="glass-panel rounded-[24px] p-5 lg:rounded-[28px]">
                  <div className="mb-2 text-sm font-black text-slate-950">快捷操作</div>
                  <p className="text-sm leading-6 text-slate-600">
                    需要更新持有金额？在右上角用户菜单打开「基金档案」上传详情截图，或在下方校对表手动修改。
                  </p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => setActiveTab("profiles")}
                      className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-700 transition hover:border-blue-200 hover:text-blue-700"
                    >
                      上传详情截图
                    </button>
                    {holdings.length > 0 ? (
                      <button
                        type="button"
                        onClick={() => setReviewTableOpen(true)}
                        className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-700 transition hover:border-blue-200 hover:text-blue-700"
                      >
                        校对持仓
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
              {holdings.length > 0 || rawText ? (
                <CollapsibleReviewSection
                  open={reviewTableOpen}
                  onToggle={() => setReviewTableOpen((open) => !open)}
                  warningCount={ocrWarningCount}
                >
                  <HoldingTable
                    holdings={holdings}
                    onChange={setHoldings}
                    warnings={holdingWarnings}
                    onWarningsChange={setHoldingWarnings}
                    portfolioSummary={portfolioSummary}
                    diffs={holdingDiffs}
                    canApplyPreviousStructure={previousHoldings.length > 0}
                    onApplyPreviousStructure={() => {
                      setHoldings(mergeHoldingsWithPrevious(previousHoldings, holdings));
                      setMessage("已沿用昨日基金列表，并保留本次 OCR 的金额与收益。请再扫一眼高亮格子。");
                    }}
                    onAllocateMessage={setMessage}
                    sectorRefresh={sectorRefresh}
                    showSectorRefreshControls={false}
                  />
                </CollapsibleReviewSection>
              ) : null}
              <div ref={reportSectionRef} className="min-w-0">
                <div className="mb-3 text-sm font-black text-slate-950">今日日报</div>
                {todayReport ? (
                  <ReportPanel report={todayReport} />
                ) : (
                  <div className="glass-panel rounded-[24px] border border-dashed border-slate-200 px-5 py-8 text-center text-sm leading-6 text-slate-500">
                    今日尚未生成日报。确认持仓后点击「生成今日基金操作日报」；历史报告请在右上角用户菜单打开。
                  </div>
                )}
              </div>
            </div>
          ) : null}

          {activeTab === "dashboard" ? <PortfolioDashboard /> : null}

          {activeTab === "profiles" ? (
            <div className="grid min-w-0 gap-6">
              <FundProfilePanel
                profiles={profiles}
                isBusy={isProfiling}
                onFileSelect={handleProfileFile}
                onRefresh={() => {
                  void loadProfiles();
                  void loadPortfolioSummary();
                }}
                onImport={(selectedFile) => void handleImportProfiles(selectedFile)}
              />
            </div>
          ) : null}

          {activeTab === "history" ? (
            <div className="grid gap-6">
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
          onEdit={() => {
            setSelectedHoldingIndex(null);
            setReviewTableOpen(true);
            setActiveTab("report");
          }}
          onHoldingResolved={(index, resolved) => {
            setHoldings((current) =>
              current.map((item, itemIndex) => (itemIndex === index ? resolved : item)),
            );
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
  onSelect: (tab: Extract<TabId, "today" | "report">) => void;
}) {
  const highlightedTab =
    activeTab === "today" || activeTab === "report" ? activeTab : null;

  return (
    <div className="glass-panel mb-4 overflow-x-auto rounded-[20px] p-1.5 lg:mb-5 lg:rounded-[24px] lg:p-2">
      <div className="grid min-w-[280px] grid-cols-2 gap-1.5 lg:min-w-0 lg:gap-2">
        {primaryTabs.map((tab) => {
          const active = tab.id === highlightedTab;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => onSelect(tab.id)}
              aria-current={active ? "page" : undefined}
              className={`flex items-center gap-2.5 rounded-[16px] px-3 py-2.5 text-left transition lg:gap-3 lg:rounded-[18px] lg:px-4 lg:py-3 ${
                active
                  ? "bg-[var(--brand)] text-white shadow-[0_12px_28px_rgba(21,101,232,0.22)]"
                  : "bg-white text-slate-600 hover:bg-blue-50 hover:text-blue-700"
              }`}
            >
              <span
                className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-xl lg:h-9 lg:w-9 lg:rounded-2xl ${
                  active ? "bg-white/15 text-white" : "bg-blue-50 text-blue-600"
                }`}
              >
                {tab.icon}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-black">{tab.label}</span>
                <span
                  className={`mt-0.5 hidden truncate text-xs sm:block ${active ? "text-slate-300" : "text-slate-400"}`}
                >
                  {tab.description}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
