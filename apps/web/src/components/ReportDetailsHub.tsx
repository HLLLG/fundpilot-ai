"use client";

import { useMemo, useState } from "react";
import {
  ChevronDown,
  ClipboardCheck,
  Newspaper,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";

import { RebalanceSimulationPanel } from "@/components/RebalanceSimulationPanel";
import { ReportNewsBriefPanel } from "@/components/ReportNewsBriefPanel";
import { ReportOutcomesPanel } from "@/components/ReportOutcomesPanel";
import { SectorOpportunityCard } from "@/components/SectorOpportunityCard";
import type { Report, SectorRotationFacts } from "@/lib/api";

type ReportTool = "news" | "rotation" | "rebalance" | "review";

type ReportToolSelection = {
  reportId: string;
  tool: ReportTool | null;
};

type ReportDetailsHubProps = {
  report: Report;
  diagnostics?: () => React.ReactNode;
};

const TOOLS = [
  {
    id: "news",
    title: "主题要闻摘要",
    hint: "查看有效市场信息",
    icon: Newspaper,
  },
  {
    id: "rotation",
    title: "板块轮动参考",
    hint: "查看未持有的强势方向",
    icon: RefreshCw,
  },
  {
    id: "rebalance",
    title: "调仓示意模拟",
    hint: "预览仓位变化，不执行交易",
    icon: SlidersHorizontal,
  },
  {
    id: "review",
    title: "建议复盘与投研诊断",
    hint: "核对历史结果和辅助信号",
    icon: ClipboardCheck,
  },
] satisfies Array<{
  id: ReportTool;
  title: string;
  hint: string;
  icon: typeof Newspaper;
}>;

function sectorRotationFacts(report: Report): SectorRotationFacts | null {
  const facts = report.analysis_facts as { sector_rotation?: SectorRotationFacts } | undefined;
  const rotation = facts?.sector_rotation;
  return rotation?.available ? rotation : null;
}

export function ReportDetailsHub({ report, diagnostics }: ReportDetailsHubProps) {
  const [selection, setSelection] = useState<ReportToolSelection>(() => ({
    reportId: report.id,
    tool: null,
  }));
  const openTool = selection.reportId === report.id ? selection.tool : null;

  const diagnosticsContent = useMemo(
    () => (openTool === "review" && diagnostics ? diagnostics() : null),
    [diagnostics, openTool],
  );
  const rotation = sectorRotationFacts(report);
  const availableTools = TOOLS.filter((tool) => {
    if (tool.id === "news") {
      return Boolean(report.topic_briefs?.length);
    }
    if (tool.id === "rotation") {
      return Boolean(rotation?.market_top.length);
    }
    return true;
  });

  return (
    <section className="report-panel min-w-0 p-4 sm:p-5">
      <h3 className="text-base font-black text-slate-950">更多内容与工具</h3>
      <p className="mt-1 text-xs leading-5 text-slate-500">
        按需打开单项工具，阅读主线保持轻量。
      </p>

      <div className="mt-3 grid min-w-0 gap-2 sm:grid-cols-2">
        {availableTools.map((tool) => {
          const Icon = tool.icon;
          const isOpen = openTool === tool.id;
          return (
            <button
              key={tool.id}
              type="button"
              aria-label={tool.title}
              aria-expanded={isOpen}
              aria-controls={`report-tool-${tool.id}`}
              onClick={() =>
                setSelection((value) => ({
                  reportId: report.id,
                  tool:
                    value.reportId === report.id && value.tool === tool.id ? null : tool.id,
                }))
              }
              className={`flex min-h-11 min-w-0 items-center gap-3 rounded-xl border px-3 py-3 text-left transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)] ${
                isOpen
                  ? "border-blue-200 bg-blue-50/70"
                  : "border-slate-200 bg-white hover:border-blue-200 hover:bg-blue-50/40"
              }`}
            >
              <span
                aria-hidden="true"
                className={`inline-flex size-9 shrink-0 items-center justify-center rounded-lg ${
                  isOpen
                    ? "bg-[var(--brand-strong)] text-white"
                    : "bg-[var(--brand-soft)] text-[var(--brand-strong)]"
                }`}
              >
                <Icon size={17} />
              </span>
              <span className="min-w-0 flex-1">
                <strong className="block truncate text-sm text-slate-900">{tool.title}</strong>
                <span className="block truncate text-xs text-slate-500">{tool.hint}</span>
              </span>
              <ChevronDown
                aria-hidden="true"
                size={16}
                className={`shrink-0 text-slate-400 transition-transform ${isOpen ? "rotate-180" : ""}`}
              />
            </button>
          );
        })}
      </div>

      {openTool === "news" ? (
        <div id="report-tool-news" data-testid="news-content" className="mt-4 min-w-0">
          <ReportNewsBriefPanel
            briefs={report.topic_briefs ?? []}
            marketNews={report.market_news}
          />
        </div>
      ) : null}

      {openTool === "rotation" ? (
        <div id="report-tool-rotation" className="mt-4 grid min-w-0 gap-2 sm:grid-cols-2">
          {rotation?.market_top.map((item) => (
            <SectorOpportunityCard
              key={`${item.sector_label}-${item.track ?? "track"}`}
              item={item}
            />
          ))}
        </div>
      ) : null}

      {openTool === "rebalance" ? (
        <div id="report-tool-rebalance" className="mt-4 min-w-0">
          <RebalanceSimulationPanel reportId={report.id} embedded />
        </div>
      ) : null}

      {openTool === "review" ? (
        <div id="report-tool-review" className="mt-4 min-w-0 space-y-4">
          <ReportOutcomesPanel reportId={report.id} embedded />
          {diagnosticsContent}
        </div>
      ) : null}
    </section>
  );
}
