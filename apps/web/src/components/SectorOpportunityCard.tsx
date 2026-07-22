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
  forming: { label: "主线形成中", className: "status-info ring-1 ring-[var(--info-border)]" },
  confirmed: { label: "主线已确认", className: "status-good ring-1 ring-[var(--success-border)]" },
  crowded: { label: "主线拥挤过热", className: "status-warn ring-1 ring-[var(--warn-border)]" },
  fading: { label: "主线退潮", className: "status-bad ring-1 ring-[var(--danger-border)]" },
  neutral: { label: "尚未形成主线", className: "status-neutral ring-1 ring-[var(--line)]" },
  insufficient: { label: "主线证据不足", className: "status-neutral ring-1 ring-[var(--line)]" },
};

const ENTRY_STATE: Record<string, { label: string; className: string; cardClassName: string }> = {
  ready_to_start: {
    label: "可以开始布局",
    className: "status-good ring-1 ring-[var(--success-border)]",
    cardClassName: "border-[var(--success-border)] bg-[var(--success-bg)]/50 shadow-[inset_3px_0_0_var(--success-icon)]",
  },
  ready_on_pullback: {
    label: "等待合适位置",
    className: "status-warn ring-1 ring-[var(--warn-border)]",
    cardClassName: "border-[var(--warn-border)] bg-[var(--warn-bg)]/50 shadow-[inset_3px_0_0_var(--warn-icon)]",
  },
  forming: {
    label: "条件形成中",
    className: "status-info ring-1 ring-[var(--info-border)]",
    cardClassName: "border-[var(--line)] bg-[var(--surface-muted)]/70 shadow-[inset_3px_0_0_var(--muted-soft)]",
  },
  invalid: {
    label: "暂不参与",
    className: "status-bad ring-1 ring-[var(--danger-border)]",
    cardClassName: "border-[var(--danger-border)] bg-[var(--danger-bg)]/40 shadow-[inset_3px_0_0_var(--danger-icon)]",
  },
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
  const isEntryV2 = item.score_policy_version === "sector_entry_maturity.2026-07.v2";
  const entryMeta = ENTRY_STATE[item.entry_state ?? ""] ?? ENTRY_STATE.forming;
  return (
    <div
      className={`rounded-xl border px-3 py-3 ${
        isEntryV2
          ? entryMeta.cardClassName
          : isUnavailable
            ? "border-slate-100 bg-slate-50/40"
            : "border-slate-100 bg-slate-50/70"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-bold text-slate-900">{item.sector_label}</div>
        <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-bold">
          {isEntryV2 ? (
            <span className={`rounded-full px-2 py-0.5 ring-1 ${entryMeta.className}`}>
              {entryMeta.label}
            </span>
          ) : null}
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
            <span className="rounded-full bg-[var(--info-bg)] px-2 py-0.5 text-[var(--info-fg)] ring-1 ring-blue-100">
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
      {mainline && !isEntryV2 ? (
        <div data-testid="mainline-evidence" className="mt-2 rounded-lg border border-[var(--info-border)] bg-[var(--info-bg)]/60 px-2.5 py-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-[10px] font-bold text-[var(--info-icon)]">主线雷达 · 仅研究排序</div>
            <div className="text-[11px] font-bold text-[var(--info-fg)]">
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
            <p key={line} className="mt-1 break-words text-[11px] leading-4 text-[var(--info-fg)]">· {line}</p>
          ))}
          {(mainline.risks ?? []).slice(0, 1).map((line) => (
            <p key={line} className="mt-1 break-words text-[11px] leading-4 text-[var(--warn-fg)]">风险：{line}</p>
          ))}
        </div>
      ) : null}
      {isEntryV2 ? (
        <>
          <div className="mt-2 grid grid-cols-3 gap-1.5 text-xs text-slate-600">
            <Metric label="方向潜力" value={`${formatMetric(item.direction_score)} 分`} />
            <Metric label="形态成熟" value={`${formatMetric(item.setup_maturity_score)} 分`} />
            <Metric label="入场成熟" value={`${formatMetric(item.entry_readiness_score)} 分`} />
          </div>
          {item.entry_reason ? (
            <p className="mt-2 text-xs font-medium leading-5 text-slate-700">{item.entry_reason}</p>
          ) : null}
          <div className="mt-2 grid grid-cols-2 gap-1.5 text-[11px] text-slate-600">
            <Metric label="近1日 / 近5日" value={`${formatMetric(item.change_1d_percent)} / ${formatMetric(item.change_5d_percent)}%`} />
            <Metric
              label="5日主力"
              value={flowMetric(item.cumulative_5d_net_yi, item.five_day_available, "历史待补")}
            />
          </div>
          {(item.entry_triggers ?? []).length ? (
            <div className="mt-2 border-t border-slate-200/80 pt-2">
              <div className="text-[10px] font-black tracking-wide text-slate-500">
                {item.entry_state === "ready_to_start" ? "后续复核" : "等待条件"}
              </div>
              {(item.entry_triggers ?? []).slice(0, 2).map((line, index) => (
                <p key={`${line}-${index}`} className="mt-1 text-[11px] leading-4 text-slate-700">· {line}</p>
              ))}
            </div>
          ) : null}
        </>
      ) : (
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
      )}
      {!isEntryV2 && (item.pattern_label || item.entry_hint) ? (
        <p className="mt-2 break-words text-xs leading-5 text-slate-500">
          {item.pattern_label ? patternLabel(item.pattern_label) : ""}
          {item.pattern_label && item.entry_hint ? " · " : ""}
          {item.entry_hint ?? ""}
        </p>
      ) : null}
      {divergenceLines.length ? (
        <div className="mt-2 rounded-lg border border-[var(--info-border)] bg-[var(--info-bg)]/80 px-2.5 py-2">
          <div className="text-[10px] font-bold text-[var(--info-fg)]">历史回测证据</div>
          {divergenceLines.map((line) => (
            <p key={line} className="mt-1 break-words text-xs leading-5 text-[var(--info-fg)]">
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
