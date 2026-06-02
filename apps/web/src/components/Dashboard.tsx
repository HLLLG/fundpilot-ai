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
  Table2,
  TrendingUp,
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
import { buildWorkflowBlockers, hasBlockingErrors } from "@/lib/workflowBlockers";
import { TodayBlockingChecklist } from "@/components/TodayBlockingChecklist";
import { PortfolioDashboard } from "@/components/PortfolioDashboard";
import { PortfolioSummaryCard } from "@/components/PortfolioSummaryCard";
import { ReportPanel } from "@/components/ReportPanel";
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
    description: "上传截图、校对、生成日报",
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
    description: "详情建档与待补全",
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
  const reportSectionRef = useRef<HTMLDivElement>(null);
  const didHydrateReport = useRef(false);

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

  useEffect(() => {
    setProfile(loadInvestorProfile(defaultProfile));
    setAnalysisMode(loadAnalysisMode("deep"));
    setProfileReady(true);
    void loadHistory();
    void loadProfiles();
    void loadPortfolioSummary();
  }, []);

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
      if (result.portfolio_summary) {
        setPortfolioSummary(result.portfolio_summary);
      }
      const warningHint =
        (result.warning_count ?? 0) > 0
          ? `有 ${result.warning_count} 处已高亮，请优先核对负号。`
          : "请在下方校对持仓。";
      if (result.profile_sync && (result.profile_sync.updated || result.profile_sync.created)) {
        await loadPortfolioSummary();
        const syncParts = [];
        if (result.profile_sync.updated) {
          syncParts.push(`更新 ${result.profile_sync.updated} 条`);
        }
        if (result.profile_sync.created) {
          syncParts.push(`新建 ${result.profile_sync.created} 条`);
        }
        setMessage(`识别完成，档案已同步（${syncParts.join("，")}）。${warningHint}`);
      } else {
        setMessage(
          result.error ??
            (result.holdings.length ? `识别完成。${warningHint}` : "未识别到基金代码，可以手动新增持仓。"),
        );
      }
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
      <div className="mx-auto flex min-h-screen w-full max-w-[1480px] flex-col px-4 py-5 sm:px-6 lg:px-8">
        <nav className="mb-5 flex items-center justify-between gap-4 rounded-full border border-white/70 bg-white px-4 py-3 shadow-sm">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-full bg-blue-600 text-white shadow-[0_12px_28px_rgba(23,119,255,0.28)]">
              <BrainCircuit size={22} />
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

        <header className="mb-5 grid gap-5 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
          <div>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <StatusPill tone="dark">MVP 工作台</StatusPill>
              <StatusPill tone="blue">截图到日报</StatusPill>
            </div>
            <h1 className="max-w-4xl text-3xl font-black leading-tight text-slate-950 sm:text-4xl">
              把支付宝基金截图，变成一份可追溯的每日操作日报。
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-600">
              先识别持仓，再套你的稳健风控线，最后让模型做研究员。它不会替你下单，只帮你把&ldquo;该不该动&rdquo;讲清楚。
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
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
          <div className="mb-4 flex items-center justify-between gap-3 rounded-3xl border border-blue-100 bg-white px-5 py-4 text-sm font-semibold text-slate-700 shadow-sm">
            <span>{message}</span>
            <ArrowRight className="text-blue-600" size={18} />
          </div>
        ) : null}

        <TabNav activeTab={activeTab} onSelect={setActiveTab} />

        <div className="min-w-0 flex-1">
          {activeTab === "today" ? (
            <div className="grid min-w-0 gap-6">
              <PortfolioSummaryCard summary={portfolioSummary} holdings={holdings} />
              <TodayWorkflowSteps hasHoldings={holdings.length > 0} hasReport={Boolean(displayReport)} />
              <TodayBlockingChecklist blockers={workflowBlockers} />
              <div className="grid min-w-0 gap-6 lg:grid-cols-[0.9fr_1.1fr]">
                <UploadDropzone
                  rawText={rawText}
                  isBusy={isParsing}
                  selectedFileName={file?.name ?? null}
                  onRawTextChange={setRawText}
                  onFileSelect={handleFileSelect}
                  onParse={handleParse}
                  onLoadSample={() => setRawText(sampleText)}
                />
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
              </div>
              {holdings.length > 0 || rawText ? (
                <div className="min-w-0">
                  <div className="mb-3 flex items-center gap-2 text-sm font-black text-slate-950">
                    <Table2 size={18} className="text-blue-600" />
                    持仓校对
                  </div>
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
                  />
                </div>
              ) : null}
              <div ref={reportSectionRef} className="min-w-0">
                <div className="mb-3 text-sm font-black text-slate-950">今日日报</div>
                <ReportPanel report={report} />
              </div>
            </div>
          ) : null}

          {activeTab === "dashboard" ? <PortfolioDashboard /> : null}

          {activeTab === "profiles" ? (
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
          ) : null}

          {activeTab === "history" ? (
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
          ) : null}
        </div>
      </div>

      <JobStatusFloat
        jobId={activeJobId}
        onComplete={(completedReport) => void handleJobComplete(completedReport)}
        onClose={handleJobClose}
        onRetry={() => void handleJobRetry()}
      />
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
    <div className="glass-panel mb-5 overflow-x-auto rounded-[24px] p-2">
      <div className="grid min-w-[640px] grid-cols-4 gap-2">
        {tabs.map((tab) => {
          const active = tab.id === activeTab;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => onSelect(tab.id)}
              aria-current={active ? "page" : undefined}
              className={`flex items-center gap-3 rounded-[18px] px-4 py-3 text-left transition ${
                active
                  ? "bg-blue-600 text-white shadow-[0_14px_32px_rgba(23,119,255,0.24)]"
                  : "bg-white text-slate-600 hover:bg-blue-50 hover:text-blue-700"
              }`}
            >
              <span
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl ${
                  active ? "bg-white/15 text-white" : "bg-blue-50 text-blue-600"
                }`}
              >
                {tab.icon}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-black">{tab.label}</span>
                <span className={`mt-0.5 block truncate text-xs ${active ? "text-slate-300" : "text-slate-400"}`}>
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
    <div className="glass-panel rounded-[24px] px-5 py-4">
      <div className="flex items-center gap-2 text-xs font-bold text-slate-500">
        <span className="text-blue-600">{icon}</span>
        {label}
      </div>
      <div className="mt-2 text-2xl font-black text-slate-950">{value}</div>
      {hint ? <div className="mt-1 text-xs font-semibold text-slate-500">{hint}</div> : null}
    </div>
  );
}
