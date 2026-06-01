"use client";

import { Newspaper } from "lucide-react";
import type { TopicBrief } from "@/lib/api";

type ReportNewsBriefPanelProps = {
  briefs: TopicBrief[];
};

const sentimentLabel = {
  bullish: "偏多",
  bearish: "偏空",
  neutral: "中性",
} as const;

const sentimentClass = {
  bullish: "bg-rose-50 text-rose-800 border-rose-100",
  bearish: "bg-emerald-50 text-emerald-800 border-emerald-100",
  neutral: "bg-slate-50 text-slate-700 border-slate-100",
} as const;

export function ReportNewsBriefPanel({ briefs }: ReportNewsBriefPanelProps) {
  if (!briefs.length) {
    return null;
  }

  return (
    <section className="rounded-[24px] border border-violet-100 bg-gradient-to-br from-violet-50/80 via-white to-white p-5 shadow-sm">
      <div className="mb-4 flex items-center gap-2 text-sm font-black text-slate-950">
        <Newspaper size={18} className="text-violet-600" />
        主题要闻摘要
        <span className="text-xs font-semibold text-slate-400">（Flash 按主题压缩，出处见下方原文列表）</span>
      </div>
      <div className="space-y-4">
        {briefs.map((brief) => (
          <article
            key={brief.topic}
            className="rounded-2xl border border-white bg-white/90 px-4 py-3 shadow-sm"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-black text-slate-950">{brief.topic}</h3>
              <span className="text-[10px] font-bold text-slate-400">
                {brief.news_count} 条 · {brief.provider}
              </span>
            </div>
            <p className="mt-2 text-sm leading-6 text-slate-700">{brief.summary}</p>
            {brief.points.length > 0 ? (
              <ul className="mt-3 space-y-2">
                {brief.points.map((point, index) => (
                  <li
                    key={`${brief.topic}-${index}`}
                    className={`rounded-xl border px-3 py-2 text-xs ${sentimentClass[point.sentiment]}`}
                  >
                    <span className="font-bold">[{sentimentLabel[point.sentiment]}]</span>{" "}
                    {point.headline}
                    {point.is_today ? (
                      <span className="ml-1 font-semibold opacity-80">· 当日</span>
                    ) : null}
                    {point.source_titles.length > 0 ? (
                      <div className="mt-1 text-[10px] opacity-75">
                        出处：{point.source_titles.join("；")}
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}
