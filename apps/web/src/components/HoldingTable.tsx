"use client";

import { useState } from "react";
import { AlertTriangle, History, Plus, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import type {
  Holding,
  HoldingFieldWarning,
  HoldingListDiff,
  PortfolioSummary,
  SectorQuoteMeta,
} from "@/lib/api";
import { allocatePenetrationDaily } from "@/lib/api";
import { SectorMappingModal } from "@/components/SectorMappingModal";
import {
  accountActionWarnings,
  accountInfoWarnings,
  canAllocatePenetrationDaily,
  countActionableWarnings,
  diffForRow,
  resolveDailyProfitSource,
  warningsForCell,
} from "@/lib/holdingReview";
import {
  computeEstimatedDailyReturnPercent,
  holdingDailyReturnIsEstimated,
} from "@/lib/holdingMetrics";
import {
  buildSectorRefreshNotice,
  formatSectorQuoteFetchedAt,
  isEstimateFallbackMeta,
  sectorQuoteBadgeLabel,
} from "@/lib/sectorQuoteStatus";
import { useSectorQuoteRefresh } from "@/lib/useSectorQuoteRefresh";

const PENETRATION_NOTE = "穿透拆分参考";

function sectorSourceBadge(meta?: SectorQuoteMeta) {
  if (!meta) return null;

  const label = sectorQuoteBadgeLabel(meta);
  if (!label) return null;

  const fetchedClock = formatSectorQuoteFetchedAt(meta.fetched_at);
  const fetchedSuffix = fetchedClock ? ` · ${fetchedClock}` : "";

  if (isEstimateFallbackMeta(meta)) {
    return (
      <span
        className="mt-1 block text-[10px] font-bold text-amber-700"
        title={fetchedClock ? `数据更新时间（本地）${fetchedClock}` : undefined}
      >
        {label}
        {fetchedSuffix}
      </span>
    );
  }

  if (meta.source === "live") {
    return (
      <span
        className="mt-1 block text-[10px] font-bold text-emerald-700"
        title={fetchedClock ? `数据更新时间（本地）${fetchedClock}` : undefined}
      >
        {label}
        {fetchedSuffix}
      </span>
    );
  }

  if (meta.confidence === "low") {
    return <span className="mt-1 block text-[10px] font-bold text-amber-700">{label}</span>;
  }

  return <span className="mt-1 block text-[10px] font-bold text-slate-400">{label}</span>;
}

function fieldInputClass(hasIssue: boolean, severity?: string) {
  const base =
    "w-full min-w-0 rounded-xl border px-3 py-2 text-sm tabular-nums outline-none focus:ring-4 ";
  if (!hasIssue) {
    return `${base} border-slate-200 focus:border-blue-400 focus:ring-blue-100`;
  }
  if (severity === "error") {
    return `${base} border-rose-300 bg-rose-50/80 focus:border-rose-400 focus:ring-rose-100`;
  }
  return `${base} border-amber-300 bg-amber-50/80 focus:border-amber-400 focus:ring-amber-100`;
}

function EstimatedDailyReturnCell({ holding }: { holding: Holding }) {
  const estimated = computeEstimatedDailyReturnPercent(holding);
  const isEstimated = holdingDailyReturnIsEstimated(holding);
  const fromPenetration = holding.user_note?.includes(PENETRATION_NOTE);

  if (holding.daily_return_percent != null) {
    return (
      <div
        className={`w-full rounded-xl border px-3 py-2 text-sm font-medium tabular-nums ${
          fromPenetration
            ? "border-blue-200 bg-blue-50/80 text-blue-900"
            : "border-emerald-100 bg-emerald-50/80 text-emerald-800"
        }`}
        title={
          fromPenetration
            ? "由场内穿透按板块权重拆分，可继续手动微调"
            : "已填写明确的当日收益率，不再使用估算"
        }
      >
        {holding.daily_return_percent.toFixed(2)}%
        <span
          className={`mt-0.5 block text-[10px] font-semibold ${
            fromPenetration ? "text-blue-700" : "text-emerald-600"
          }`}
        >
          {fromPenetration ? "穿透拆分" : "已填当日"}
        </span>
      </div>
    );
  }

  if (estimated == null) {
    return (
      <div className="w-full rounded-xl border border-dashed border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-400">
        —
      </div>
    );
  }

  return (
    <div
      className="w-full rounded-xl border border-amber-100 bg-amber-50/90 px-3 py-2 text-sm font-semibold tabular-nums text-amber-900"
      title="估算当日收益率 ≈ 板块涨跌 + 持有收益率（昨日结算）"
    >
      {isEstimated ? `≈${estimated.toFixed(2)}%` : `${estimated.toFixed(2)}%`}
      {isEstimated ? (
        <span className="mt-0.5 block text-[10px] font-semibold text-amber-700">板块+持有</span>
      ) : null}
    </div>
  );
}

type SectorRefreshControl = ReturnType<typeof useSectorQuoteRefresh>;

type HoldingTableProps = {
  holdings: Holding[];
  onChange: (holdings: Holding[]) => void;
  warnings?: HoldingFieldWarning[];
  onWarningsChange?: (warnings: HoldingFieldWarning[]) => void;
  diffs?: HoldingListDiff[];
  portfolioSummary?: PortfolioSummary | null;
  canApplyPreviousStructure?: boolean;
  onApplyPreviousStructure?: () => void;
  onAllocateMessage?: (message: string) => void;
  sectorRefresh?: SectorRefreshControl;
  showSectorRefreshControls?: boolean;
};

export function HoldingTable({
  holdings,
  onChange,
  warnings = [],
  onWarningsChange,
  diffs = [],
  portfolioSummary = null,
  canApplyPreviousStructure = false,
  onApplyPreviousStructure,
  onAllocateMessage,
  sectorRefresh: externalSectorRefresh,
  showSectorRefreshControls = true,
}: HoldingTableProps) {
  const [isAllocating, setIsAllocating] = useState(false);
  const internalSectorRefresh = useSectorQuoteRefresh({
    holdings,
    onChange,
    warnings,
    onWarningsChange,
    onMessage: onAllocateMessage,
  });
  const sectorRefresh = externalSectorRefresh ?? internalSectorRefresh;
  const {
    isRefreshing: isRefreshingSectors,
    sectorMetaByFundCode,
    autoRefreshEnabled,
    autoIntervalMs,
    mappingQueue,
    refresh: handleRefreshSectors,
    selectMapping: handleSelectMapping,
    dismissMapping,
    toggleAutoRefresh,
    lastRefreshResult,
  } = sectorRefresh;

  const actionableCount = countActionableWarnings(warnings);
  const accountInfos = accountInfoWarnings(warnings);
  const accountWarnings = accountActionWarnings(warnings);
  const refreshNotice = buildSectorRefreshNotice(lastRefreshResult);

  const updateHolding = (index: number, patch: Partial<Holding>) => {
    onChange(
      holdings.map((holding, itemIndex) =>
        itemIndex === index ? { ...holding, ...patch } : holding,
      ),
    );
  };

  const addHolding = () => {
    onChange([
      ...holdings,
      {
        fund_code: "000000",
        fund_name: "新基金",
        holding_amount: 0,
        return_percent: 0,
        daily_profit: null,
        daily_return_percent: null,
        holding_profit: null,
        holding_return_percent: null,
        sector_name: "",
        sector_return_percent: null,
      },
    ]);
  };

  const removeHolding = (index: number) => {
    onChange(holdings.filter((_, itemIndex) => itemIndex !== index));
  };

  const canAllocatePenetration = canAllocatePenetrationDaily(portfolioSummary, holdings);

  const handleAllocatePenetration = async () => {
    if (portfolioSummary?.daily_profit == null) {
      return;
    }
    setIsAllocating(true);
    try {
      const result = await allocatePenetrationDaily(
        holdings,
        portfolioSummary.daily_profit,
        resolveDailyProfitSource(portfolioSummary, holdings) ?? "penetration_estimate",
      );
      onChange(result.holdings);
      onWarningsChange?.(result.holding_warnings);
      onAllocateMessage?.(
        `已按板块涨跌权重拆分账户当日收益 ${result.account_daily_profit >= 0 ? "+" : ""}${result.account_daily_profit.toFixed(2)} 元（各行合计 ${result.allocated_total >= 0 ? "+" : ""}${result.allocated_total.toFixed(2)}），仅供参考，可继续手动修改。`,
      );
    } catch (error) {
      onAllocateMessage?.(
        error instanceof Error ? error.message : "拆分当日收益失败，请稍后重试。",
      );
    } finally {
      setIsAllocating(false);
    }
  };

  return (
    <section className="glass-panel min-w-0 rounded-[28px] p-6">
      <div className="mb-5 flex items-center justify-between gap-4">
        <div>
          <h2 className="text-xl font-black text-slate-950">持仓校对</h2>
          <p className="mt-1 text-sm text-slate-500">
            OCR 只负责草稿，最终以你确认的表格为准。没有“当日收益率”时，估算列 = 板块涨跌 + 持有收益率。
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {showSectorRefreshControls && holdings.length > 0 ? (
            <>
              <button
                type="button"
                onClick={() => void handleRefreshSectors(true)}
                disabled={isRefreshingSectors}
                className="inline-flex items-center gap-2 whitespace-nowrap rounded-full border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm font-bold text-emerald-900 transition hover:bg-emerald-100 disabled:opacity-60"
              >
                <RefreshCw size={16} className={isRefreshingSectors ? "animate-spin" : ""} />
                {isRefreshingSectors ? "刷新中..." : "刷新板块涨跌"}
              </button>
              <label className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600">
                <input
                  type="checkbox"
                  checked={autoRefreshEnabled}
                  onChange={(event) => toggleAutoRefresh(event.target.checked)}
                />
                自动 {Math.round(autoIntervalMs / 1000)}s
              </label>
            </>
          ) : null}
          {canAllocatePenetration ? (
            <button
              type="button"
              onClick={() => void handleAllocatePenetration()}
              disabled={isAllocating}
              className="inline-flex items-center gap-2 whitespace-nowrap rounded-full border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-bold text-blue-800 transition hover:bg-blue-100 disabled:opacity-60"
            >
              <Sparkles size={16} />
              {isAllocating ? "拆分中..." : "一键填充估算当日收益"}
            </button>
          ) : null}
          {canApplyPreviousStructure && onApplyPreviousStructure ? (
            <button
              type="button"
              onClick={onApplyPreviousStructure}
              className="inline-flex items-center gap-2 whitespace-nowrap rounded-full border border-indigo-200 bg-indigo-50 px-4 py-2 text-sm font-bold text-indigo-800 transition hover:bg-indigo-100"
            >
              <History size={16} />
              沿用上次基金列表
            </button>
          ) : null}
          <button
            type="button"
            onClick={addHolding}
            className="inline-flex items-center gap-2 whitespace-nowrap rounded-full bg-slate-950 px-4 py-2 text-sm font-bold text-white transition hover:bg-blue-700"
          >
            <Plus size={16} />
            新增
          </button>
        </div>
      </div>

      {refreshNotice ? (
        <div
          className={`mb-4 rounded-2xl border px-4 py-3 text-sm leading-6 ${
            refreshNotice.tone === "amber"
              ? "border-amber-200 bg-amber-50/90 text-amber-950"
              : refreshNotice.tone === "blue"
                ? "border-blue-200 bg-blue-50/90 text-blue-950"
                : "border-slate-200 bg-slate-50 text-slate-700"
          }`}
        >
          <div className="font-bold">{refreshNotice.title}</div>
          <div className="mt-1 text-xs opacity-90">{refreshNotice.description}</div>
        </div>
      ) : null}

      {accountInfos.length > 0 ? (
        <div className="mb-4 space-y-2 rounded-2xl border border-blue-200 bg-blue-50/90 px-4 py-3">
          {accountInfos.map((item) => (
            <p key={item.code} className="text-sm font-semibold leading-6 text-blue-950">
              {item.message}
            </p>
          ))}
          {canAllocatePenetration ? (
            <p className="text-xs leading-5 text-blue-800/90">
              可使用“一键填充估算当日收益”：按“持有金额 × 板块涨跌”权重拆分账户场内穿透收益，并自动计算各行当日收益率。
            </p>
          ) : null}
        </div>
      ) : null}

      {actionableCount > 0 || accountWarnings.length > 0 ? (
        <div className="mb-4 space-y-2 rounded-2xl border border-amber-200 bg-amber-50/90 px-4 py-3">
          <div className="flex items-start gap-2 text-sm font-bold text-amber-950">
            <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber-600" />
            <span>
              发现 {actionableCount} 处需核对（已高亮单元格），优先检查当日收益额与板块涨跌的负号。
            </span>
          </div>
          {accountWarnings.map((item) => (
            <p key={item.code} className="pl-7 text-xs font-semibold leading-5 text-amber-900">
              {item.message}
            </p>
          ))}
        </div>
      ) : null}

      <div className="max-w-full overflow-x-auto overscroll-x-contain">
        <table className="w-full min-w-[1720px] table-fixed border-separate border-spacing-y-3">
          <colgroup>
            <col className="w-[7.5rem]" />
            <col className="w-[14rem]" />
            <col className="w-[8.5rem]" />
            <col className="w-[8.5rem]" />
            <col className="w-[7.5rem]" />
            <col className="w-[9.5rem]" />
            <col className="w-[7rem]" />
            <col className="w-[8.5rem]" />
            <col className="w-[7.5rem]" />
            <col className="w-[9.5rem]" />
            <col className="w-[4.5rem]" />
          </colgroup>
          <thead>
            <tr className="text-left text-xs font-bold uppercase text-slate-400">
              <th className="px-3">基金代码</th>
              <th className="px-3">基金名称</th>
              <th className="px-3">持有金额</th>
              <th className="px-3">当日收益额</th>
              <th className="px-3">当日收益率</th>
              <th className="px-3">关联板块</th>
              <th className="px-3">板块涨跌</th>
              <th className="px-3">持有收益额</th>
              <th className="px-3">持有收益率</th>
              <th className="px-3" title="无当日收益率时 ≈ 板块涨跌 + 持有收益率">
                估算当日收益率
              </th>
              <th className="px-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {holdings.map((holding, index) => {
              const rowDiff = diffForRow(diffs, index);
              const codeWarning = warningsForCell(warnings, index, "fund_code");
              const dailyProfitWarning = warningsForCell(warnings, index, "daily_profit");
              const sectorWarning = warningsForCell(warnings, index, "sector_return_percent");

              return (
                <tr key={`${holding.fund_code}-${index}`} className="bg-white shadow-sm">
                  <td className="rounded-l-2xl px-3 py-3">
                    <input
                      value={holding.fund_code}
                      onChange={(event) => updateHolding(index, { fund_code: event.target.value })}
                      title={codeWarning?.message}
                      className={`${fieldInputClass(Boolean(codeWarning), codeWarning?.severity)} font-bold`}
                    />
                    {rowDiff && rowDiff.change_type !== "unchanged" ? (
                      <div className="mt-1 text-[10px] font-bold text-indigo-600">
                        {rowDiff.change_type === "added" ? "新增" : rowDiff.messages[0] ?? "有变动"}
                      </div>
                    ) : null}
                  </td>
                  <td className="px-3 py-3">
                    <input
                      value={holding.fund_name}
                      onChange={(event) => updateHolding(index, { fund_name: event.target.value })}
                      className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                    />
                  </td>
                  <td className="px-3 py-3">
                    <input
                      value={holding.holding_amount}
                      type="number"
                      min={0}
                      step="0.01"
                      onChange={(event) =>
                        updateHolding(index, { holding_amount: Number(event.target.value) })
                      }
                      className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm tabular-nums outline-none focus:border-blue-400"
                    />
                  </td>
                  <td className="px-3 py-3">
                    <input
                      value={holding.daily_profit ?? ""}
                      type="number"
                      step="0.01"
                      onChange={(event) =>
                        updateHolding(index, {
                          daily_profit: event.target.value === "" ? null : Number(event.target.value),
                        })
                      }
                      title={dailyProfitWarning?.message ?? "收盘前养基宝常为“--”，可以留空"}
                      className={`${fieldInputClass(Boolean(dailyProfitWarning), dailyProfitWarning?.severity)} ${
                        holding.user_note?.includes(PENETRATION_NOTE)
                          ? "border-blue-200 bg-blue-50/50"
                          : ""
                      }`}
                      placeholder={holding.daily_profit == null ? "收盘前可留空" : "如 -86.23"}
                    />
                  </td>
                  <td className="px-3 py-3">
                    <input
                      value={holding.daily_return_percent ?? ""}
                      type="number"
                      step="0.01"
                      onChange={(event) =>
                        updateHolding(index, {
                          daily_return_percent:
                            event.target.value === "" ? null : Number(event.target.value),
                        })
                      }
                      className={`w-full rounded-xl border px-3 py-2 text-sm tabular-nums outline-none focus:border-blue-400 ${
                        holding.user_note?.includes(PENETRATION_NOTE)
                          ? "border-blue-200 bg-blue-50/50"
                          : "border-slate-200"
                      }`}
                      placeholder={holding.daily_return_percent == null ? "收盘前可留空" : "如 -0.57"}
                    />
                  </td>
                  <td className="px-3 py-3">
                    <input
                      value={holding.sector_name ?? ""}
                      onChange={(event) => updateHolding(index, { sector_name: event.target.value })}
                      className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-blue-400"
                      placeholder="如 半导体"
                    />
                  </td>
                  <td className="px-3 py-3">
                    <input
                      value={holding.sector_return_percent ?? ""}
                      type="number"
                      step="0.01"
                      onChange={(event) =>
                        updateHolding(index, {
                          sector_return_percent:
                            event.target.value === "" ? null : Number(event.target.value),
                        })
                      }
                      title={
                        sectorWarning?.message ??
                        sectorMetaByFundCode[holding.fund_code]?.message ??
                        undefined
                      }
                      className={fieldInputClass(Boolean(sectorWarning), sectorWarning?.severity)}
                      placeholder="如 2.87"
                    />
                    {sectorSourceBadge(sectorMetaByFundCode[holding.fund_code])}
                  </td>
                  <td className="px-3 py-3">
                    <input
                      value={holding.holding_profit ?? ""}
                      type="number"
                      step="0.01"
                      onChange={(event) =>
                        updateHolding(index, {
                          holding_profit:
                            event.target.value === "" ? null : Number(event.target.value),
                        })
                      }
                      className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm tabular-nums outline-none focus:border-blue-400"
                      placeholder="如 -260.85"
                    />
                  </td>
                  <td className="px-3 py-3">
                    <input
                      value={holding.holding_return_percent ?? holding.return_percent}
                      type="number"
                      step="0.01"
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        updateHolding(index, {
                          holding_return_percent: value,
                          return_percent: value,
                        });
                      }}
                      className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm tabular-nums outline-none focus:border-blue-400"
                      placeholder="如 -3.14"
                    />
                  </td>
                  <td className="px-3 py-3">
                    <EstimatedDailyReturnCell holding={holding} />
                  </td>
                  <td className="rounded-r-2xl px-3 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => removeHolding(index)}
                      className="inline-flex h-10 w-10 items-center justify-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                      aria-label="删除持仓"
                    >
                      <Trash2 size={18} />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {holdings.length === 0 ? (
        <div className="rounded-3xl border border-dashed border-slate-200 bg-white px-5 py-8 text-center text-sm text-slate-500">
          暂无持仓。上传截图、粘贴 OCR 文本，或手动新增一行。
        </div>
      ) : null}

      {!externalSectorRefresh ? (
        <SectorMappingModal
          open={mappingQueue.length > 0}
          fundName={mappingQueue[0]?.fundName ?? ""}
          sectorName={mappingQueue[0]?.sectorName}
          candidates={mappingQueue[0]?.candidates ?? []}
          onClose={dismissMapping}
          onSelect={(candidate) => void handleSelectMapping(candidate)}
        />
      ) : null}
    </section>
  );
}
