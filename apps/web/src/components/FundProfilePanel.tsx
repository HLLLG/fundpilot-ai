"use client";

import { BookMarked, FileImage, RefreshCw } from "lucide-react";
import type { FundProfile } from "@/lib/api";

type FundProfilePanelProps = {
  profiles: FundProfile[];
  detailText: string;
  isBusy: boolean;
  onDetailTextChange: (value: string) => void;
  onFileSelect: (file: File) => void;
  onParseText: () => void;
  onRefresh: () => void;
};

export function FundProfilePanel({
  profiles,
  detailText,
  isBusy,
  onDetailTextChange,
  onFileSelect,
  onParseText,
  onRefresh,
}: FundProfilePanelProps) {
  return (
    <section className="glass-panel min-w-0 rounded-[28px] p-6">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="mb-3 inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-indigo-600 text-white">
            <BookMarked size={22} />
          </div>
          <h2 className="text-xl font-black text-slate-950">基金档案库</h2>
          <p className="mt-2 text-sm leading-6 text-slate-600">
            单基金详情截图只需要建档一次。之后总览截图里的简称会自动匹配完整名称和基金代码。
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

      <div className="mt-5 space-y-3">
        {profiles.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-5 text-center text-sm text-slate-500">
            暂无档案。先上传一张单基金详情截图。
          </div>
        ) : null}
        {profiles.map((profile) => (
          <div key={profile.fund_code} className="rounded-2xl bg-white px-4 py-3 shadow-sm">
            <div className="text-sm font-black text-slate-950">{profile.fund_name}</div>
            <div className="mt-1 text-xs text-slate-500">
              {profile.fund_code}
              {profile.position_percent ? ` · 仓位 ${profile.position_percent}%` : ""}
              {profile.holding_cost ? ` · 成本 ${profile.holding_cost}` : ""}
            </div>
            <div className="mt-2 text-xs text-slate-500">
              {profile.sector_name || "未知板块"}
              {profile.sector_return_percent !== null && profile.sector_return_percent !== undefined
                ? ` · ${profile.sector_return_percent}%`
                : ""}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
