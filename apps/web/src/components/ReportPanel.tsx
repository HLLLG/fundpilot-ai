"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, BarChart3, Download, Sparkles } from "lucide-react";
import type { Report, ReportDiff } from "@/lib/api";
import { fetchReportDiff, fetchReportMarkdown } from "@/lib/api";
import { ReportChatPanel } from "@/components/ReportChatPanel";
import { ReportDiffPanel } from "@/components/ReportDiffPanel";
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

const INTERNAL_CAVEAT_MARKERS = ["JSON 被截断", "无法解析为完整 JSON", "已使用本地规则补齐"];

function userFacingCaveats(caveats: string[]) {
  return caveats.filter((line) => !INTERNAL_CAVEAT_MARKERS.some((marker) => line.includes(marker)));
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
    <div className="rounded-2xl border border-blue-100 bg-blue-50/60 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-black text-slate-950">
          {item.fund_code} · {item.fund_name}
        </span>
        <StatusPill tone="blue">{item.action}</StatusPill>
      </div>
      {navHint ? <p className="mt-1.5 text-xs text-slate-500">{navHint}</p> : null}
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
  const [diff, setDiff] = useState<ReportDiff | null>(null);
  const [isExporting, setIsExporting] = useState(false);

  useEffect(() => {
    if (!report?.id) {
      setDiff(null);
      return;
    }
    void fetchReportDiff(report.id)
      .then((response) => setDiff(response.has_previous && response.diff ? response.diff : null))
      .catch(() => setDiff(null));
  }, [report?.id]);

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
  const caveatLines = userFacingCaveats(report.caveats);
  const portfolioRecommendations =
    report.fund_recommendations.length > 0
      ? report.recommendations
      : report.recommendations.filter((line) => !/^\[\d{6}\s*[·｜|]/.test(line.trim()));

  return (
    <section className="glass-panel min-w-0 rounded-[28px] p-6" data-testid="report-ready">
      <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
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

      {diff ? <ReportDiffPanel diff={diff} /> : null}

      <div className="rounded-[24px] bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center gap-2 text-sm font-black text-slate-950">
          <Sparkles size={18} className="text-blue-600" />
          决策建议
        </div>
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(280px,360px)]">
          <div className="min-w-0 space-y-3">
            {portfolioRecommendations.map((item, index) => (
              <div
                key={`portfolio-${index}`}
                className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 text-slate-700"
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
          <ReportChatPanel reportId={report.id} reportTitle={report.title} />
        </div>
      </div>

      {[...report.risk.alerts.map((alert) => alert.message), ...caveatLines].length > 0 ? (
        <div className="mt-5 rounded-[24px] bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center gap-2 text-sm font-black text-slate-950">
            <AlertTriangle size={18} className="text-amber-500" />
            风险提醒
          </div>
          <div className="space-y-3">
            {[...report.risk.alerts.map((alert) => alert.message), ...caveatLines].map(
              (item, index) => (
                <div
                  key={`${item}-${index}`}
                  className="rounded-2xl border border-amber-100 bg-amber-50/70 px-4 py-3 text-sm leading-6 text-slate-700"
                >
                  {item}
                </div>
              ),
            )}
          </div>
        </div>
      ) : null}

      {report.market_news.length > 0 ? (
        <div className="mt-5 rounded-[24px] bg-white p-5 shadow-sm">
          <div className="mb-4 text-sm font-black text-slate-950">已抓取新闻（优先当日）</div>
          <div className="space-y-3">
            {report.market_news.map((item, index) => (
              <div
                key={`${item.url ?? item.title}-${index}`}
                className="rounded-2xl border border-emerald-100 bg-emerald-50/50 px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                  <span className="font-bold text-emerald-800">{item.topic}</span>
                  {"is_today" in item && item.is_today ? (
                    <span className="rounded-full bg-emerald-600 px-2 py-0.5 text-[10px] font-bold text-white">
                      当日
                    </span>
                  ) : null}
                  {item.published_at ? <span>{item.published_at}</span> : null}
                  {item.source ? <span>{item.source}</span> : null}
                </div>
                {item.url ? (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2 block text-sm font-bold leading-6 text-slate-950 underline-offset-2 hover:underline"
                  >
                    {item.title}
                  </a>
                ) : (
                  <div className="mt-2 text-sm font-bold leading-6 text-slate-950">{item.title}</div>
                )}
                {item.snippet ? (
                  <p className="mt-2 text-xs leading-5 text-slate-600">{item.snippet}</p>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
