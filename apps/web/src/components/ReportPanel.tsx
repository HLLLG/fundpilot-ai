"use client";

import { useState } from "react";
import { BarChart3, Download, Sparkles } from "lucide-react";
import type { Report } from "@/lib/api";
import { fetchReportMarkdown } from "@/lib/api";
import { actionBadgeClass, actionCardClass } from "@/lib/actionStyles";
import { ReportChatPanel } from "@/components/ReportChatPanel";
import { ReportCollapsibleSection } from "@/components/ReportCollapsibleSection";
import { RebalanceSimulationPanel } from "@/components/RebalanceSimulationPanel";
import { ReportNewsBriefPanel } from "@/components/ReportNewsBriefPanel";
import { ReportOutcomesPanel } from "@/components/ReportOutcomesPanel";
import { StatusPill } from "@/components/StatusPill";

type ReportPanelProps = {
  report: Report | null;
};

const riskTone = {
  low: "green",
  medium: "amber",
  high: "red",
} as const;

const actionLabel = {
  watch: "观察",
  pause_add: "暂停加仓",
  staggered_add: "分批加仓",
  risk_review: "减仓/风控复核",
};

function FundDiagnosticHint({
  fundCode,
  snapshots,
}: {
  fundCode: string;
  snapshots: Report["snapshots"];
}) {
  const match = snapshots.find((snapshot) => snapshot.fund_code === fundCode);
  if (!match) {
    return null;
  }
  const hints: string[] = [];
  if (match.fund_type) hints.push(`类型 ${match.fund_type}`);
  if (match.management_fee) hints.push(`管理费 ${match.management_fee}`);
  if (match.return_1y_percent != null) hints.push(`近1年 ${match.return_1y_percent}%`);
  if (match.max_drawdown_1y_percent != null)
    hints.push(`最大回撤 ${match.max_drawdown_1y_percent}%`);
  if (!hints.length) {
    return null;
  }
  return <p className="mt-1 text-xs text-indigo-700">{hints.join(" · ")}</p>;
}

function navHintForFund(fundCode: string, snapshots: Report["snapshots"]): string | null {
  const snapshot = snapshots.find((item) => item.fund_code === fundCode);
  if (!snapshot) {
    return null;
  }
  if (snapshot.latest_nav != null && snapshot.nav_date) {
    return `最新净值 ${snapshot.latest_nav} · 日期 ${snapshot.nav_date}`;
  }
  if (snapshot.latest_nav != null) {
    return `最新净值 ${snapshot.latest_nav}`;
  }
  if (snapshot.nav_date) {
    return `净值日期 ${snapshot.nav_date}`;
  }
  if (snapshot.note) {
    return snapshot.note;
  }
  return null;
}

type FundRec = Report["fund_recommendations"][number];

function FundRecommendationCard({
  item,
  snapshots,
}: {
  item: FundRec;
  snapshots: Report["snapshots"];
}) {
  const navHint = navHintForFund(item.fund_code, snapshots);

  return (
    <div className={`rounded-xl border px-4 py-3.5 ${actionCardClass(item.action)}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-black text-slate-950">
          {item.fund_code} · {item.fund_name}
        </span>
        <span
          className={`inline-flex rounded-full border px-2.5 py-0.5 text-xs font-bold ${actionBadgeClass(item.action)}`}
        >
          {item.action}
        </span>
      </div>
      {navHint ? <p className="mt-1.5 text-xs text-slate-500">{navHint}</p> : null}
      <FundDiagnosticHint fundCode={item.fund_code} snapshots={snapshots} />
      {item.amount_note || item.amount_yuan != null ? (
        <div className="mt-2 rounded-xl bg-white/80 px-3 py-2 text-sm font-bold text-blue-800">
          {item.amount_note ??
            (item.amount_yuan != null
              ? `参考金额：约 ${item.amount_yuan.toLocaleString("zh-CN")} 元`
              : null)}
        </div>
      ) : null}
      {item.news_bullish?.length ? (
        <div className="mt-3 rounded-xl border border-emerald-100 bg-emerald-50/80 px-3 py-2">
          <div className="text-xs font-bold text-emerald-800">板块利好</div>
          <ul className="mt-1 space-y-1 text-xs leading-5 text-slate-700">
            {item.news_bullish.map((headline, index) => (
              <li key={`${item.fund_code}-bull-${index}`}>{headline}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {item.news_bearish?.length ? (
        <div className="mt-3 rounded-xl border border-rose-100 bg-rose-50/80 px-3 py-2">
          <div className="text-xs font-bold text-rose-800">板块利空 / 风险</div>
          <ul className="mt-1 space-y-1 text-xs leading-5 text-slate-700">
            {item.news_bearish.map((headline, index) => (
              <li key={`${item.fund_code}-bear-${index}`}>{headline}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <ul className="mt-3 space-y-2 text-sm leading-6 text-slate-700">
        {item.points.map((point, pointIndex) => (
          <li key={`${item.fund_code}-${pointIndex}`} className="list-disc pl-5">
            {point}
          </li>
        ))}
      </ul>
    </div>
  );
}

function displayFundRecommendations(report: Report) {
  if (report.fund_recommendations.length > 0) {
    return report.fund_recommendations;
  }
  const byCode = new Map<string, Report["fund_recommendations"][number]>();
  for (const line of report.recommendations) {
    const match = line.match(/^\[(\d{6})\s*[·｜|]\s*([^\]]+)\]\s*(.*)$/);
    if (!match) {
      continue;
    }
    const [, fundCode, action, rest] = match;
    const existing = byCode.get(fundCode);
    if (!existing) {
      byCode.set(fundCode, {
        fund_code: fundCode,
        fund_name: fundCode,
        action: action.trim(),
        points: rest.trim() ? [rest.trim()] : [],
      });
      continue;
    }
    if (rest.trim() && !existing.points.includes(rest.trim())) {
      existing.points.push(rest.trim());
    }
  }
  return [...byCode.values()];
}

export function ReportPanel({ report }: ReportPanelProps) {
  const [isExporting, setIsExporting] = useState(false);

  const handleExportMarkdown = async () => {
    if (!report) {
      return;
    }
    setIsExporting(true);
    try {
      const markdown = await fetchReportMarkdown(report.id);
      const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${report.title || "fund-report"}.md`;
      anchor.click();
      URL.revokeObjectURL(url);
    } finally {
      setIsExporting(false);
    }
  };

  if (!report) {
    return (
      <section className="glass-panel signal-grid min-w-0 rounded-[28px] p-6">
        <div className="flex min-h-80 flex-col justify-between rounded-[24px] bg-white/75 p-6">
          <div>
            <StatusPill tone="blue">等待生成</StatusPill>
            <h2 className="mt-5 text-2xl font-black text-slate-950">你的日报会出现在这里</h2>
            <p className="mt-3 max-w-lg text-sm leading-6 text-slate-600">
              上传截图并确认持仓后，系统会先跑硬风控，再让 DeepSeek 生成带风险边界的操作日报。
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            {["规则先行", "模型辅助", "人工确认"].map((item) => (
              <div key={item} className="rounded-2xl border border-slate-100 bg-white px-4 py-3 text-sm font-bold text-slate-700">
                {item}
              </div>
            ))}
          </div>
        </div>
      </section>
    );
  }

  const fundRecommendations = displayFundRecommendations(report);
  const portfolioRecommendations =
    report.fund_recommendations.length > 0
      ? report.recommendations
      : report.recommendations.filter((line) => !/^\[\d{6}\s*[·｜|]/.test(line.trim()));

  return (
    <section className="report-shell min-w-0 animate-fade-up" data-testid="report-ready">
      <div className="report-panel mb-5 p-4 sm:p-5">
      <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <StatusPill tone={riskTone[report.risk.level]}>风险 {report.risk.level}</StatusPill>
            <StatusPill tone="dark">{actionLabel[report.risk.suggested_action]}</StatusPill>
            <StatusPill tone="blue">{report.provider}</StatusPill>
          </div>
          <h2 className="text-2xl font-black text-slate-950">{report.title}</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">{report.summary}</p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
          <div className="rounded-3xl bg-slate-950 px-5 py-4 text-white">
            <div className="flex items-center gap-2 text-xs font-bold text-slate-300">
              <BarChart3 size={16} />
              加权收益率
            </div>
            <div className="mt-2 text-3xl font-black">{report.risk.weighted_return_percent}%</div>
          </div>
          <button
            type="button"
            onClick={() => void handleExportMarkdown()}
            disabled={isExporting}
            className="inline-flex items-center justify-center gap-2 rounded-3xl border border-slate-200 bg-white px-4 py-3 text-sm font-bold text-slate-700 shadow-sm transition hover:border-blue-300 hover:text-blue-700 disabled:opacity-50"
          >
            <Download size={16} />
            {isExporting ? "导出中..." : "导出 Markdown"}
          </button>
        </div>
      </div>
      </div>

      <div className="report-panel mb-5 overflow-hidden">
        <div className="report-panel-header">
          <div className="flex items-center gap-2 text-sm font-black text-slate-950">
            <Sparkles size={18} className="text-blue-600" />
            决策建议
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            逐基金操作建议与依据；宽屏时右侧可追问，窄屏时追问在下方。
          </p>
        </div>
        <div className="report-decision-grid p-4 sm:p-5">
          <div className="min-w-0 space-y-3">
            {portfolioRecommendations.map((item, index) => (
              <div
                key={`portfolio-${index}`}
                className="rounded-xl border border-slate-200 bg-slate-50/90 px-4 py-3 text-sm leading-6 text-slate-700"
              >
                {item}
              </div>
            ))}
            {fundRecommendations.map((item) => (
              <FundRecommendationCard
                key={item.fund_code}
                item={item}
                snapshots={report.snapshots}
              />
            ))}
          </div>
          <div className="report-chat-sticky min-w-0">
            <ReportChatPanel
              reportId={report.id}
              reportTitle={report.title}
              compact
            />
          </div>
        </div>
      </div>

      <ReportCollapsibleSection title="调仓示意模拟" className="mb-5">
        <RebalanceSimulationPanel reportId={report.id} embedded />
      </ReportCollapsibleSection>

      <ReportCollapsibleSection
        title="建议复盘"
        className="mb-5"
      >
        <ReportOutcomesPanel reportId={report.id} embedded />
      </ReportCollapsibleSection>

      {report.topic_briefs && report.topic_briefs.length > 0 ? (
        <ReportCollapsibleSection title="主题要闻摘要" className="mb-5">
          <ReportNewsBriefPanel briefs={report.topic_briefs} marketNews={report.market_news} />
        </ReportCollapsibleSection>
      ) : null}
    </section>
  );
}
