"use client";

import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  BadgeCheck,
  BookMarked,
  BrainCircuit,
  Camera,
  FileText,
  History,
  LockKeyhole,
  Table2,
  TrendingUp,
} from "lucide-react";
import type {
  AnalysisMode,
  AutomationStatus,
  FundProfile,
  Holding,
  InvestorProfile,
  InboxEvent,
  Report,
} from "@/lib/api";
import {
  analyzeHoldings,
  consumeInboxEvent,
  exportFundProfiles,
  fetchAutomationStatus,
  importFundProfiles,
  listFundProfiles,
  listInboxEvents,
  listReports,
  parseFundProfile,
  parseOcr,
  startAnalyzeJob,
  waitForAnalysisJob,
} from "@/lib/api";
import { ensureNotificationPermission, notifyDesktop } from "@/lib/notifications";
import {
  loadAnalysisMode,
  loadAutoAnalyzeOnOcr,
  loadInboxSeenIds,
  loadInvestorProfile,
  loadUseAsyncAnalyze,
  saveAnalysisMode,
  saveAutoAnalyzeOnOcr,
  saveInboxSeenIds,
  saveInvestorProfile,
  saveUseAsyncAnalyze,
} from "@/lib/storage";
import { AutomationPanel } from "@/components/AutomationPanel";
import { DailyWorkflowBar } from "@/components/DailyWorkflowBar";
import { FundProfilePanel } from "@/components/FundProfilePanel";
import { HistoryRail } from "@/components/HistoryRail";
import { HoldingTable } from "@/components/HoldingTable";
import { ReportPanel } from "@/components/ReportPanel";
import { RiskControls } from "@/components/RiskControls";
import { StatusPill } from "@/components/StatusPill";
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

type TabId = "capture" | "profiles" | "analysis" | "history";

const tabs: Array<{
  id: TabId;
  label: string;
  description: string;
  icon: React.ReactNode;
}> = [
  {
    id: "capture",
    label: "截图识别",
    description: "识别总览并校对持仓",
    icon: <Camera size={17} />,
  },
  {
    id: "profiles",
    label: "基金档案",
    description: "一次建档，后续自动匹配",
    icon: <BookMarked size={17} />,
  },
  {
    id: "analysis",
    label: "分析报告",
    description: "生成并查看每日操作建议",
    icon: <FileText size={17} />,
  },
  {
    id: "history",
    label: "历史日报",
    description: "回看已保存报告",
    icon: <History size={17} />,
  },
];

export function Dashboard() {
  const [file, setFile] = useState<File | null>(null);
  const [rawText, setRawText] = useState("");
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [profile, setProfile] = useState<InvestorProfile>(defaultProfile);
  const [report, setReport] = useState<Report | null>(null);
  const [reports, setReports] = useState<Report[]>([]);
  const [profiles, setProfiles] = useState<FundProfile[]>([]);
  const [detailText, setDetailText] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [isParsing, setIsParsing] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isProfiling, setIsProfiling] = useState(false);
  const [activeTab, setActiveTab] = useState<TabId>("capture");
  const [analysisMode, setAnalysisMode] = useState<AnalysisMode>("deep");
  const [profileReady, setProfileReady] = useState(false);
  const [automationStatus, setAutomationStatus] = useState<AutomationStatus | null>(null);
  const [inboxEvents, setInboxEvents] = useState<InboxEvent[]>([]);
  const [autoAnalyzeOnOcr, setAutoAnalyzeOnOcr] = useState(false);
  const [useAsyncAnalyze, setUseAsyncAnalyze] = useState(true);
  const [notificationPermission, setNotificationPermission] = useState<
    NotificationPermission | "unsupported"
  >("unsupported");
  const [activeJobId, setActiveJobId] = useState<string | null>(null);

  const totalAmount = useMemo(
    () => holdings.reduce((sum, holding) => sum + Number(holding.holding_amount || 0), 0),
    [holdings],
  );

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

  useEffect(() => {
    setProfile(loadInvestorProfile(defaultProfile));
    setAnalysisMode(loadAnalysisMode("deep"));
    setAutoAnalyzeOnOcr(loadAutoAnalyzeOnOcr());
    setUseAsyncAnalyze(loadUseAsyncAnalyze());
    setProfileReady(true);
    void loadHistory();
    void loadProfiles();
    void fetchAutomationStatus().then(setAutomationStatus).catch(() => setAutomationStatus(null));
    if (typeof window !== "undefined" && "Notification" in window) {
      setNotificationPermission(Notification.permission);
    }
  }, []);

  useEffect(() => {
    if (!profileReady) {
      return;
    }
    saveInvestorProfile(profile);
  }, [profile, profileReady]);

  useEffect(() => {
    if (!profileReady) {
      return;
    }
    saveAnalysisMode(analysisMode);
  }, [analysisMode, profileReady]);

  useEffect(() => {
    if (!profileReady) {
      return;
    }
    saveAutoAnalyzeOnOcr(autoAnalyzeOnOcr);
  }, [autoAnalyzeOnOcr, profileReady]);

  useEffect(() => {
    if (!profileReady) {
      return;
    }
    saveUseAsyncAnalyze(useAsyncAnalyze);
  }, [useAsyncAnalyze, profileReady]);

  const workflowStep = useMemo<1 | 2 | 3>(() => {
    if (isAnalyzing) {
      return 3;
    }
    if (holdings.length > 0) {
      return 2;
    }
    return 1;
  }, [holdings.length, isAnalyzing]);

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
      setMessage(
        result.error ??
          (result.holdings.length ? "识别完成，请在下方校对持仓。" : "未识别到基金代码，可以手动新增持仓。"),
      );
      if (autoAnalyzeOnOcr && result.holdings.length > 0 && !result.error) {
        await runAnalyze(result.holdings);
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
    setIsAnalyzing(true);
    setMessage(useAsyncAnalyze ? "分析任务已提交，后台生成中…" : null);
    try {
      let result: Report;
      if (useAsyncAnalyze) {
        const jobId = await startAnalyzeJob(targetHoldings, profile, rawText, analysisMode);
        setActiveJobId(jobId);
        result = await waitForAnalysisJob(jobId);
        setActiveJobId(null);
      } else {
        result = await analyzeHoldings(targetHoldings, profile, rawText, analysisMode);
      }
      setReport(result);
      await loadHistory();
      setActiveTab("analysis");
      notifyDesktop("FundPilot 日报已生成", { body: result.title });
      setMessage(
        analysisMode === "fast"
          ? "快速模式日报已生成（Flash + 预取新闻）。"
          : "日报已生成并保存到历史记录。",
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "生成日报失败。");
      notifyDesktop("FundPilot 分析失败", {
        body: error instanceof Error ? error.message : "请查看页面提示",
      });
    } finally {
      setIsAnalyzing(false);
      setActiveJobId(null);
    }
  };

  const runAnalyzeFromInbox = async (event: InboxEvent) => {
    const eventHoldings = event.payload.holdings ?? [];
    if (!eventHoldings.length) {
      return;
    }
    setHoldings(eventHoldings);
    setRawText(event.payload.raw_text ?? "");
    await runAnalyze(eventHoldings);
    await consumeInboxEvent(event.id);
    void pollInboxEvents();
  };

  const handleAnalyze = async () => {
    await runAnalyze(holdings);
  };

  const parseHoldingsFromInput = async (fileOverride?: File): Promise<Holding[]> => {
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
    setMessage(
      result.error ??
        (result.holdings.length ? "识别完成，请在下方校对持仓。" : "未识别到基金，请手动新增或检查截图。"),
    );
    if (autoAnalyzeOnOcr && result.holdings.length > 0 && !result.error) {
      await runAnalyze(result.holdings);
    }
    return result.holdings;
  };

  const handleApplyInboxEvent = (event: InboxEvent) => {
    setHoldings(event.payload.holdings ?? []);
    setRawText(event.payload.raw_text ?? "");
    setActiveTab("capture");
    setMessage(`已载入收件箱截图：${event.file_name ?? ""}`);
    void consumeInboxEvent(event.id).then(() => pollInboxEvents());
  };

  const handleAnalyzeInboxEvent = (event: InboxEvent) => {
    void runAnalyzeFromInbox(event);
  };

  const handleDismissInboxEvent = (event: InboxEvent) => {
    void consumeInboxEvent(event.id).then(() => pollInboxEvents());
  };

  const handleRequestNotifications = async () => {
    const permission = await ensureNotificationPermission();
    setNotificationPermission(permission);
    if (permission === "granted") {
      notifyDesktop("FundPilot 通知已开启", { body: "分析完成或收件箱有新截图时会提醒。" });
    }
  };

  const pollInboxEvents = async () => {
    try {
      const events = await listInboxEvents("pending");
      setInboxEvents(events);
      const seen = loadInboxSeenIds();
      for (const event of events) {
        if (seen.has(event.id)) {
          continue;
        }
        seen.add(event.id);
        if (event.kind === "schedule_reminder") {
          notifyDesktop("FundPilot 交易日提醒", { body: event.payload.message });
          continue;
        }
        if (event.kind === "ocr_ready") {
          notifyDesktop("FundPilot 收件箱", {
            body: `已识别 ${event.payload.holdings?.length ?? 0} 条持仓：${event.file_name ?? "截图"}`,
          });
          setHoldings(event.payload.holdings ?? []);
          setRawText(event.payload.raw_text ?? "");
          setActiveTab("capture");
          setMessage("收件箱已识别新截图，请校对或直接分析。");
          if (autoAnalyzeOnOcr && (event.payload.holdings?.length ?? 0) > 0) {
            await runAnalyzeFromInbox(event);
          }
        }
      }
      saveInboxSeenIds(seen);
    } catch {
      // API may be offline during startup
    }
  };

  useEffect(() => {
    const timer = window.setInterval(() => {
      void pollInboxEvents();
    }, 4000);
    void pollInboxEvents();
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- poll when automation prefs change
  }, [autoAnalyzeOnOcr, useAsyncAnalyze, analysisMode]);

  const handleRunDaily = async () => {
    setMessage(null);
    try {
      let nextHoldings = holdings;
      if (!nextHoldings.length) {
        if (!file && !rawText.trim()) {
          setMessage("请先上传养基宝总览截图，或粘贴 OCR 文本。");
          return;
        }
        setIsParsing(true);
        nextHoldings = await parseHoldingsFromInput();
        setIsParsing(false);
      }
      if (!nextHoldings.length) {
        return;
      }
      await runAnalyze(nextHoldings);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "今日一键分析失败。");
      setIsParsing(false);
      setIsAnalyzing(false);
    }
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
              先识别持仓，再套你的稳健风控线，最后让模型做研究员。它不会替你下单，只帮你把“该不该动”讲清楚。
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-1">
            <MetricCard icon={<TrendingUp size={18} />} label="持仓总额" value={`¥${totalAmount.toLocaleString("zh-CN")}`} />
            <MetricCard icon={<LockKeyhole size={18} />} label="风险底线" value={`${profile.max_drawdown_percent}%`} />
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
          {activeTab === "capture" ? (
            <div className="grid min-w-0 gap-6">
              <DailyWorkflowBar
                step={workflowStep}
                holdingsCount={holdings.length}
                isParsing={isParsing}
                isAnalyzing={isAnalyzing}
                canAnalyze={Boolean(file || rawText.trim() || holdings.length > 0)}
                onRunDaily={() => void handleRunDaily()}
              />
              <AutomationPanel
                status={automationStatus}
                events={inboxEvents}
                autoAnalyzeOnOcr={autoAnalyzeOnOcr}
                useAsyncAnalyze={useAsyncAnalyze}
                notificationPermission={notificationPermission}
                onAutoAnalyzeOnOcrChange={setAutoAnalyzeOnOcr}
                onUseAsyncAnalyzeChange={setUseAsyncAnalyze}
                onRequestNotifications={() => void handleRequestNotifications()}
                onApplyEvent={handleApplyInboxEvent}
                onAnalyzeEvent={handleAnalyzeInboxEvent}
                onDismissEvent={handleDismissInboxEvent}
              />
              {activeJobId ? (
                <div className="rounded-2xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm font-semibold text-blue-800">
                  后台任务进行中（{activeJobId.slice(0, 8)}…），可先浏览页面，完成后会通知你。
                </div>
              ) : null}
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
                  isBusy={isAnalyzing}
                />
              </div>
              {holdings.length > 0 || rawText ? (
                <div className="min-w-0">
                  <div className="mb-3 flex items-center gap-2 text-sm font-black text-slate-950">
                    <Table2 size={18} className="text-blue-600" />
                    持仓校对
                  </div>
                  <HoldingTable holdings={holdings} onChange={setHoldings} />
                </div>
              ) : null}
            </div>
          ) : null}

          {activeTab === "profiles" ? (
            <FundProfilePanel
              profiles={profiles}
              detailText={detailText}
              isBusy={isProfiling}
              onDetailTextChange={setDetailText}
              onFileSelect={handleProfileFile}
              onParseText={handleProfileText}
              onRefresh={loadProfiles}
              onExport={() => void handleExportProfiles()}
              onImport={(selectedFile) => void handleImportProfiles(selectedFile)}
            />
          ) : null}

          {activeTab === "analysis" ? (
            <div className="flex min-w-0 flex-col gap-6">
              <RiskControls
                profile={profile}
                analysisMode={analysisMode}
                onAnalysisModeChange={setAnalysisMode}
                onChange={setProfile}
                onAnalyze={() => void handleAnalyze()}
                isBusy={isAnalyzing}
              />
              <ReportPanel report={report} />
            </div>
          ) : null}

          {activeTab === "history" ? (
            <HistoryRail
              reports={reports}
              onRefresh={loadHistory}
              onSelect={(selectedReport) => {
                setReport(selectedReport);
                setActiveTab("analysis");
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
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="glass-panel rounded-[24px] px-5 py-4">
      <div className="flex items-center gap-2 text-xs font-bold text-slate-500">
        <span className="text-blue-600">{icon}</span>
        {label}
      </div>
      <div className="mt-2 text-2xl font-black text-slate-950">{value}</div>
    </div>
  );
}
