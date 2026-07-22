"use client";

import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronUp, Layers3, Loader2 } from "lucide-react";

import {
  fetchFundHoldingsDistribution,
  type FundDisclosureHolding,
  type FundHoldingsDistribution,
} from "@/lib/api";

type FundHoldingsDisclosureProps = {
  fundCode: string;
  enabled: boolean;
};

const COLLAPSED_ROW_COUNT = 5;

function reportLabel(value: string | null | undefined) {
  const matched = /^(\d{4})-Q([1-4])$/.exec(value ?? "");
  if (!matched) return "最新季报";
  const quarter = ["一", "二", "三", "四"][Number(matched[2]) - 1];
  return `${matched[1]} ${quarter}季报`;
}

function compactPeriod(value: string | null | undefined) {
  const matched = /^(\d{4})-Q([1-4])$/.exec(value ?? "");
  return matched ? `${matched[1].slice(2)}Q${matched[2]}` : "上期";
}

function shortDate(value: string | null | undefined) {
  const matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value ?? "");
  return matched ? `${matched[2]}-${matched[3]}` : value ?? "—";
}

function percent(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(2)}%`;
}

function signedPercent(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  const normalized = Math.abs(value) < 0.005 ? 0 : value;
  return `${normalized > 0 ? "+" : ""}${normalized.toFixed(2)}%`;
}

function quoteTone(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value) || Math.abs(value) < 0.005) {
    return "text-slate-400";
  }
  return value > 0 ? "text-rose-600" : "text-emerald-600";
}

function shortTime(value: string | null | undefined) {
  const matched = /T(\d{2}):(\d{2})/.exec(value ?? "");
  return matched ? `${matched[1]}:${matched[2]}` : null;
}

function changePresentation(row: FundDisclosureHolding) {
  const magnitude = Math.abs(row.change_percent_points ?? 0).toFixed(2);
  if (row.change_direction === "new") {
    return { label: "新增", className: "text-[var(--warn-icon)]" };
  }
  if (row.change_direction === "increased") {
    return { label: `↑ ${magnitude}`, className: "text-rose-600" };
  }
  if (row.change_direction === "decreased") {
    return { label: `↓ ${magnitude}`, className: "text-emerald-600" };
  }
  return { label: "持平", className: "text-slate-400" };
}

export function FundHoldingsDisclosure({
  fundCode,
  enabled,
}: FundHoldingsDisclosureProps) {
  const [distribution, setDistribution] = useState<FundHoldingsDistribution | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setExpanded(false);
    setDistribution(null);
    setError(false);
    if (!enabled || !/^\d{6}$/.test(fundCode) || fundCode === "000000") {
      setLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setLoading(true);
    void fetchFundHoldingsDistribution(fundCode)
      .then((result) => {
        if (!cancelled) setDistribution(result);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [enabled, fundCode]);

  const visibleRows = useMemo(() => {
    const rows = distribution?.holdings ?? [];
    return expanded ? rows : rows.slice(0, COLLAPSED_ROW_COUNT);
  }, [distribution?.holdings, expanded]);

  if (!enabled) return null;

  const available = distribution?.status === "available" && distribution.holdings.length > 0;
  const usesStockPosition = distribution?.display_weight_basis === "stock_position";
  const comparisonLabel = `较${compactPeriod(distribution?.previous_report_period)}`;
  const quoteHeaderLabel = distribution?.quote_session_date
    ? `${shortDate(distribution.quote_session_date)}涨幅`
    : "涨幅";
  const quoteTime = shortTime(distribution?.quote_updated_at);

  return (
    <section className="border-t border-slate-100 pt-3" aria-labelledby="fund-holdings-title">
      <div className="flex items-start justify-between gap-3 px-0.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-[var(--brand-strong)]">
              <Layers3 size={13} aria-hidden="true" />
            </span>
            <h3 id="fund-holdings-title" className="text-sm font-bold text-slate-900">
              上季持仓
            </h3>
          </div>
          {available ? (
            <p className="mt-1.5 pl-8 text-[11px] leading-4 text-slate-500">
              截至 {shortDate(distribution.as_of_date)}
              {distribution.stock_allocation_percent != null
                ? ` · 股票仓位 ${percent(distribution.stock_allocation_percent)}`
                : " · 占基金净值"}
            </p>
          ) : null}
        </div>
        {available ? (
          <span className="shrink-0 rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[10px] font-semibold text-slate-500">
            {reportLabel(distribution.report_period)}
          </span>
        ) : null}
      </div>

      {loading ? (
        <div className="mt-3 flex min-h-24 items-center justify-center rounded-xl border border-slate-100 bg-slate-50/70 text-xs text-slate-500" role="status">
          <Loader2 size={15} className="mr-2 animate-spin text-[var(--brand)]" />
          正在读取季报持仓…
        </div>
      ) : error || (distribution && !available) ? (
        <div className="mt-3 rounded-xl border border-dashed border-slate-200 bg-slate-50/60 px-3 py-3 text-center text-xs text-slate-500">
          暂未取得可核验的季度股票持仓
        </div>
      ) : available && distribution ? (
        <>
          <div className="mt-3 overflow-hidden rounded-xl border border-slate-200/90 bg-white">
            <div className="grid grid-cols-[minmax(0,1fr)_54px_62px_44px] items-center border-b border-slate-100 bg-slate-50/80 px-3 py-2 text-[10px] font-semibold text-slate-500 min-[360px]:grid-cols-[minmax(0,1fr)_62px_76px_58px]">
              <span>股票</span>
              <span className="text-right">
                <span className="min-[360px]:hidden">涨幅</span>
                <span className="hidden min-[360px]:inline">{quoteHeaderLabel}</span>
              </span>
              <span className="text-right">
                <span className="min-[360px]:hidden">{usesStockPosition ? "仓位" : "净值"}</span>
                <span className="hidden min-[360px]:inline">
                  {usesStockPosition ? "股票仓位内" : "占基金净值"}
                </span>
              </span>
              <span className="text-right">
                <span className="min-[360px]:hidden">上期</span>
                <span className="hidden min-[360px]:inline">{comparisonLabel}</span>
              </span>
            </div>
            <ol className="divide-y divide-slate-100">
              {visibleRows.map((row) => {
                const change = changePresentation(row);
                const barWidth = Math.max(0, Math.min(100, row.display_weight_percent));
                return (
                  <li key={row.security_code} className="relative px-3 py-2.5">
                    <div className="grid grid-cols-[minmax(0,1fr)_54px_62px_44px] items-center gap-0 min-[360px]:grid-cols-[minmax(0,1fr)_62px_76px_58px]">
                      <div className="flex min-w-0 items-center gap-2.5 pr-1 min-[360px]:pr-2">
                        <span className="hidden w-4 shrink-0 text-right text-[10px] font-semibold tabular-nums text-slate-300 min-[360px]:block">
                          {String(row.rank).padStart(2, "0")}
                        </span>
                        <div className="min-w-0">
                          <p className="truncate text-[13px] font-semibold text-slate-800">
                            {row.security_name || row.security_code}
                          </p>
                          <p className="mt-0.5 text-[10px] tabular-nums text-slate-400">
                            {row.security_code}
                          </p>
                        </div>
                      </div>
                      <strong
                        className={`text-right text-[11px] font-bold tabular-nums min-[360px]:text-[12px] ${quoteTone(row.quote_change_percent)}`}
                      >
                        {signedPercent(row.quote_change_percent)}
                      </strong>
                      <div className="text-right">
                        <strong className="text-[12px] font-bold tabular-nums text-slate-800 min-[360px]:text-[13px]">
                          {percent(row.display_weight_percent)}
                        </strong>
                        {usesStockPosition ? (
                          <span className="mt-0.5 block text-[9px] tabular-nums text-slate-400">
                            净值 {percent(row.nav_weight_percent)}
                          </span>
                        ) : null}
                      </div>
                      <span className={`text-right text-[10px] font-semibold tabular-nums min-[360px]:text-[11px] ${change.className}`}>
                        {change.label}
                      </span>
                    </div>
                    <div className="absolute bottom-0 left-0 h-px w-full bg-slate-50" aria-hidden="true">
                      <span
                        className="block h-full bg-[var(--brand)]/35"
                        style={{ width: `${barWidth}%` }}
                      />
                    </div>
                  </li>
                );
              })}
            </ol>
            {distribution.holdings.length > COLLAPSED_ROW_COUNT ? (
              <button
                type="button"
                onClick={() => setExpanded((current) => !current)}
                className="flex min-h-11 w-full items-center justify-center gap-1.5 border-t border-slate-100 text-xs font-semibold text-[var(--brand-strong)] transition hover:bg-slate-50"
                aria-expanded={expanded}
              >
                {expanded ? (
                  <>
                    收起持仓 <ChevronUp size={14} />
                  </>
                ) : (
                  <>
                    查看全部 {distribution.holdings.length} 只 <ChevronDown size={14} />
                  </>
                )}
              </button>
            ) : null}
          </div>
          <p className="px-1 pb-1 pt-2 text-[10px] leading-4 text-slate-400">
            {distribution.data_note} 季报有滞后，不代表当前实时持仓。
            {distribution.quote_session_date
              ? ` 涨幅为 ${shortDate(distribution.quote_session_date)}${quoteTime ? ` ${quoteTime}` : ""} 行情快照。`
              : ""}
          </p>
        </>
      ) : null}
    </section>
  );
}
