"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  BadgeCheck,
  BookMarked,
  BrainCircuit,
  History,
  LayoutDashboard,
  LockKeyhole,
  Sun,
  TrendingUp,
  X,
} from "lucide-react";
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
  exportFundProfiles,
  fetchPortfolioHoldings,
  fetchPortfolioSummary,
  importFundProfiles,
  listFundProfiles,
  listReports,
  parseFundProfile,
  parseOcr,
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
import { UploadDropzone } from "@/components/UploadDropzone";

const sampleText = `华夏中证电网设备主题ETF发起式联接A
015608
持有金额 5,280.66
持有收益率 -3.25%

天弘中证红利低波动100A
008114
持有金额 3,500
持有收益率 1.45%`;

const defaultProfile: InvestorProfile = {
  style: "稳健",
  horizon: "半年到一年",
  max_drawdown_percent: 8,
  concentration_limit_percent: 35,
  prefer_dca: true,
  avoid_chasing: true,
};

type TabId = "today" | "dashboard" | "profiles" | "history";

const tabs: Array<{
  id: TabId;
  label: string;
  description: string;
  icon: React.ReactNode;
}> = [
  {
    id: "today",
    label: "今日",
    description: "持仓涨跌与日报",
    icon: <Sun size={17} />,
  },
  {
    id: "dashboard",
    label: "仪表盘",
    description: "资产走势与持仓分布",
    icon: <LayoutDashboard size={17} />,
  },
  {
    id: "profiles",
    label: "基金档案",
    description: "上传总览与建档",
    icon: <BookMarked size={17} />,
  },
  {
    id: "history",
    label: "历史日报",
    description: "回看已保存报告",
    icon: <History size={17} />,
  },
];

const riskLevelLabel = {
  low: "低",
  medium: "中",
  high: "高",
} as const;

export function Dashboard() {
  const [file, setFile] = useState<File | null>(null);
  const [rawText, setRawText] = useState("");
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [profile, setProfile] = useState<InvestorProfile>(defaultProfile);
  const [report, setReport] = useState<Report | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [profiles, setProfiles] = useState<FundProfile[]>([]);
  const [portfolioSummary, setPortfolioSummary] = useState<PortfolioSummary | null>(null);
  const [holdingWarnings, setHoldingWarnings] = useState<HoldingFieldWarning[]>([]);
  const [holdingDiffs, setHoldingDiffs] = useState<HoldingListDiff[]>([]);
  const [previousHoldings, setPreviousHoldings] = useState<Holding[]>([]);
  const [detailText, setDetailText] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [isParsing, setIsParsing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isProfiling, setIsProfiling] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>("today");
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("deep");
  const [profileReady, setProfileReady] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [reviewTableOpen, setReviewTableOpen] = useState(false);
  const [selectedHoldingIndex, setSelectedHoldingIndex] = useState<number | null>(null);
  const reportSectionRef = useRef<HTMLDivElement>(null);
  const didHydrateReport = useRef(false);
  const shouldRefreshOnLoad = useRef(false);
  const [isHydratingHoldings, setIsHydratingHoldings] = useState(true);

  const sessionTotal = useMemo(
    () => holdings.reduce((sum, holding) => sum + Number(holding.holding_amount || 0), 0),
    [holdings],
  );

  const totalAmount = portfolioSummary?.total_assets ?? (sessionTotal || null);
  const dailyProfit = portfolioSummary?.daily_profit;
  const displayReport = report ?? reports[0] ?? null;

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
        fund_name: "新基金",
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
      const items = await listReports();
      setReports(items);
      if (items.length > 0 && !didHydrateReport.current) {
        didHydrateReport.current = true;
        setReport(items[0]);
      }
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
    void sectorRefresh.refresh(true);
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

  const handleParse = async (fileOverride?: File) => {
    setIsParsing(true);
    setMessage(null);
    try {
      const formData = new FormData();
      const fileToUpload = fileOverride ?? file;
      if (fileToUpload) {
        formData.append("file", fileToUpload);
      }
      if (rawText.trim()) {
        formData.append("raw_text", rawText);
      }
      const result = await parseOcr(formData);
      setRawText(result.raw_text);
      setHoldings(result.holdings);
      setHoldingWarnings(result.holding_warnings ?? []);
      setHoldingDiffs(result.holding_diffs ?? []);
      setPreviousHoldings(result.previous_holdings ?? []);
      setReviewTableOpen((result.warning_count ?? 0) > 0);
      if (result.portfolio_summary) {
        setPortfolioSummary(result.portfolio_summary);
      }
      if (result.sector_refresh) {
        sectorRefresh.applyServerRefresh(result.sector_refresh);
      } else if (result.holdings.length) {
        void sectorRefresh.refresh(true);
      }
      setActiveTab("today");
      await loadProfiles();

      const parts: string[] = [];
      if (result.holdings.length) {
        parts.push(`已识别 ${result.holdings.length} 只基金`);
      }
      if (result.profile_sync?.updated || result.profile_sync?.created) {
        const syncParts = [];
        if (result.profile_sync.updated) {
          syncParts.push(`更新档案 ${result.profile_sync.updated} 条`);
        }
        if (result.profile_sync.created) {
          syncParts.push(`新建档案 ${result.profile_sync.created} 条`);
        }
        parts.push(syncParts.join("，"));
      }
      if (result.sector_refresh?.ok) {
        parts.push(result.sector_refresh.message);
      } else if (result.sector_refresh?.message) {
        parts.push(result.sector_refresh.message);
      }
      const warningHint =
        (result.warning_count ?? 0) > 0
          ? `有 ${result.warning_count} 处需核对。`
          : "板块涨跌已刷新，当日收益已按关联板块估算。";
      setMessage(
        result.error ??
          (parts.length ? `${parts.join("；")}。${warningHint}` : "未识别到基金持仓，请检查截图或手动录入。"),
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "OCR 识别失败，请改用手动文本。");
    } finally {
      setIsParsing(false);
    }
  };

  const handleFileSelect = (selectedFile: File) => {
    setFile(selectedFile);
    void handleParse(selectedFile);
  };

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
    setActiveTab("today");
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

  const handleExportProfiles = async () => {
    try {
      const payload = await exportFundProfiles();
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `fund-profiles-${new Date().toISOString().slice(0, 10)}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      setMessage(`已导出 ${payload.profiles.length} 条基金档案。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "导出档案失败。");
    }
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
      setDetailText(profileResult.raw_text ?? "");
      await loadProfiles();
      await loadPortfolioSummary();
      setActiveTab("profiles");
      setMessage(`基金档案已保存：${profileResult.fund_name}（${profileResult.fund_code}）`);
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

  const handleProfileText = () => {
    const formData = new FormData();
    formData.append("raw_text", detailText);
    void handleProfileForm(formData);
  };

  return (
    <main className="premium-bg min-h-screen">
      <div className="mx-auto flex min-h-screen w-full max-w-[1520px] flex-col px-4 py-4 sm:px-6 lg:px-8 lg:py-6">
        <nav className="animate-fade-up mb-4 flex items-center justify-between gap-4 rounded-2xl border border-white/80 bg-white/90 px-4 py-3 shadow-sm backdrop-blur-sm lg:mb-5 lg:rounded-full">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-[var(--brand)] text-white shadow-[0_10px_24px_rgba(21,101,232,0.28)] lg:h-11 lg:w-11 lg:rounded-full">
              <BrainCircuit size={20} />
            </div>
            <div>
              <div className="text-sm font-black text-slate-950">FundPilot AI</div>
              <div className="text-xs text-slate-500">私人基金投研助手</div>
            </div>
          </div>
          <div className="hidden items-center gap-2 md:flex">
            <StatusPill tone="blue">本地优先</StatusPill>
            <StatusPill tone="green">DeepSeek V4 Pro</StatusPill>
            <StatusPill tone="amber">人工确认</StatusPill>
          </div>
        </nav>

        <header className="animate-fade-up mb-4 grid gap-4 lg:mb-5 lg:grid-cols-[1.15fr_0.85fr] lg:items-end lg:gap-5">
          <div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <StatusPill tone="dark">工作台</StatusPill>
              <StatusPill tone="blue">截图 → 日报</StatusPill>
            </div>
            <h1 className="max-w-3xl text-2xl font-black leading-tight tracking-tight text-slate-950 sm:text-3xl lg:text-[2rem]">
              养基宝式持仓看板，实时估算当日涨跌
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
              首页自动恢复基金档案与持仓，刷新即可更新板块涨跌。上传总览截图请前往「基金档案」。
            </p>
          </div>
          <div className="grid gap-2.5 sm:grid-cols-3 lg:grid-cols-3">
            <MetricCard
              icon={<TrendingUp size={18} />}
              label="持仓总额"
              value={
                totalAmount !== null && totalAmount !== undefined
                  ? `¥${totalAmount.toLocaleString("zh-CN")}`
                  : "—"
              }
              hint={
                dailyProfit !== null && dailyProfit !== undefined
                  ? `当日 ${dailyProfit > 0 ? "+" : ""}${dailyProfit.toLocaleString("zh-CN")}`
                  : undefined
              }
            />
            <MetricCard
              icon={<LockKeyhole size={18} />}
              label="最新风险"
              value={
                displayReport
                  ? `${riskLevelLabel[displayReport.risk.level]} · ${displayReport.risk.weighted_return_percent}%`
                  : `底线 ${profile.max_drawdown_percent}%`
              }
              hint={displayReport ? "来自最近日报" : "生成日报后更新"}
            />
            <MetricCard icon={<BadgeCheck size={18} />} label="日报数量" value={`${reports.length}`} />
          </div>
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
            <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(360px,400px)_minmax(0,1fr)] xl:items-start xl:gap-6">
              <div className="xl:sticky xl:top-5">
                <YangjibaoHoldingsBoard
                  holdings={holdings}
                  portfolioSummary={portfolioSummary}
                  sectorRefresh={sectorRefresh}
                  isLoading={isHydratingHoldings}
                  className="max-w-none"
                  onOpenCapture={() => setActiveTab("profiles")}
                  onAddHolding={addEmptyHolding}
                  onExpandReview={() => setReviewTableOpen(true)}
                  onSelectHolding={setSelectedHoldingIndex}
                />
              </div>

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
                      需要更新持有金额？前往「基金档案」上传最新养基宝总览，或在下方校对表手动修改。
                    </p>
                    <div className="mt-4 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => setActiveTab("profiles")}
                        className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-700 transition hover:border-blue-200 hover:text-blue-700"
                      >
                        上传总览截图
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
                  <ReportPanel report={report} />
                </div>
              </div>
            </div>
          ) : null}

          {activeTab === "dashboard" ? <PortfolioDashboard /> : null}

          {activeTab === "profiles" ? (
            <div className="grid min-w-0 gap-6">
              <UploadDropzone
                rawText={rawText}
                isBusy={isParsing}
                selectedFileName={file?.name ?? null}
                onRawTextChange={setRawText}
                onFileSelect={handleFileSelect}
                onParse={handleParse}
                onLoadSample={() => setRawText(sampleText)}
              />
              <FundProfilePanel
                profiles={profiles}
                portfolioSummary={portfolioSummary}
                detailText={detailText}
                isBusy={isProfiling}
                onDetailTextChange={setDetailText}
                onFileSelect={handleProfileFile}
                onParseText={handleProfileText}
                onRefresh={() => {
                  void loadProfiles();
                  void loadPortfolioSummary();
                }}
                onExport={() => void handleExportProfiles()}
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
                setActiveTab("today");
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
          sectorMeta={sectorRefresh.sectorMetaByIndex[selectedHoldingIndex]}
          onClose={() => setSelectedHoldingIndex(null)}
          onNavigate={setSelectedHoldingIndex}
          onEdit={() => {
            setSelectedHoldingIndex(null);
            setReviewTableOpen(true);
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
  onSelect: (tab: TabId) => void;
}) {
  return (
    <div className="glass-panel mb-4 overflow-x-auto rounded-[20px] p-1.5 lg:mb-5 lg:rounded-[24px] lg:p-2">
      <div className="grid min-w-[560px] grid-cols-4 gap-1.5 lg:min-w-0 lg:gap-2">
        {tabs.map((tab) => {
          const active = tab.id === activeTab;
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

function MetricCard({
  icon,
  label,
  value,
  hint,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="glass-panel rounded-[20px] px-4 py-3 lg:rounded-[24px] lg:px-5 lg:py-4">
      <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-slate-500 lg:text-xs lg:normal-case lg:tracking-normal">
        <span className="text-[var(--brand)]">{icon}</span>
        {label}
      </div>
      <div className="mt-1.5 text-xl font-black tabular-nums text-slate-950 lg:mt-2 lg:text-2xl">{value}</div>
      {hint ? <div className="mt-1 text-[11px] font-semibold text-slate-500 lg:text-xs">{hint}</div> : null}
    </div>
  );
}
