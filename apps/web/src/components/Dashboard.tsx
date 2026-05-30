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
import type { FundProfile, Holding, InvestorProfile, Report } from "@/lib/api";
import { analyzeHoldings, listFundProfiles, listReports, parseFundProfile, parseOcr } from "@/lib/api";
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

type TabId = "capture" | "profiles" | "holdings" | "analysis" | "history";

const tabs: Array<{
  id: TabId;
  label: string;
  description: string;
  icon: React.ReactNode;
}> = [
  {
    id: "capture",
    label: "截图识别",
    description: "上传总览截图并设置风控",
    icon: <Camera size={17} />,
  },
  {
    id: "profiles",
    label: "基金档案",
    description: "一次建档，后续自动匹配",
    icon: <BookMarked size={17} />,
  },
  {
    id: "holdings",
    label: "持仓校对",
    description: "确认 OCR 结果和关键指标",
    icon: <Table2 size={17} />,
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
    void loadHistory();
    void loadProfiles();
  }, []);

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
      if (result.holdings.length) {
        setActiveTab("holdings");
      }
      setMessage(
        result.error ??
          (result.holdings.length ? "识别完成，请校对持仓。" : "未识别到基金代码，可以手动新增持仓。"),
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

  const handleAnalyze = async () => {
    setIsAnalyzing(true);
    setMessage(null);
    try {
      const result = await analyzeHoldings(holdings, profile, rawText);
      setReport(result);
      await loadHistory();
      setActiveTab("analysis");
      setMessage("日报已生成并保存到历史记录。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "生成日报失败。");
    } finally {
      setIsAnalyzing(false);
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
                onChange={setProfile}
                onAnalyze={handleAnalyze}
                isBusy={isAnalyzing}
              />
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
            />
          ) : null}

          {activeTab === "holdings" ? (
            <HoldingTable holdings={holdings} onChange={setHoldings} />
          ) : null}

          {activeTab === "analysis" ? (
            <div className="grid min-w-0 gap-6 xl:grid-cols-[380px_minmax(0,1fr)]">
              <RiskControls
                profile={profile}
                onChange={setProfile}
                onAnalyze={handleAnalyze}
                isBusy={isAnalyzing}
              />
              <ReportPanel report={report} />
            </div>
          ) : null}

          {activeTab === "history" ? (
            <HistoryRail reports={reports} onRefresh={loadHistory} onSelect={(selectedReport) => {
              setReport(selectedReport);
              setActiveTab("analysis");
            }} />
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
      <div className="grid min-w-[760px] grid-cols-5 gap-2">
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
