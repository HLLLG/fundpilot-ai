import type { SectorOpportunity } from "@/lib/api";
import { formatMetric, patternLabel, trackLabel } from "@/lib/decisionText";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg bg-white px-2 py-1.5 ring-1 ring-slate-100">
      <div className="text-[10px] font-bold text-slate-400">{label}</div>
      <div className="mt-0.5 break-words font-semibold text-slate-800">{value}</div>
    </div>
  );
}

type SectorOpportunityCardProps = {
  item: SectorOpportunity;
  /** Shown when the sector currently doesn't constitute an actionable opportunity (日报持仓场景). */
  unavailableHint?: string;
};

/**
 * Shared sector-direction card: used by 荐基 ("本次主方向") and 日报
 * ("板块轮动参考" / 持仓板块方向) so both surfaces speak the same visual language.
 */
export function SectorOpportunityCard({ item, unavailableHint }: SectorOpportunityCardProps) {
  const isUnavailable = item.opportunity_available === false;
  return (
    <div
      className={`rounded-xl border px-3 py-3 ${
        isUnavailable ? "border-slate-100 bg-slate-50/40" : "border-slate-100 bg-slate-50/70"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-bold text-slate-900">{item.sector_label}</div>
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-bold">
          {item.track ? (
            <span className="rounded-full bg-white px-2 py-0.5 text-slate-600 ring-1 ring-slate-200">
              {trackLabel(item.track)}
            </span>
          ) : null}
          {item.confidence ? (
            <span className="rounded-full bg-blue-50 px-2 py-0.5 text-blue-800 ring-1 ring-blue-100">
              {item.confidence}
            </span>
          ) : null}
          {isUnavailable ? (
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-500 ring-1 ring-slate-200">
              暂非机会
            </span>
          ) : null}
        </div>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-slate-600">
        <Metric label="机会评分" value={formatMetric(item.score)} />
        <Metric label="近1日/近5日" value={`${formatMetric(item.change_1d_percent)} / ${formatMetric(item.change_5d_percent)}%`} />
        <Metric label="今日主力" value={`${formatMetric(item.today_main_force_net_yi)} 亿`} />
        <Metric label="5日主力" value={`${formatMetric(item.cumulative_5d_net_yi)} 亿`} />
      </div>
      {item.pattern_label || item.entry_hint ? (
        <p className="mt-2 break-words text-xs leading-5 text-slate-500">
          {item.pattern_label ? patternLabel(item.pattern_label) : ""}
          {item.pattern_label && item.entry_hint ? " · " : ""}
          {item.entry_hint ?? ""}
        </p>
      ) : null}
      {isUnavailable && unavailableHint ? (
        <p className="mt-1.5 break-words text-xs leading-5 text-slate-400">{unavailableHint}</p>
      ) : null}
    </div>
  );
}
