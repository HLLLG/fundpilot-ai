"use client";

import type { FundLookthroughFacts, LookthroughExposureRow } from "@/lib/api";

type ReportLookthroughPanelProps = {
  lookthrough: FundLookthroughFacts;
};

function exposureLabel(row: LookthroughExposureRow): string {
  const name = row.security_name?.trim();
  const key = row.security_key?.trim();
  if (name && key) {
    return `${name}（${key}）`;
  }
  return (
    name || key || row.industry?.trim() || row.listing_market?.trim() || "未标识"
  );
}

function percentText(value?: number | null): string | null {
  return value == null || !Number.isFinite(value) ? null : `${value}%`;
}

function ExposureRows({
  title,
  hint,
  rows,
  testId,
}: {
  title: string;
  hint: string;
  rows?: LookthroughExposureRow[];
  testId: string;
}) {
  const visible = (rows ?? []).filter(
    (row) => percentText(row.exposure_lower_bound_percent) !== null,
  );
  if (!visible.length) {
    return null;
  }
  return (
    <div className="mt-3" data-testid={testId}>
      <div className="text-xs font-black text-slate-900">{title}</div>
      <p className="mt-0.5 text-[11px] leading-4 text-slate-500">{hint}</p>
      <ul className="mt-1.5 space-y-1">
        {visible.map((row, index) => (
          <li
            key={`${exposureLabel(row)}-${index}`}
            className="flex items-baseline justify-between gap-3 rounded-lg bg-slate-50 px-2.5 py-1.5 text-xs"
          >
            <span className="min-w-0 break-words text-slate-700 [overflow-wrap:anywhere]">
              {exposureLabel(row)}
            </span>
            <span className="shrink-0 font-mono font-bold tabular-nums text-slate-900">
              ≥ {percentText(row.exposure_lower_bound_percent)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * 组合穿透重复暴露。
 *
 * 按基金市值算出来的集中度看不出「三只名字和板块标签都不同的基金其实重仓同一批
 * 股票」，这是日报唯一能暴露这类风险的地方。展示上有两条硬性纪律：
 * 全部数字都标成「≥」下界，以及未知质量必须显式写出来——否则用户会把
 * 「没列出来」当成「没有」。
 */
export function ReportLookthroughPanel({ lookthrough }: ReportLookthroughPanelProps) {
  const portfolio = lookthrough.portfolio ?? {};
  const status = String(lookthrough.status ?? "unavailable");
  const usable = status === "qualified" || status === "partial";
  const unknownMass = percentText(portfolio.unknown_account_mass_percent);
  const disclosedMass = percentText(
    portfolio.disclosed_security_mass_lower_bound_percent,
  );

  if (!usable) {
    return (
      <div
        data-testid="report-lookthrough-unavailable"
        className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-xs leading-5 text-slate-600"
      >
        本次未取得可用的基金定期报告披露数据，暂无法核对跨基金重复暴露。
      </div>
    );
  }

  return (
    <div className="min-w-0" data-testid="report-lookthrough">
      <div className="rounded-xl border border-[var(--info-border)] bg-[var(--info-bg)]/60 px-3 py-2.5 text-[11px] leading-5 text-[var(--info-fg)]">
        <p className="font-black">这些数字是披露范围内的下界，不是完整持仓</p>
        <p className="mt-0.5">
          来源为基金定期报告，存在披露滞后。
          {disclosedMass ? `已披露证券占组合约 ${disclosedMass}；` : ""}
          {unknownMass
            ? `其余约 ${unknownMass} 未披露，重合情况未知——未列出不等于没有。`
            : "未披露部分的重合情况未知——未列出不等于没有。"}
        </p>
      </div>

      <ExposureRows
        testId="report-lookthrough-securities"
        title="穿透后的证券暴露"
        hint="多只基金同时重仓同一只证券时，实际风险高于单只基金的仓位占比。"
        rows={portfolio.top_security_exposure_lower_bounds}
      />
      <ExposureRows
        testId="report-lookthrough-industries"
        title="穿透后的行业暴露"
        hint="跨基金合并后的行业集中度，可能高于按基金板块标签的估计。"
        rows={portfolio.top_industry_exposure_lower_bounds}
      />
      <ExposureRows
        testId="report-lookthrough-markets"
        title="穿透后的上市地暴露"
        hint="用于核对是否在单一市场过度集中。"
        rows={portfolio.top_listing_market_exposure_lower_bounds}
      />

      <p className="mt-3 text-[11px] leading-5 text-slate-500">
        穿透结果只用于提示重复暴露与集中度风险，不参与仓位比例计算，也不能作为买入理由。
      </p>
    </div>
  );
}
