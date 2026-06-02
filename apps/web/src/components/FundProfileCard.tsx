"use client";

import type { FundProfile } from "@/lib/api";

type FundProfileCardProps = {
  profile: FundProfile;
  onOpenDetail?: () => void;
};

function formatMoney(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `¥${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSigned(value: number | null | undefined, suffix = "") {
  if (value === null || value === undefined) {
    return "—";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${suffix}`;
}

function profitClass(value: number | null | undefined) {
  if (value === null || value === undefined || value === 0) {
    return "text-slate-600";
  }
  return value > 0 ? "text-rose-600" : "text-emerald-600";
}

export function FundProfileCard({ profile, onOpenDetail }: FundProfileCardProps) {
  const canOpen = Boolean(onOpenDetail) && profile.fund_code !== "000000";

  return (
    <article
      className={`rounded-2xl bg-white px-4 py-4 shadow-sm ring-1 ring-slate-100 ${
        canOpen ? "cursor-pointer transition hover:ring-blue-200 hover:shadow-md" : ""
      }`}
      onClick={canOpen ? onOpenDetail : undefined}
      onKeyDown={
        canOpen
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onOpenDetail?.();
              }
            }
          : undefined
      }
      role={canOpen ? "button" : undefined}
      tabIndex={canOpen ? 0 : undefined}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-black text-slate-950">{profile.fund_name}</h3>
          <p className="mt-1 text-xs text-slate-500">
            {profile.fund_code}
            {profile.is_provisional ? " · 待补全详情" : ""}
            {profile.position_percent !== null && profile.position_percent !== undefined
              ? ` · 仓位 ${profile.position_percent}%`
              : ""}
          </p>
        </div>
        <div className="text-right">
          <div className="text-xs text-slate-500">持有金额</div>
          <div className="text-sm font-black text-slate-950">{formatMoney(profile.holding_amount)}</div>
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs sm:grid-cols-3">
        <div>
          <dt className="text-slate-400">持有收益</dt>
          <dd className={`font-bold ${profitClass(profile.holding_profit)}`}>
            {formatSigned(profile.holding_profit)}
          </dd>
        </div>
        <div>
          <dt className="text-slate-400">持有收益率</dt>
          <dd className={`font-bold ${profitClass(profile.holding_return_percent)}`}>
            {formatSigned(profile.holding_return_percent, "%")}
          </dd>
        </div>
        <div>
          <dt className="text-slate-400">当日收益</dt>
          <dd className={`font-bold ${profitClass(profile.daily_profit)}`}>
            {formatSigned(profile.daily_profit)}
          </dd>
        </div>
        <div>
          <dt className="text-slate-400">持有份额</dt>
          <dd className="font-semibold text-slate-700">
            {profile.holding_shares?.toLocaleString("zh-CN") ?? "—"}
          </dd>
        </div>
        <div>
          <dt className="text-slate-400">持仓成本</dt>
          <dd className="font-semibold text-slate-700">
            {profile.holding_cost?.toLocaleString("zh-CN") ?? "—"}
          </dd>
        </div>
        <div>
          <dt className="text-slate-400">持有天数</dt>
          <dd className="font-semibold text-slate-700">
            {profile.holding_days !== null && profile.holding_days !== undefined
              ? `${profile.holding_days} 天`
              : "—"}
          </dd>
        </div>
      </dl>

      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span className="rounded-full bg-slate-100 px-2 py-0.5 font-semibold">
          {profile.sector_name || "未知板块"}
        </span>
        {profile.sector_return_percent !== null && profile.sector_return_percent !== undefined ? (
          <span className={`font-bold ${profitClass(profile.sector_return_percent)}`}>
            板块 {formatSigned(profile.sector_return_percent, "%")}
          </span>
        ) : null}
      </div>

      {canOpen ? (
        <p className="mt-3 text-xs font-bold text-blue-600">点击查看净值走势 →</p>
      ) : profile.fund_code === "000000" ? (
        <p className="mt-3 text-xs text-amber-700">补全基金代码后可查看净值走势</p>
      ) : null}
    </article>
  );
}
