"use client";

import { useEffect, useState } from "react";
import { LineChart, X } from "lucide-react";
import type { FundNavHistory, FundProfile } from "@/lib/api";
import { fetchFundNavHistory } from "@/lib/api";
import { FundProfileCard } from "@/components/FundProfileCard";
import { NavLineChart } from "@/components/NavLineChart";

const PERIODS = [
  { label: "近1月", days: 22 },
  { label: "近3月", days: 66 },
  { label: "近6月", days: 132 },
  { label: "近1年", days: 252 },
] as const;

type FundProfileDetailModalProps = {
  profile: FundProfile;
  onClose: () => void;
};

export function FundProfileDetailModal({ profile, onClose }: FundProfileDetailModalProps) {
  const [days, setDays] = useState(66);
  const [history, setHistory] = useState<FundNavHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchFundNavHistory(profile.fund_code, days)
      .then((data) => {
        if (!cancelled) {
          setHistory(data);
        }
      })
      .catch((loadError) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "加载净值走势失败");
          setHistory(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [profile.fund_code, days]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/50 p-0 sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="fund-detail-title"
      onClick={onClose}
    >
      <div
        className="flex max-h-[94vh] w-full max-w-lg flex-col overflow-hidden rounded-t-[28px] bg-slate-100 shadow-2xl sm:max-w-2xl sm:rounded-[28px]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between gap-3 bg-white px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <LineChart size={18} className="shrink-0 text-blue-600" />
              <h2 id="fund-detail-title" className="truncate text-base font-black text-slate-950">
                {profile.fund_name}
              </h2>
            </div>
            <p className="mt-0.5 text-xs text-slate-500">{profile.fund_code}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-slate-100 text-slate-500 transition hover:bg-slate-200 hover:text-slate-900"
            aria-label="关闭"
          >
            <X size={20} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="space-y-3 p-4">
            <FundProfileCard profile={profile} />

            <section className="overflow-hidden rounded-[24px] bg-white shadow-sm ring-1 ring-slate-100">
              <div className="border-b border-slate-100 px-4 pt-4 pb-3">
                <h3 className="text-sm font-black text-slate-950">净值走势</h3>
                <p className="mt-0.5 text-[11px] text-slate-400">公开单位净值 · 东方财富</p>

                <div
                  className="mt-3 grid grid-cols-4 gap-1 rounded-xl bg-slate-100 p-1"
                  role="tablist"
                  aria-label="时间范围"
                >
                  {PERIODS.map((period) => {
                    const active = days === period.days;
                    return (
                      <button
                        key={period.label}
                        type="button"
                        role="tab"
                        aria-selected={active}
                        onClick={() => setDays(period.days)}
                        className={`rounded-lg py-2 text-xs font-bold transition ${
                          active
                            ? "bg-white text-blue-700 shadow-sm ring-1 ring-slate-200/80"
                            : "text-slate-500 hover:text-slate-800"
                        }`}
                      >
                        {period.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="p-3 sm:p-4">
                {loading ? (
                  <div className="space-y-3 animate-pulse">
                    <div className="h-16 rounded-2xl bg-slate-100" />
                    <div className="h-[240px] rounded-2xl bg-slate-100" />
                  </div>
                ) : error ? (
                  <div className="rounded-2xl border border-rose-100 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                    {error}
                  </div>
                ) : history?.note && !history.points.length ? (
                  <div className="rounded-2xl border border-amber-100 bg-amber-50 px-4 py-3 text-sm text-amber-900">
                    {history.note}
                  </div>
                ) : (
                  <NavLineChart
                    points={history?.points ?? []}
                    periodChangePercent={history?.period_change_percent}
                    height={248}
                  />
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
