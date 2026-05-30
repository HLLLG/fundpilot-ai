"use client";

import { AlertTriangle, BarChart3, Clock3, Sparkles } from "lucide-react";
import type { Report } from "@/lib/api";
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

export function ReportPanel({ report }: ReportPanelProps) {
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
        <div className="rounded-3xl bg-slate-950 px-5 py-4 text-white">
          <div className="flex items-center gap-2 text-xs font-bold text-slate-300">
            <BarChart3 size={16} />
            加权收益率
          </div>
          <div className="mt-2 text-3xl font-black">{report.risk.weighted_return_percent}%</div>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <div className="rounded-[24px] bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center gap-2 text-sm font-black text-slate-950">
            <Sparkles size={18} className="text-blue-600" />
            决策建议
          </div>
          <div className="space-y-3">
            {report.recommendations.map((item, index) => (
              <div key={`${item}-${index}`} className="rounded-2xl border border-blue-100 bg-blue-50/60 px-4 py-3 text-sm leading-6 text-slate-700">
                {item}
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-[24px] bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center gap-2 text-sm font-black text-slate-950">
            <AlertTriangle size={18} className="text-amber-500" />
            风险提醒
          </div>
          <div className="space-y-3">
            {[...report.risk.alerts.map((alert) => alert.message), ...report.caveats].map(
              (item, index) => (
                <div key={`${item}-${index}`} className="rounded-2xl border border-amber-100 bg-amber-50/70 px-4 py-3 text-sm leading-6 text-slate-700">
                  {item}
                </div>
              ),
            )}
          </div>
        </div>
      </div>

      <div className="mt-5 rounded-[24px] bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center gap-2 text-sm font-black text-slate-950">
          <Clock3 size={18} className="text-emerald-500" />
          基金数据快照
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          {report.snapshots.map((snapshot, index) => (
            <div key={`${snapshot.fund_code}-${snapshot.source}-${snapshot.nav_date ?? "none"}-${index}`} className="rounded-2xl border border-slate-100 px-4 py-3">
              <div className="text-sm font-black text-slate-950">{snapshot.fund_name}</div>
              <div className="mt-1 text-xs text-slate-500">
                {snapshot.fund_code} · {snapshot.source}
                {snapshot.latest_nav ? ` · 净值 ${snapshot.latest_nav}` : ""}
                {snapshot.nav_date ? ` · ${snapshot.nav_date}` : ""}
              </div>
              {snapshot.note ? <div className="mt-2 text-xs leading-5 text-slate-500">{snapshot.note}</div> : null}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-5 rounded-[24px] bg-white p-5 shadow-sm">
        <div className="mb-4 text-sm font-black text-slate-950">养基宝核心指标</div>
        <div className="grid gap-3 md:grid-cols-2">
          {report.holdings.map((holding, index) => (
            <div key={`${holding.fund_code}-${holding.fund_name}-${index}`} className="rounded-2xl border border-slate-100 px-4 py-3">
              <div className="text-sm font-black text-slate-950">{holding.fund_name}</div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-slate-500">
                <span>当日 {holding.daily_profit ?? "-"} / {holding.daily_return_percent ?? "-"}%</span>
                <span>板块 {holding.sector_name || "-"}</span>
                <span>板块涨跌 {holding.sector_return_percent ?? "-"}%</span>
                <span>持有 {holding.holding_profit ?? "-"} / {holding.holding_return_percent ?? holding.return_percent}%</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-5 rounded-[24px] bg-white p-5 shadow-sm">
        <div className="mb-4 text-sm font-black text-slate-950">近期消息核查主题</div>
        <div className="grid gap-3 md:grid-cols-2">
          {report.market_context.map((item, index) => (
            <div key={`${item.topic}-${index}`} className="rounded-2xl border border-slate-100 px-4 py-3">
              <div className="text-sm font-black text-slate-950">{item.topic}</div>
              <div className="mt-1 text-xs leading-5 text-slate-500">{item.query}</div>
              <div className="mt-2 text-xs leading-5 text-slate-500">{item.note}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
