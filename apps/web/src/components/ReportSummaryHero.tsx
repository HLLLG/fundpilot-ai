"use client";

import { useId, useState } from "react";
import { ChevronDown, Download, LoaderCircle } from "lucide-react";

import { StatusPill } from "@/components/StatusPill";
import type { Report } from "@/lib/api";
import { portfolioRecommendationLines } from "@/lib/reportPresentation";

type ReportSummaryHeroProps = {
  report: Report;
  needsActionCount: number;
  isExporting: boolean;
  onExport: () => void;
};

type MetricProps = {
  label: string;
  value: string;
  emphasis?: boolean;
};

const riskTone = { low: "green", medium: "amber", high: "red" } as const;
const riskLabel = { low: "较低", medium: "中等", high: "较高" } as const;
const actionLabel = {
  watch: "观察",
  pause_add: "暂停加仓",
  staggered_add: "分批加仓",
  risk_review: "减仓/风控复核",
} as const;

const ALERT_CODE_LABELS: Record<string, string> = {
  PORTFOLIO_COST_BASIS_LOSS: "组合浮亏触线",
  HOLDING_COST_BASIS_LOSS: "单只浮亏触线",
  CONCENTRATION: "集中度超限",
};

function alertCodeLabel(code: string): string {
  return ALERT_CODE_LABELS[code] ?? code;
}

/**
 * 风险告警此前只被压缩成一个「低/中/高」徽标，`code` / `severity` / `evidence`
 * 三个已经算好的结构化字段在整个前端没有任何消费点——用户看不到究竟是哪条线被触发、
 * 依据是什么。high 一定直出，medium 收进折叠（`HOLDING_COST_BASIS_LOSS` 会逐只触发，
 * 全展开会淹没摘要区）。
 */
function RiskAlertList({ alerts }: { alerts: Report["risk"]["alerts"] }) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  if (!alerts?.length) {
    return null;
  }
  const high = alerts.filter((alert) => alert.severity === "high");
  const rest = alerts.filter((alert) => alert.severity !== "high");
  const visible = expanded ? [...high, ...rest] : high;

  return (
    <div className="mt-3" data-testid="report-risk-alerts">
      <ul id={listId} className="space-y-1.5">
        {visible.map((alert, index) => (
          <li
            key={`${alert.code}-${index}`}
            className={`rounded-xl border px-3 py-2 text-xs leading-5 ${
              alert.severity === "high"
                ? "border-[var(--danger-border)] bg-[var(--danger-bg)] text-[var(--danger-fg)]"
                : "border-[var(--warn-border)] bg-[var(--warn-bg)] text-[var(--warn-fg)]"
            }`}
          >
            <span className="flex flex-wrap items-baseline gap-x-2">
              <strong className="font-black">{alertCodeLabel(alert.code)}</strong>
              <span className="min-w-0 break-words [overflow-wrap:anywhere]">{alert.message}</span>
            </span>
            {alert.evidence ? (
              <span className="mt-0.5 block break-words opacity-75 [overflow-wrap:anywhere]">
                依据：{alert.evidence}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {rest.length ? (
        <button
          type="button"
          aria-controls={listId}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
          className="mt-1.5 inline-flex min-h-11 items-center gap-1 rounded-lg px-2.5 text-xs font-bold text-slate-500 transition hover:bg-slate-50 hover:text-[var(--brand-strong)]"
        >
          {expanded
            ? "收起风险提醒"
            : high.length
              ? `另有 ${rest.length} 条中等风险提醒`
              : `查看 ${rest.length} 条中等风险提醒`}
          <ChevronDown
            aria-hidden="true"
            className={`size-3.5 transition-transform ${expanded ? "rotate-180" : ""}`}
          />
        </button>
      ) : null}
    </div>
  );
}

function Metric({ label, value, emphasis = false }: MetricProps) {
  return (
    <div
      className={`report-metric min-w-0 px-2 py-3 text-center sm:px-4 sm:py-3.5 ${
        emphasis
          ? "is-emphasis"
          : ""
      }`}
    >
      <dt className="break-words text-[10px] font-bold leading-4 tracking-wide text-slate-500 sm:text-xs">
        {label}
      </dt>
      <dd
        className={`tnum mt-1 break-words font-display text-base font-extrabold leading-tight sm:text-xl ${
          emphasis ? "text-[var(--brand-deep)]" : "text-slate-900"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

export function ReportSummaryHero({
  report,
  needsActionCount,
  isExporting,
  onExport,
}: ReportSummaryHeroProps) {
  const [metadataOpen, setMetadataOpen] = useState(false);
  const [portfolioOpen, setPortfolioOpen] = useState(false);
  const headingId = useId();
  const metadataId = useId();
  const portfolioId = useId();
  const portfolioLines = portfolioRecommendationLines(report);

  return (
    <section aria-labelledby={headingId} className="report-editorial-hero overflow-hidden p-4 sm:p-6">
      <div
        className="report-summary-layout grid min-w-0 gap-5"
        data-testid="report-summary-layout"
      >
        <div className="min-w-0">
          <p className="ink-label mb-2">Daily Brief · 持仓日报</p>
          <div className="mb-2 flex flex-wrap gap-2">
            <StatusPill tone={riskTone[report.risk.level]}>
              风险 {riskLabel[report.risk.level]}
            </StatusPill>
            <StatusPill tone="dark">{actionLabel[report.risk.suggested_action]}</StatusPill>
          </div>
          <h2
            id={headingId}
            className="font-display text-2xl font-extrabold tracking-tight text-[var(--brand-deep)]"
          >
            {report.title}
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--muted)]">{report.summary}</p>
          <RiskAlertList alerts={report.risk.alerts} />
        </div>

        {/* 「组合风险」那一格删掉了：左上角的风险胶囊已经写着同一个值。 */}
        <dl
          className="grid min-w-0 grid-cols-2 gap-2"
          data-testid="report-summary-metrics"
        >
          <Metric
            label="组合收益"
            value={`${report.risk.weighted_return_percent}%`}
            emphasis
          />
          <Metric label="需要处理" value={`${needsActionCount} 只`} />
        </dl>
      </div>

      {/* 这里原来是一条 6 格的「日报决策轨道」，逐格与本屏已有内容重复：
            02 组合变化 = 上面的「组合收益」
            03 风险判断 = 左上角的风险胶囊 + 原「组合风险」格
            04 建议动作 = 左上角的动作胶囊
            01 数据时间 = 下面「报告信息」里的生成时间
          剩下两格更彻底 —— 05「支撑证据 / 按需展开」和 06「后续追问 / 保持上下文」
          不是信息，是在向用户解释这个界面怎么用。整条删除。 */}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--line)] pt-3">
        <div className="flex flex-wrap gap-1">
          {portfolioLines.length ? (
            <button
              type="button"
              aria-controls={portfolioId}
              aria-expanded={portfolioOpen}
              onClick={() => setPortfolioOpen((value) => !value)}
              className="inline-flex min-h-11 items-center gap-1 rounded-lg px-2.5 py-2 text-xs font-bold text-slate-500 transition hover:bg-slate-50 hover:text-[var(--brand-strong)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)]"
            >
              组合说明
              <ChevronDown
                aria-hidden="true"
                className={`size-3.5 transition-transform ${portfolioOpen ? "rotate-180" : ""}`}
              />
            </button>
          ) : null}
          <button
            type="button"
            aria-controls={metadataId}
            aria-expanded={metadataOpen}
            onClick={() => setMetadataOpen((value) => !value)}
            className="inline-flex min-h-11 items-center gap-1 rounded-lg px-2.5 py-2 text-xs font-bold text-slate-500 transition hover:bg-slate-50 hover:text-[var(--brand-strong)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)]"
          >
            报告信息
            <ChevronDown
              aria-hidden="true"
              className={`size-3.5 transition-transform ${metadataOpen ? "rotate-180" : ""}`}
            />
          </button>
        </div>

        <button
          type="button"
          aria-busy={isExporting}
          aria-label={isExporting ? "正在导出 Markdown" : "导出 Markdown"}
          onClick={onExport}
          disabled={isExporting}
          className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-[var(--brand-deep)] px-4 py-2 text-sm font-bold text-white shadow-sm transition hover:bg-[var(--brand-strong)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--brand)] disabled:cursor-wait disabled:opacity-60"
        >
          {isExporting ? (
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          ) : (
            <Download aria-hidden="true" className="size-4" />
          )}
          {isExporting ? "导出中..." : "导出 Markdown"}
        </button>
      </div>

      {portfolioOpen ? (
        <ul
          id={portfolioId}
          data-testid="report-summary-portfolio"
          className="mt-3 space-y-1.5 rounded-2xl border border-[var(--info-border)] bg-[var(--info-bg)]/80 px-4 py-3 text-sm leading-6 text-slate-700"
        >
          {portfolioLines.map((line, index) => (
            <li key={`${index}-${line}`} className="pl-3 before:-ml-3 before:mr-2 before:text-[var(--brand)] before:content-['•']">
              {line}
            </li>
          ))}
        </ul>
      ) : null}

      {metadataOpen ? (
        <div
          id={metadataId}
          data-testid="report-summary-metadata"
          className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 rounded-2xl border border-slate-100 bg-slate-50/80 px-4 py-3 text-xs leading-5 text-slate-500"
        >
          <span>
            模型 <span className="font-semibold text-slate-700">{report.provider}</span>
          </span>
          <span>
            生成时间{" "}
            <time className="font-semibold text-slate-700" dateTime={report.created_at}>
              {report.created_at}
            </time>
          </span>
        </div>
      ) : null}
    </section>
  );
}
