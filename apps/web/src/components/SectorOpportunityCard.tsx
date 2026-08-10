"use client";

import { useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { SectorOpportunity, SectorSignalBacktestSector } from "@/lib/api";
import { divergenceBacktestLines, formatMetric, patternLabel, trackLabel } from "@/lib/decisionText";
import { MethodologyNote } from "@/components/MethodologyNote";

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

export const ENTRY_MATURITY_V2 = "sector_entry_maturity.2026-07.v2";
export const ENTRY_MATURITY_V3 = "sector_entry_maturity.2026-08.v3";

export function isEntryMaturityPolicy(version: string | null | undefined): boolean {
  return version === ENTRY_MATURITY_V2 || version === ENTRY_MATURITY_V3;
}

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

/** 趋势成形信号分的分档标签。
 *
 * 这里刻意不用「概率」和「大概率」这类措辞。后端那个数是
 * `15 + 加权信号分 × 0.82` 的仿射变换，从未做过校准——没有人验证过标成 70% 的方向是否
 * 真有约七成形成了趋势。一个各项都中性（全 50 分）的方向按该公式就会读出 56%，把它显示
 * 成「56% 大概率形成」等于对用户宣称一件系统无法兑现的事。数值原样保留（信息量完全相同），
 * 只把单位从「概率」改回「信号分」。校准（记录 N 日后是否真的进入 ready 并画可靠性曲线）
 * 完成之后才配得上「概率」这个词。
 */
const SIGNAL_BAND: Record<string, string> = {
  low: "信号偏弱",
  watch: "接近试仓线",
  early_probe: "早期成形",
  building: "信号偏强",
  confirmed: "趋势较明确",
  strong: "强趋势",
};

function entryStateDisplayLabel(item: SectorOpportunity): string {
  if (item.probability_early_probe_eligible) {
    return "可提前试仓";
  }
  if (item.entry_state !== "ready_on_pullback") {
    return ENTRY_STATE[item.entry_state ?? ""]?.label ?? ENTRY_STATE.forming.label;
  }
  switch (item.waiting_reason_code) {
    case "flow_confirmation":
      return "等待资金确认";
    case "fund_entry_confirmation":
      return "等待基金信号";
    case "structure_repair":
      return "等待结构修复";
    case "trend_confirmation":
      return "等待趋势确认";
    default:
      return ENTRY_STATE.ready_on_pullback.label;
  }
}

function mainlineMetric(value: number | null | undefined, suffix = "%"): string {
  if (value == null || !Number.isFinite(value)) return "待补";
  return `${value > 0 ? "+" : ""}${formatMetric(value)}${suffix}`;
}

function weightLabel(weight: number | null | undefined): string {
  if (weight == null || !Number.isFinite(weight)) return "";
  return `(${Math.round(weight * 100)}%)`;
}

function formatPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "较低比例";
  return `${Math.round(value * 100)}%`;
}

type SectorOpportunityCardProps = {
  item: SectorOpportunity;
  /** Keep the direction summary visible while supporting evidence starts collapsed. */
  collapsibleDetails?: boolean;
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
  collapsibleDetails = false,
  unavailableHint,
  divergenceBacktest,
}: SectorOpportunityCardProps) {
  const generatedId = useId();
  const [detailsOpen, setDetailsOpen] = useState(false);
  const isUnavailable = item.opportunity_available === false;
  const divergenceLines = divergenceBacktestLines(divergenceBacktest);
  const mainline = item.mainline_regime;
  const mainlineMeta = MAINLINE_STATUS[mainline?.status ?? ""] ?? MAINLINE_STATUS.insufficient;
  const mainlineFeatures = mainline?.features;
  const isEntryV3 = item.score_policy_version === ENTRY_MATURITY_V3;
  const hasEntryMaturity = isEntryMaturityPolicy(item.score_policy_version);
  const entryMeta = item.probability_early_probe_eligible
    ? {
        label: "可提前试仓",
        className: "status-good ring-1 ring-[var(--success-border)]",
        cardClassName: "border-[var(--info-border)] bg-[var(--info-bg)]/35 shadow-[inset_3px_0_0_var(--info-icon)]",
      }
    : ENTRY_STATE[item.entry_state ?? ""] ?? ENTRY_STATE.forming;
  const entryLabel = entryStateDisplayLabel(item);
  const blockWeights = item.block_weights ?? {};
  const highElasticity =
    item.sector_elasticity_percentile != null && item.sector_elasticity_percentile >= 70;
  const flowInflectionPath = item.selection_path === "flow_inflection_probe";
  const probabilityEarlyPath = item.selection_path === "probability_early_probe";
  const formationSignalScore = item.trend_formation_probability;
  const signalBand = SIGNAL_BAND[item.formation_probability_band ?? ""] ?? "信号评估";
  const canCollapseDetails = collapsibleDetails && hasEntryMaturity;
  const showDetails = !canCollapseDetails || detailsOpen;
  const detailsContentId = `sector-opportunity-details-${generatedId.replace(/:/g, "")}`;
  return (
    <div
      className={`rounded-xl border px-3 py-3 ${
        hasEntryMaturity
          ? entryMeta.cardClassName
          : isUnavailable
            ? "border-slate-100 bg-slate-50/40"
            : "border-slate-100 bg-slate-50/70"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-bold text-slate-900">{item.sector_label}</div>
        <div className="flex min-w-0 items-center gap-1.5">
          <div className="flex flex-wrap items-center justify-end gap-1.5 text-[11px] font-bold">
            {hasEntryMaturity ? (
              <span className={`rounded-full px-2 py-0.5 ring-1 ${entryMeta.className}`}>
                {entryLabel}
              </span>
            ) : null}
            {item.track ? (
              <span className="rounded-full bg-white px-2 py-0.5 text-slate-600 ring-1 ring-slate-200">
                {trackLabel(item.track)}
              </span>
            ) : null}
            {isEntryV3 && flowInflectionPath ? (
              <span className="rounded-full bg-[var(--success-bg)] px-2 py-0.5 text-[var(--success-fg)] ring-1 ring-[var(--success-border)]">
                资金拐点
              </span>
            ) : null}
            {isEntryV3 && probabilityEarlyPath ? (
              <span className="rounded-full bg-[var(--success-bg)] px-2 py-0.5 text-[var(--success-fg)] ring-1 ring-[var(--success-border)]">
                提前试仓
              </span>
            ) : null}
            {isEntryV3 && highElasticity ? (
              <span
                data-testid="sector-high-elasticity"
                className="rounded-full bg-[var(--info-bg)] px-2 py-0.5 text-[var(--info-fg)] ring-1 ring-[var(--info-border)]"
              >
                高弹性
              </span>
            ) : null}
            {mainline ? (
              <span
                data-testid="mainline-status"
                className={`rounded-full px-2 py-0.5 ring-1 ${mainlineMeta.className}`}
                title="主线状态用于判断当前趋势阶段"
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
          {canCollapseDetails ? (
            <button
              type="button"
              onClick={() => setDetailsOpen((value) => !value)}
              aria-expanded={detailsOpen}
              aria-controls={detailsContentId}
              aria-label={`${detailsOpen ? "收起" : "展开"}${item.sector_label}方向详情`}
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-full text-slate-500 transition hover:bg-white/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--brand)]"
            >
              <ChevronDown
                size={16}
                aria-hidden="true"
                className={`transition ${detailsOpen ? "rotate-180" : ""}`}
              />
            </button>
          ) : null}
        </div>
      </div>
      {mainline && !hasEntryMaturity ? (
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
      {hasEntryMaturity ? (
        <>
          {isEntryV3 && formationSignalScore != null ? (
            <div
              data-testid="formation-probability"
              className="mt-2 overflow-hidden rounded-xl border border-[var(--info-border)] bg-white/85"
            >
              <div className="flex items-end justify-between gap-3 px-3 py-2.5">
                <div>
                  <div className="text-[10px] font-black tracking-[0.08em] text-slate-500">
                    趋势成形信号分
                  </div>
                  <div className="mt-0.5 flex items-baseline gap-2">
                    {/* 单位是「分」不是「%」：这个数没有经过校准，不能当概率读。 */}
                    <span className="font-mono text-xl font-black tabular-nums text-slate-950">
                      {Math.round(formationSignalScore)}
                      <span className="ml-0.5 text-[11px] font-bold text-slate-500">/100</span>
                    </span>
                    <span className="text-[11px] font-bold text-[var(--info-fg)]">{signalBand}</span>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] font-bold text-slate-500">本次比例</div>
                  <div className="mt-0.5 text-sm font-black text-slate-900">
                    计划仓位的 {formatPercent(item.first_tranche_scale)}
                  </div>
                </div>
              </div>
              <div className="h-1.5 bg-slate-100" aria-hidden="true">
                <div
                  className="h-full bg-[linear-gradient(90deg,var(--info-icon),var(--success-icon))] transition-[width]"
                  style={{ width: `${Math.max(5, Math.min(95, formationSignalScore))}%` }}
                />
              </div>
            </div>
          ) : null}
          {isEntryV3 && formationSignalScore == null && canCollapseDetails ? (
            <div className="mt-2 grid grid-cols-2 gap-1.5 text-xs text-slate-600">
              <Metric label="方向评分" value={`${formatMetric(item.direction_score)} 分`} />
              <Metric label="本次比例" value={`计划仓位的 ${formatPercent(item.first_tranche_scale)}`} />
            </div>
          ) : null}
          {!isEntryV3 && canCollapseDetails ? (
            <div className="mt-2 grid grid-cols-2 gap-1.5 text-xs text-slate-600">
              <Metric label="方向潜力" value={`${formatMetric(item.direction_score)} 分`} />
              <Metric label="入场成熟" value={`${formatMetric(item.entry_readiness_score)} 分`} />
            </div>
          ) : null}
          {showDetails ? (
            <div
              id={detailsContentId}
              data-testid={canCollapseDetails ? "sector-opportunity-details" : undefined}
              className={canCollapseDetails ? "mt-2 border-t border-slate-200/80 pt-2" : undefined}
            >
              {isEntryV3 ? (
                <>
                  <div className="grid grid-cols-3 gap-1.5 text-xs text-slate-600">
                    <Metric
                      label={`趋势强度 ${weightLabel(blockWeights.trend_strength)}`}
                      value={`${formatMetric(item.trend_strength_score)} 分`}
                    />
                    <Metric
                      label={`资金参与 ${weightLabel(blockWeights.participation)}`}
                      value={`${formatMetric(item.participation_score)} 分`}
                    />
                    <Metric
                      label={`结构修复 ${weightLabel(blockWeights.position_risk)}`}
                      value={`${formatMetric(item.position_risk_score)} 分`}
                    />
                  </div>
                  {/* 原来这里是两段分开的灰字（"概率含义" + "如何合成"），讲的是同
                      一个评分模型，而且卡片本身只有半栏宽 —— 合成一个口径入口，
                      免得一张小卡上排出好几个折叠触发器。权重本来就印在上面三个
                      指标的标签里（如「趋势强度 (70%)」）。 */}
                  <MethodologyNote label="评分口径" className="mt-1.5">
                    {formationSignalScore != null ? (
                      <p>
                        趋势成形信号分是趋势强度、资金参与度、结构修复、短期动量与资金加速度的
                        加权合成（0～100），衡量「这个方向正在成形的信号有多强」。
                        它<strong>不是概率、也不是收益预测</strong>：尚未用历史样本校准，
                        因此不能读成「几成会涨」。仓位不由它决定。
                      </p>
                    ) : null}
                    <p>
                      三项是互不重叠的独立维度，按括号内权重合成为方向分 {formatMetric(item.direction_score)}；
                      它们不是三重确认。
                    </p>
                  </MethodologyNote>
                  {probabilityEarlyPath || flowInflectionPath || highElasticity ? (
                    <div
                      data-testid="sector-selection-priority"
                      className="mt-2 rounded-lg border border-[var(--info-border)] bg-[var(--info-bg)]/60 px-2.5 py-2 text-[11px] leading-4 text-[var(--info-fg)]"
                    >
                      <div className="font-bold">
                        方向排序优先
                        {item.selection_priority_score != null
                          ? ` · ${formatMetric(item.selection_priority_score)} 分`
                          : ""}
                      </div>
                      {probabilityEarlyPath ? (
                        <p className="mt-1">领先资金、短期强度与结构共振，优先进入提前试仓复核。</p>
                      ) : flowInflectionPath ? (
                        <p className="mt-1">今日资金转强，优先于普通等待方向。</p>
                      ) : null}
                      {highElasticity ? (
                        <p className="mt-1">
                          20日年化波动 {formatMetric(item.sector_annualized_volatility_20d_percent)}%，
                          横截面 {formatMetric(item.sector_elasticity_percentile)} 分位。
                        </p>
                      ) : null}
                      <MethodologyNote label="边界" className="mt-1">
                        提前试仓仍须具体基金信号通过，不会因为排序靠前自动买入。
                      </MethodologyNote>
                    </div>
                  ) : null}
                </>
              ) : (
                <div className="grid grid-cols-3 gap-1.5 text-xs text-slate-600">
                  <Metric label="方向潜力" value={`${formatMetric(item.direction_score)} 分`} />
                  <Metric label="形态成熟" value={`${formatMetric(item.setup_maturity_score)} 分`} />
                  <Metric label="入场成熟" value={`${formatMetric(item.entry_readiness_score)} 分`} />
                </div>
              )}
              {item.entry_reason ? (
                <p className="mt-2 text-xs font-medium leading-5 text-slate-700">{item.entry_reason}</p>
              ) : null}
              {isEntryV3 && (item.overheat_flags ?? []).length ? (
                <div
                  data-testid="overheat-disclosure"
                  className="mt-2 rounded-lg border border-[var(--warn-border)] bg-[var(--warn-bg)]/60 px-2.5 py-2"
                >
                  <div className="text-[10px] font-bold text-[var(--warn-fg)]">
                    短期加速 · 本次金额按 {formatPercent(item.first_tranche_scale)} 计算
                  </div>
                  {(item.overheat_flags ?? []).slice(0, 2).map((line) => (
                    <p key={line} className="mt-1 break-words text-[11px] leading-4 text-[var(--warn-fg)]">
                      · {line}
                    </p>
                  ))}
                  {/* 上面一行已经写明「本次金额按 X% 计算」，这句只解释为什么。 */}
                  <MethodologyNote label="影响" className="mt-1">
                    过热不否决当前机会，但会缩小本次参考金额；买入后由日报继续跟踪。
                  </MethodologyNote>
                </div>
              ) : null}
              <div className="mt-2 grid grid-cols-3 gap-1.5 text-[11px] text-slate-600">
                <Metric label="近1日 / 近5日" value={`${formatMetric(item.change_1d_percent)} / ${formatMetric(item.change_5d_percent)}%`} />
                <Metric
                  label="今日主力"
                  value={flowMetric(item.today_main_force_net_yi, item.today_available, "今日待补")}
                />
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
      {!hasEntryMaturity && (item.pattern_label || item.entry_hint) ? (
        <p className="mt-2 break-words text-xs leading-5 text-slate-500">
          {item.pattern_label ? patternLabel(item.pattern_label) : ""}
          {item.pattern_label && item.entry_hint ? " · " : ""}
          {item.entry_hint ?? ""}
        </p>
      ) : null}
      {showDetails && divergenceLines.length ? (
        <div className="mt-2 rounded-lg border border-[var(--info-border)] bg-[var(--info-bg)]/80 px-2.5 py-2">
          <div className="text-[10px] font-bold text-[var(--info-fg)]">历史回测证据</div>
          {divergenceLines.map((line) => (
            <p key={line} className="mt-1 break-words text-xs leading-5 text-[var(--info-fg)]">
              {line}
            </p>
          ))}
        </div>
      ) : null}
      {showDetails && isUnavailable && unavailableHint ? (
        <p className="mt-1.5 break-words text-xs leading-5 text-slate-500">{unavailableHint}</p>
      ) : null}
    </div>
  );
}
