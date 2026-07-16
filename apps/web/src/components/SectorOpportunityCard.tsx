import type { SectorOpportunity, SectorSignalBacktestSector } from "@/lib/api";
import { divergenceBacktestLines, formatMetric, patternLabel, trackLabel } from "@/lib/decisionText";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg bg-white px-2 py-1.5 ring-1 ring-slate-100">
      <div className="text-[10px] font-bold text-slate-500">{label}</div>
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

const MAINLINE_STATUS: Record<string, { label: string; className: string }> = {
  forming: { label: "主线形成中", className: "bg-blue-50 text-blue-800 ring-blue-100" },
  confirmed: { label: "主线已确认", className: "bg-emerald-50 text-emerald-800 ring-emerald-100" },
  crowded: { label: "主线拥挤过热", className: "bg-amber-50 text-amber-800 ring-amber-100" },
  fading: { label: "主线退潮", className: "bg-rose-50 text-rose-800 ring-rose-100" },
  neutral: { label: "尚未形成主线", className: "bg-slate-100 text-slate-600 ring-slate-200" },
  insufficient: { label: "主线证据不足", className: "bg-slate-100 text-slate-500 ring-slate-200" },
};

function mainlineMetric(value: number | null | undefined, suffix = "%"): string {
  if (value == null || !Number.isFinite(value)) return "待补";
  return `${value > 0 ? "+" : ""}${formatMetric(value)}${suffix}`;
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
  const mainline = item.mainline_regime;
  const mainlineMeta = MAINLINE_STATUS[mainline?.status ?? ""] ?? MAINLINE_STATUS.insufficient;
  const mainlineFeatures = mainline?.features;
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
          {mainline ? (
            <span
              data-testid="mainline-status"
              className={`rounded-full px-2 py-0.5 ring-1 ${mainlineMeta.className}`}
              title="主线模型只参与研究排序，不替代交易门禁"
            >
              {mainlineMeta.label}
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
      {mainline ? (
        <div data-testid="mainline-evidence" className="mt-2 rounded-lg border border-indigo-100 bg-indigo-50/55 px-2.5 py-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-[10px] font-bold text-indigo-700">主线雷达 · 仅研究排序</div>
            <div className="text-[11px] font-bold text-indigo-900">
              {mainline.score == null ? "评分待补" : `主线分 ${formatMetric(mainline.score)}`}
            </div>
          </div>
          <div className="mt-1.5 grid grid-cols-3 gap-1.5 text-[10px] text-slate-600">
            <div>20日超额<br /><b className="text-slate-800">{mainlineMetric(mainlineFeatures?.relative_return_20d_percent)}</b></div>
            <div>强度分位<br /><b className="text-slate-800">{mainlineMetric(mainlineFeatures?.relative_strength_percentile)}</b></div>
            <div>上涨广度<br /><b className="text-slate-800">{mainlineMetric(mainlineFeatures?.advancing_ratio_percent)}</b></div>
          </div>
          {mainline.source_dates?.sector_price_source?.includes("proxy") ? (
            <p className="mt-1 text-[10px] leading-4 text-slate-500">
              价格口径：当前大市值成分股代理
              {mainline.source_dates.proxy_member_count ? `（${mainline.source_dates.proxy_member_count} 只）` : ""}
              ，非官方板块指数
            </p>
          ) : null}
          {(mainline.evidence ?? []).slice(0, 2).map((line) => (
            <p key={line} className="mt-1 break-words text-[11px] leading-4 text-indigo-950">· {line}</p>
          ))}
          {(mainline.risks ?? []).slice(0, 1).map((line) => (
            <p key={line} className="mt-1 break-words text-[11px] leading-4 text-amber-800">风险：{line}</p>
          ))}
        </div>
      ) : null}
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
        <p className="mt-1.5 break-words text-xs leading-5 text-slate-500">{unavailableHint}</p>
      ) : null}
    </div>
  );
}
