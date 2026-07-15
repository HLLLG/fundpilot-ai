import {
  BadgeCheck,
  CircleAlert,
  Clock3,
  ReceiptText,
  RefreshCw,
} from "lucide-react";
import type {
  FundTradeability,
  FundTradeabilityGate,
  FundTransactionCostAssessment,
  HoldingTransactionExecution,
} from "@/lib/api";

type FundTradeabilityEvidenceProps = {
  tradeability?: FundTradeability;
  tradeabilityGate?: FundTradeabilityGate;
  costAssessment?: FundTransactionCostAssessment;
  holdingTransactionExecution?: HoldingTransactionExecution;
  compact?: boolean;
};

const STATE_LABELS: Record<string, string> = {
  open: "开放",
  limited: "限大额",
  suspended: "暂停",
  closed: "封闭",
  subscription_period: "认购期",
  exchange_only: "仅场内",
  unknown: "待核验",
};

function isFiniteNumber(value: number | null | undefined): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function formatMoney(value: number | null | undefined): string {
  if (!isFiniteNumber(value)) return "待核验";
  return `¥${value.toLocaleString("zh-CN", {
    maximumFractionDigits: 2,
  })}`;
}

function formatCheckedAt(value: string | null | undefined): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

function sourceLabel(source: string): string {
  if (source.includes("fund_purchase_em")) return "东方财富申赎清单";
  if (source.includes("fundf10") || source.includes("purchase_info")) {
    return "东方财富基金费率页";
  }
  return source;
}

function statusLabel(state: string | null | undefined, raw: string | null | undefined): string {
  if (state && STATE_LABELS[state]) return STATE_LABELS[state];
  return raw?.trim() || "待核验";
}

function statusTone(state: string | null | undefined): string {
  if (state === "open") return "bg-emerald-100 text-emerald-900";
  if (state === "limited" || state === "subscription_period") {
    return "bg-amber-100 text-amber-900";
  }
  if (state === "suspended" || state === "closed" || state === "exchange_only") {
    return "bg-rose-100 text-rose-900";
  }
  return "bg-slate-200 text-slate-700";
}

function gateMeta(status: string | undefined) {
  if (status === "eligible") {
    return {
      label: "执行门禁通过",
      className: "bg-emerald-100 text-emerald-900",
      Icon: BadgeCheck,
    };
  }
  if (status === "excluded") {
    return {
      label: "场外申购排除",
      className: "bg-rose-100 text-rose-900",
      Icon: CircleAlert,
    };
  }
  return {
    label: "仅研究观察",
    className: "bg-amber-100 text-amber-900",
    Icon: CircleAlert,
  };
}

export function FundTradeabilityEvidence({
  tradeability,
  tradeabilityGate,
  costAssessment,
  holdingTransactionExecution,
  compact = false,
}: FundTradeabilityEvidenceProps) {
  const gate =
    tradeabilityGate ?? tradeability?.tradeability_gate ?? costAssessment?.tradeability_gate;
  const hasSnapshot = Boolean(
      (tradeability && Object.keys(tradeability).length > 0) ||
      (gate && Object.keys(gate).length > 0) ||
      (costAssessment && Object.keys(costAssessment).length > 0) ||
      (holdingTransactionExecution && Object.keys(holdingTransactionExecution).length > 0),
  );

  if (!hasSnapshot) {
    return (
      <div
        className={
          compact
            ? "rounded-lg border border-dashed border-slate-200 bg-slate-50/70 px-2.5 py-2 text-[11px] leading-5 text-slate-500"
            : "mt-3 rounded-xl border border-dashed border-slate-200 bg-slate-50/70 px-3 py-2.5 text-xs leading-5 text-slate-500"
        }
        aria-label="交易条件未记录"
      >
        历史报告未记录交易条件；重新扫描后可补齐申赎状态、起购额与限额。
      </div>
    );
  }

  const purchaseState = tradeability?.purchase_state ?? "unknown";
  const redemptionState = tradeability?.redemption_state ?? "unknown";
  const initialMinimum =
    tradeability?.minimum_initial_purchase_yuan ??
    tradeability?.minimums?.initial_yuan ??
    tradeability?.minimum_purchase_yuan;
  const additionalMinimum =
    holdingTransactionExecution?.effective_additional_min_purchase_yuan ??
    tradeability?.minimum_additional_purchase_yuan ??
    tradeability?.minimums?.additional_yuan;
  const effectiveInitial =
    gate?.effective_initial_min_purchase_yuan ?? costAssessment?.minimum_purchase_yuan;
  const dailyLimit =
    holdingTransactionExecution?.max_purchase_yuan ??
    gate?.max_purchase_yuan ??
    tradeability?.daily_purchase_limit_yuan ??
    costAssessment?.daily_purchase_limit_yuan;
  const unlimited =
    holdingTransactionExecution?.max_purchase_unlimited ??
    gate?.max_purchase_unlimited ??
    tradeability?.daily_purchase_limit_unlimited ??
    costAssessment?.daily_purchase_limit_unlimited;
  const sourceIds = [
    ...(tradeability?.source_ids ?? []),
    ...(costAssessment?.source_ids ?? []),
  ];
  const sources = [...new Set(sourceIds.map(sourceLabel))];
  const checkedAt = formatCheckedAt(tradeability?.checked_at ?? costAssessment?.checked_at);
  const revalidationRequired =
    gate?.revalidation_required === true || tradeability?.revalidation_required === true;
  const totalCost = costAssessment?.estimated_total_cost_upper_bound_percent;
  const fundMinimumHoldingDays =
    tradeability?.explicit_minimum_holding_days ??
    costAssessment?.fund_minimum_holding_days;
  const baseGatePresentation = gateMeta(
    holdingTransactionExecution
      ? holdingTransactionExecution.add_status
      : gate?.status,
  );
  const gatePresentation = {
    ...baseGatePresentation,
    label: holdingTransactionExecution
      ? holdingTransactionExecution.add_status === "eligible"
        ? "追加门禁通过"
        : "追加需复核"
      : baseGatePresentation.label,
  };
  const GateIcon = gatePresentation.Icon;
  const initialText = formatMoney(initialMinimum ?? effectiveInitial);
  const effectiveDiffers =
    isFiniteNumber(initialMinimum) &&
    isFiniteNumber(effectiveInitial) &&
    effectiveInitial > initialMinimum;
  const limitText = unlimited === true ? "无限额" : formatMoney(dailyLimit);
  const costText = isFiniteNumber(totalCost) ? `约 ${totalCost.toFixed(2)}%` : "待核验";

  if (compact) {
    return (
      <div
        className="rounded-xl border border-slate-200 bg-slate-50/75 px-2.5 py-2"
        aria-label="基金交易条件"
      >
        <div className="flex flex-wrap items-center gap-1 text-[10px] font-bold">
          <span className={`rounded-full px-2 py-0.5 ${statusTone(purchaseState)}`}>
            申购{statusLabel(purchaseState, tradeability?.purchase_status)}
          </span>
          <span className={`rounded-full px-2 py-0.5 ${statusTone(redemptionState)}`}>
            赎回{statusLabel(redemptionState, tradeability?.redemption_status)}
          </span>
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${gatePresentation.className}`}>
            <GateIcon size={10} aria-hidden="true" />
            {gatePresentation.label}
          </span>
        </div>
        <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] leading-5">
          <div>
            <dt className="inline text-slate-500">首次起购 </dt>
            <dd className="inline font-bold tabular-nums text-slate-800">{initialText}</dd>
          </div>
          <div>
            <dt className="inline text-slate-500">追加起购 </dt>
            <dd className="inline font-bold tabular-nums text-slate-800">
              {formatMoney(additionalMinimum)}
            </dd>
          </div>
          <div>
            <dt className="inline text-slate-500">单日限额 </dt>
            <dd className="inline font-bold tabular-nums text-slate-800">{limitText}</dd>
          </div>
          <div>
            <dt className="inline text-slate-500">最低持有 </dt>
            <dd className="inline font-bold tabular-nums text-slate-800">
              {isFiniteNumber(fundMinimumHoldingDays)
                ? `${fundMinimumHoldingDays} 天`
                : "待核验"}
            </dd>
          </div>
        </dl>
        {sources.length || checkedAt || revalidationRequired ? (
          <p className="mt-1.5 break-words text-[10px] leading-4 text-slate-500 [overflow-wrap:anywhere]">
            {sources.length ? `来源：${sources.join(" + ")}` : "来源待核验"}
            {checkedAt ? ` · 核验 ${checkedAt}` : ""}
            {revalidationRequired ? " · 下单前复核" : ""}
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <section
      className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-slate-50/75"
      aria-label="交易条件与成本核验"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200/80 px-3 py-2.5">
        <h4 className="flex items-center gap-1.5 text-xs font-black text-slate-900">
          <ReceiptText size={14} className="text-[var(--brand)]" aria-hidden="true" />
          交易条件与成本核验
        </h4>
        <div className="flex flex-wrap items-center gap-1 text-[10px] font-bold">
          <span className={`rounded-full px-2 py-1 ${statusTone(purchaseState)}`}>
            申购{statusLabel(purchaseState, tradeability?.purchase_status)}
          </span>
          <span className={`rounded-full px-2 py-1 ${statusTone(redemptionState)}`}>
            赎回{statusLabel(redemptionState, tradeability?.redemption_status)}
          </span>
          <span className={`inline-flex items-center gap-1 rounded-full px-2 py-1 ${gatePresentation.className}`}>
            <GateIcon size={11} aria-hidden="true" />
            {gatePresentation.label}
          </span>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-px bg-slate-200/70 sm:grid-cols-4">
        <div className="bg-white/85 px-3 py-2.5">
          <dt className="text-[10px] font-bold tracking-wide text-slate-500">首次起购</dt>
          <dd className="mt-1 text-xs font-black tabular-nums text-slate-900">
            {initialText}
          </dd>
          {effectiveDiffers ? (
            <p className="mt-0.5 text-[10px] text-slate-500">
              本台执行门槛 {formatMoney(effectiveInitial)}
            </p>
          ) : null}
        </div>
        <div className="bg-white/85 px-3 py-2.5">
          <dt className="text-[10px] font-bold tracking-wide text-slate-500">追加起购</dt>
          <dd className="mt-1 text-xs font-black tabular-nums text-slate-900">
            {formatMoney(additionalMinimum)}
          </dd>
        </div>
        <div className="bg-white/85 px-3 py-2.5">
          <dt className="text-[10px] font-bold tracking-wide text-slate-500">单日申购限额</dt>
          <dd className="mt-1 text-xs font-black tabular-nums text-slate-900">{limitText}</dd>
          <p className="mt-0.5 text-[10px] text-slate-500">
            最低持有期{isFiniteNumber(fundMinimumHoldingDays) ? ` ${fundMinimumHoldingDays} 天` : "待核验"}
          </p>
        </div>
        <div className="bg-white/85 px-3 py-2.5">
          <dt className="text-[10px] font-bold tracking-wide text-slate-500">
            未折扣标准费率成本上限
          </dt>
          <dd className="mt-1 text-xs font-black tabular-nums text-slate-900">{costText}</dd>
          {costAssessment?.minimum_holding_days != null ? (
            <p className="mt-0.5 text-[10px] text-slate-500">
              按最短 {costAssessment.minimum_holding_days} 天
            </p>
          ) : null}
        </div>
      </dl>

      <div className="space-y-1 px-3 py-2.5 text-[11px] leading-5 text-slate-600">
        <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          {sources.length ? <span>来源：{sources.join(" + ")}</span> : <span>来源待核验</span>}
          {checkedAt ? (
            <span className="inline-flex items-center gap-1">
              <Clock3 size={11} aria-hidden="true" />
              核验 {checkedAt}
            </span>
          ) : null}
          {revalidationRequired ? (
            <span className="inline-flex items-center gap-1 font-bold text-amber-800">
              <RefreshCw size={11} aria-hidden="true" />
              下单前复核
            </span>
          ) : null}
        </p>
        <p className="font-medium text-amber-900">
          费用按未折扣标准费率保守估算，不代表销售平台最终成交费；状态、剩余额度与到账规则以实际下单页为准。
        </p>
      </div>
    </section>
  );
}
