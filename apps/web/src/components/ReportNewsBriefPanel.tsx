"use client";

import { ExternalLink, Newspaper } from "lucide-react";
import type { Report, TopicBrief, TopicBriefPoint } from "@/lib/api";

type MarketNewsItem = Report["market_news"][number];

type ReportNewsBriefPanelProps = {
  briefs: TopicBrief[];
  marketNews?: MarketNewsItem[];
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

function buildTitleUrlMap(marketNews: MarketNewsItem[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const item of marketNews) {
    if (item.title && item.url) {
      map.set(item.title, item.url);
    }
  }
  return map;
}

function resolvePointSources(
  point: TopicBriefPoint,
  titleUrlMap: Map<string, string>,
): Array<{ title: string; url: string | null }> {
  return point.source_titles.map((title) => ({
    title,
    url: titleUrlMap.get(title) ?? null,
  }));
}

function NewsSourceLink({ title, url }: { title: string; url: string }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-start gap-0.5 font-semibold underline decoration-violet-300 underline-offset-2 transition hover:text-violet-800"
    >
      <span>{title}</span>
      <ExternalLink size={10} className="mt-0.5 shrink-0 opacity-60" />
    </a>
  );
}

function dedupeLinkedSources(
  sources: Array<{ title: string; url: string }>,
): Array<{ title: string; url: string }> {
  const seen = new Set<string>();
  const unique: Array<{ title: string; url: string }> = [];
  for (const source of sources) {
    if (seen.has(source.url)) {
      continue;
    }
    seen.add(source.url);
    unique.push(source);
  }
  return unique;
}

function BriefPointItem({
  point,
  titleUrlMap,
}: {
  point: TopicBriefPoint;
  titleUrlMap: Map<string, string>;
}) {
  const sources = resolvePointSources(point, titleUrlMap);
  const linkedSources = dedupeLinkedSources(
    sources.filter((item): item is { title: string; url: string } => !!item.url),
  );
  const primaryUrl = linkedSources.length === 1 ? linkedSources[0].url : null;

  return (
    <li className={`rounded-xl border px-3 py-2 text-xs ${sentimentClass[point.sentiment]}`}>
      <span className="font-bold">[{sentimentLabel[point.sentiment]}]</span>{" "}
      {primaryUrl ? (
        <NewsSourceLink title={point.headline} url={primaryUrl} />
      ) : (
        point.headline
      )}
      {point.is_today ? <span className="ml-1 font-semibold opacity-80">· 当日</span> : null}
      {sources.length > 0 ? (
        <div className="mt-1.5 space-y-1 text-[10px] leading-5 opacity-90">
          {linkedSources.length > 1 ? (
            <>
              <div className="font-semibold opacity-75">出处</div>
              {linkedSources.map((source) => (
                <div key={`${source.url}-${source.title}`}>
                  <NewsSourceLink title={source.title} url={source.url} />
                </div>
              ))}
            </>
          ) : linkedSources.length === 0 ? (
            <div className="opacity-75">出处：{sources.map((item) => item.title).join("；")}</div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

export function ReportNewsBriefPanel({ briefs, marketNews = [] }: ReportNewsBriefPanelProps) {
  if (!briefs.length) {
    return null;
  }

  const titleUrlMap = buildTitleUrlMap(marketNews);

  return (
    <section className="rounded-[24px] border border-violet-100 bg-gradient-to-br from-violet-50/80 via-white to-white p-5 shadow-sm">
      <div className="mb-4 flex items-center gap-2 text-sm font-black text-slate-950">
        <Newspaper size={18} className="text-violet-600" />
        主题要闻摘要
        <span className="text-xs font-semibold text-slate-400">（点击要点或出处跳转原文）</span>
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
                  <BriefPointItem
                    key={`${brief.topic}-${index}`}
                    point={point}
                    titleUrlMap={titleUrlMap}
                  />
                ))}
              </ul>
            ) : null}
          </article>
        ))}
      </div>
    </section>
  );
}
