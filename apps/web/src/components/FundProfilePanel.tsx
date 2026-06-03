"use client";

import { useState } from "react";
import { BookMarked, Download, FileImage, RefreshCw, Upload } from "lucide-react";
import type { FundProfile, PortfolioSummary } from "@/lib/api";
import { FundProfileCard } from "@/components/FundProfileCard";
import { FundProfileDetailModal } from "@/components/FundProfileDetailModal";
import { PortfolioSummaryCard } from "@/components/PortfolioSummaryCard";

type FundProfilePanelProps = {
  profiles: FundProfile[];
  portfolioSummary: PortfolioSummary | null;
  detailText: string;
  isBusy: boolean;
  onDetailTextChange: (value: string) => void;
  onFileSelect: (file: File) => void;
  onParseText: () => void;
  onRefresh: () => void;
  onExport: () => void;
  onImport: (file: File) => void;
};

export function FundProfilePanel({
  profiles,
  portfolioSummary,
  detailText,
  isBusy,
  onDetailTextChange,
  onFileSelect,
  onParseText,
  onRefresh,
  onExport,
  onImport,
}: FundProfilePanelProps) {
  const [selectedProfile, setSelectedProfile] = useState<FundProfile | null>(null);

  return (
    <section className="glass-panel min-w-0 rounded-[28px] p-6">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-indigo-600 text-white">
            <BookMarked size={22} />
          </div>
          <h2 className="text-xl font-black text-slate-950">基金档案库</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            上传养基宝/支付宝「账户汇总」截图，识别后会写入基金档案并同步到首页看板。
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white text-slate-500 shadow-sm transition hover:text-blue-600"
          aria-label="刷新基金档案"
        >
          <RefreshCw size={17} />
        </button>
      </div>

      <label className="group flex min-h-28 flex-col items-center justify-center rounded-[24px] border border-dashed border-indigo-300 bg-white/80 px-5 py-6 text-center transition hover:border-indigo-500 hover:bg-indigo-50/70">
        <FileImage className="mb-2 text-indigo-600" size={30} />
        <span className="text-sm font-black text-slate-950">上传单基金详情截图建档</span>
        <span className="mt-1 text-xs text-slate-500">识别基金代码、成本、份额、持仓占比和关联板块</span>
        <input
          type="file"
          accept="image/*"
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) {
              onFileSelect(file);
            }
            event.currentTarget.value = "";
          }}
        />
      </label>

      <textarea
        value={detailText}
        onChange={(event) => onDetailTextChange(event.target.value)}
        placeholder="也可以粘贴单基金详情页 OCR 文本..."
        className="mt-4 min-h-24 w-full resize-y rounded-3xl border border-slate-200 bg-white px-5 py-4 text-sm leading-6 text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-indigo-400 focus:ring-4 focus:ring-indigo-100"
      />

      <button
        type="button"
        onClick={onParseText}
        disabled={isBusy}
        className="mt-4 inline-flex w-full items-center justify-center rounded-full bg-indigo-600 px-5 py-3 text-sm font-black text-white shadow-[0_16px_36px_rgba(79,70,229,0.24)] transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:shadow-none"
      >
        {isBusy ? "正在建档..." : "从详情文本建档"}
      </button>

      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        <button
          type="button"
          onClick={onExport}
          className="inline-flex items-center justify-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2.5 text-sm font-bold text-slate-700 transition hover:border-indigo-300 hover:text-indigo-700"
        >
          <Download size={16} />
          导出档案 JSON
        </button>
        <label className="inline-flex cursor-pointer items-center justify-center gap-2 rounded-full border border-slate-200 bg-white px-4 py-2.5 text-sm font-bold text-slate-700 transition hover:border-indigo-300 hover:text-indigo-700">
          <Upload size={16} />
          导入档案 JSON
          <input
            type="file"
            accept="application/json,.json"
            className="sr-only"
            onChange={(event) => {
              const selected = event.target.files?.[0];
              if (selected) {
                onImport(selected);
              }
              event.currentTarget.value = "";
            }}
          />
        </label>
      </div>

      <div className="mt-5 space-y-4">
        <PortfolioSummaryCard summary={portfolioSummary} />

        {profiles.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-5 text-center text-sm text-slate-500">
            暂无档案。先在上方上传养基宝总览，或上传单基金详情截图建档。
          </div>
        ) : (
          <div className="space-y-3">
            {profiles.map((profile) => (
              <FundProfileCard
                key={profile.fund_code}
                profile={profile}
                onOpenDetail={() => setSelectedProfile(profile)}
              />
            ))}
          </div>
        )}
      </div>

      {selectedProfile ? (
        <FundProfileDetailModal
          profile={selectedProfile}
          onClose={() => setSelectedProfile(null)}
        />
      ) : null}
    </section>
  );
}
