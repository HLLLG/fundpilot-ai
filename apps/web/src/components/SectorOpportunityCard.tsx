import type { SectorOpportunity, SectorSignalBacktestSector } from "@/lib/api";
import { divergenceBacktestLines, formatMetric, patternLabel, trackLabel } from "@/lib/decisionText";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg bg-white px-2 py-1.5 ring-1 ring-slate-100">
      <div className="text-[10px] font-bold text-slate-400">{label}</div>
      <div className="mt-0.5 break-words font-semibold text-slate-800">{value}</div>
    </div>
  );
}

function flowMetric(
  value: number | null | undefined,
  available: boolean | undefined,
  missingLabel: string,
): string {
  if (available === false || value == null || !Number.isFinite(value)) {
    return missingLabel;
  }
  return `${formatMetric(value)} 亿`;
}

type SectorOpportunityCardProps = {
  item: SectorOpportunity;
  /** Shown when the sector currently doesn't constitute an actionable opportunity (日报持仓场景). */
  unavailableHint?: string;
  /** M1.3：该板块「量价背离」信号的历史回测（仅日报持仓场景按板块反查传入，market_top 场景通常没有）。 */
  divergenceBacktest?: SectorSignalBacktestSector | null;
};

/**
 * Shared sector-direction card: used by 荐基 ("本次主方向") and 日报
 * ("板块轮动参考" / 持仓板块方向) so both surfaces speak the same visual language.
 */
export function SectorOpportunityCard({
  item,
  unavailableHint,
  divergenceBacktest,
}: SectorOpportunityCardProps) {
  const isUnavailable = item.opportunity_available === false;
  const divergenceLines = divergenceBacktestLines(divergenceBacktest);
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
        <Metric
          label="今日主力"
          value={flowMetric(item.today_main_force_net_yi, item.today_available, "今日数据暂缺")}
        />
        <Metric
          label="5日主力"
          value={flowMetric(item.cumulative_5d_net_yi, item.five_day_available, "5日历史暂缺")}
        />
      </div>
      {item.pattern_label || item.entry_hint ? (
        <p className="mt-2 break-words text-xs leading-5 text-slate-500">
          {item.pattern_label ? patternLabel(item.pattern_label) : ""}
          {item.pattern_label && item.entry_hint ? " · " : ""}
          {item.entry_hint ?? ""}
        </p>
      ) : null}
      {divergenceLines.length ? (
        <div className="mt-2 rounded-lg border border-blue-100 bg-blue-50/60 px-2.5 py-2">
          <div className="text-[10px] font-bold text-blue-700">历史回测证据</div>
          {divergenceLines.map((line) => (
            <p key={line} className="mt-1 break-words text-xs leading-5 text-blue-900">
              {line}
            </p>
          ))}
        </div>
      ) : null}
      {isUnavailable && unavailableHint ? (
        <p className="mt-1.5 break-words text-xs leading-5 text-slate-400">{unavailableHint}</p>
      ) : null}
    </div>
  );
}
