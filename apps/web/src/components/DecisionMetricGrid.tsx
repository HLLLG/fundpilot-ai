import {
  Archive,
  BadgeCheck,
  ReceiptText,
  Scale,
  ShieldOff,
  Target,
} from "lucide-react";
import type {
  OutcomeLegacyReference,
  OutcomeMetricName,
  OutcomeMetricStats,
  OutcomeMetricSummary,
} from "@/lib/api";

const METRIC_CARDS: Array<{
  key: OutcomeMetricName;
  label: string;
  badge: string;
  description: string;
  icon: typeof Target;
  accent: string;
  iconClass: string;
}> = [
  {
    key: "gross_direction",
    label: "毛收益方向",
    badge: "基础口径",
    description: "只判断上涨/下跌方向，不扣交易费用。",
    icon: Target,
    accent: "bg-slate-900",
    iconClass: "bg-slate-950 text-emerald-300",
  },
  {
    key: "positive_net_return",
    label: "假设费后正收益",
    badge: "用户假设",
    description: "按冻结的买卖合计费用假设扣减，不是实际账单。",
    icon: ReceiptText,
    accent: "bg-[var(--warn-icon)]",
    iconClass: "bg-[var(--warn-bg)] text-[var(--warn-icon)]",
  },
  {
    key: "gross_excess",
    label: "合同基准超额",
    badge: "正式基准",
    description: "仅完整、已冻结的基金合同基准进入统计。",
    icon: Scale,
    accent: "bg-blue-500",
    iconClass: "bg-[var(--info-bg)] text-[var(--info-fg)]",
  },
  {
    key: "net_excess",
    label: "费后合同超额",
    badge: "正式 + 假设",
    description: "用户费用假设扣减后，再与正式合同基准比较。",
    icon: BadgeCheck,
    accent: "bg-[var(--success-icon)]",
    iconClass: "bg-[var(--success-bg)] text-[var(--success-icon)]",
  },
];

function percent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${value}%`;
}

function MetricCard({
  config,
  stats,
}: {
  config: (typeof METRIC_CARDS)[number];
  stats?: OutcomeMetricStats;
}) {
  const Icon = config.icon;
  const eligible = stats?.eligible_count ?? 0;
  const mature = stats?.mature_count ?? 0;
  const coverage = Math.max(0, Math.min(stats?.coverage_percent ?? 0, 100));

  return (
    <article
      className="relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white px-3.5 py-3.5 shadow-[0_8px_28px_rgba(15,23,42,0.05)]"
      aria-label={`${config.label}：命中率 ${percent(stats?.hit_rate_percent)}，覆盖率 ${percent(stats?.coverage_percent)}`}
    >
      <div className={`absolute inset-y-0 left-0 w-1 ${config.accent}`} aria-hidden="true" />
      <div className="flex items-start justify-between gap-2 pl-1">
        <div className="min-w-0">
          <div className="text-[11px] font-black tracking-[0.04em] text-slate-900">
            {config.label}
          </div>
          <div className="mt-1 text-[10px] font-bold text-slate-400">{config.badge}</div>
        </div>
        <span className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${config.iconClass}`}>
          <Icon size={15} aria-hidden="true" />
        </span>
      </div>

      <div className="mt-3 flex items-end justify-between gap-3 pl-1 tabular-nums">
        <div>
          <div className="text-[10px] text-slate-400">命中率</div>
          <div className="mt-0.5 text-2xl font-black tracking-tight text-slate-950">
            {percent(stats?.hit_rate_percent)}
          </div>
        </div>
        <div className="pb-0.5 text-right text-[10px] leading-4 text-slate-500">
          <div>成熟 {mature}/{eligible}</div>
          <div>覆盖 {percent(stats?.coverage_percent)}</div>
        </div>
      </div>

      <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-slate-100" aria-hidden="true">
        <div className={`h-full rounded-full ${config.accent}`} style={{ width: `${coverage}%` }} />
      </div>
      <p className="mt-2 text-[10px] leading-4 text-slate-500">{config.description}</p>
    </article>
  );
}

export function DecisionMetricGrid({
  metrics,
  className = "",
}: {
  metrics?: OutcomeMetricSummary;
  className?: string;
}) {
  return (
    <div className={`grid gap-2 sm:grid-cols-2 xl:grid-cols-4 ${className}`} data-testid="decision-metric-grid">
      {METRIC_CARDS.map((config) => (
        <MetricCard key={config.key} config={config} stats={metrics?.[config.key]} />
      ))}
    </div>
  );
}

export function FeeBenchmarkMethodNote({ feePercent }: { feePercent?: number | null }) {
  const feeText = feePercent === null || feePercent === undefined
    ? "默认 1.5%（若未修改）"
    : `${feePercent}%`;
  return (
    <div className="rounded-2xl border border-[var(--warn-border)] bg-[linear-gradient(135deg,#fffbeb_0%,#ffffff_58%,#eff6ff_100%)] px-4 py-3 text-[11px] leading-5 text-slate-600">
      <div className="flex items-start gap-2">
        <ReceiptText size={15} className="mt-0.5 shrink-0 text-[var(--warn-icon)]" aria-hidden="true" />
        <p>
          <strong className="text-slate-900">费用口径：</strong>{feeText} 是你设置的买卖合计费用假设，
          <strong className="text-[var(--warn-fg)]">不是平台实际扣费</strong>；管理费、托管费等已反映在基金净值中，不会重复扣除。
          只有决策时冻结且组成完整的<strong className="text-[var(--info-fg)]">基金合同基准</strong>进入正式超额，跟踪指数和类别代理只作参考。
        </p>
      </div>
    </div>
  );
}

export function LegacyReferenceStrip({
  legacy,
  horizon,
  className = "",
}: {
  legacy?: OutcomeLegacyReference;
  horizon?: string;
  className?: string;
}) {
  const count = legacy?.recommendation_count ?? legacy?.total_count ?? 0;
  const reportCount = legacy?.report_count ?? 0;
  if (!legacy || (count <= 0 && reportCount <= 0)) return null;

  const horizonStats = horizon ? legacy.by_horizon?.[horizon] : undefined;
  const direction = horizonStats?.gross_direction ?? legacy.metrics?.gross_direction;
  const eligible = horizonStats?.eligible_count ?? legacy.eligible_count ?? direction?.eligible_count ?? 0;
  const mature = horizonStats?.mature_count ?? legacy.mature_count ?? direction?.mature_count ?? 0;
  const hitRate = horizonStats?.hit_rate_percent ?? legacy.hit_rate_percent ?? direction?.hit_rate_percent;

  return (
    <aside className={`rounded-2xl border border-dashed border-slate-300 bg-slate-100/70 px-4 py-3 ${className}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-black text-slate-700">
          <Archive size={15} className="text-slate-500" aria-hidden="true" />
          旧口径历史参考
        </div>
        <span className="inline-flex items-center gap-1 rounded-full border border-slate-300 bg-white px-2.5 py-1 text-[10px] font-bold text-slate-600">
          <ShieldOff size={12} aria-hidden="true" />
          已排除正式 V2 统计
        </span>
      </div>
      <p className="mt-2 text-[11px] leading-5 text-slate-600 tabular-nums">
        {reportCount ? `${reportCount} 份旧报告 · ` : ""}成熟 {mature}/{eligible} · 方向命中 {percent(hitRate)}。
        这些记录仍可复核，但缺少审计合格的持久化 DecisionEvent v2，不会混入上方四项指标。
      </p>
    </aside>
  );
}
